"""E1: decision-maker matrix across label sets (human gold, in-domain hold-out, new videos)."""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import common as C

SETS = ("human_gold", "in_domain", "new_video")


def build_sets() -> dict:
    data = C.load_dataset()
    gold = C.human_gold()
    a_video = gold[0]["bvid"] if gold else "BV1GJ411x7h7"
    a_rows = data[a_video]
    holdout = C.stratified_sample(a_rows, 131, seed=20261104)
    holdout_texts = {r["text"] for r in holdout}
    train_a = [r for r in a_rows if r["text"] not in holdout_texts]
    others = {bvid: rows for bvid, rows in data.items() if bvid != a_video}
    new_videos = {bvid: C.stratified_sample(rows, 100, seed=20261105) for bvid, rows in others.items()}
    return {"data": data, "gold": gold, "a_video": a_video, "train_a": train_a,
            "in_domain": holdout, "new_videos": new_videos, "others": others}


def run(stage: str) -> None:
    ctx = build_sets()
    data = ctx["data"]
    results: dict = {"sets": {}, "config": {"stage": stage}}

    def safe(set_name: str, backend: str, rows: list[dict], train, fn, elapsed_fn=None) -> None:
        started = time.time()
        try:
            preds = fn()
        except Exception as exc:
            results["sets"].setdefault(set_name, {})[backend] = {"n": len(rows), "error": str(exc)[:300]}
            print(f"{set_name:12s} {backend:16s} ERROR {exc}"[:300], flush=True)
            (C.RESULTS / "matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        record(set_name, backend, rows, preds, (time.time() - started) * 1000)

    def record(set_name: str, backend: str, rows: list[dict], preds: list[dict], elapsed: float) -> None:
        results["sets"].setdefault(set_name, {})[backend] = C.evaluate(rows, preds, elapsed)
        print(f"{set_name:12s} {backend:16s} " + json.dumps(results["sets"][set_name][backend], ensure_ascii=False), flush=True)
        (C.RESULTS / "matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_local(rows: list[dict], train: list[dict], set_name: str, tag: str) -> None:
        started = time.time(); record(set_name, "rules", rows, C.predict_rules(rows), (time.time() - started) * 1000)
        for kind in ("centroid", "softmax"):
            started = time.time()
            record(set_name, f"linear_{kind}", rows, C.predict_linear(train, rows, kind), (time.time() - started) * 1000)
        started = time.time(); record(set_name, "tfidf_svm", rows, C.predict_sklearn(train, rows), (time.time() - started) * 1000)
        if stage in ("local", "all"):
            started = time.time(); record(set_name, "bge_anchor", rows, C.predict_bge_anchor(rows), (time.time() - started) * 1000)

    # human gold (29): train on the rest of video A
    gold = ctx["gold"]
    run_local(gold, ctx["train_a"], "human_gold", "gold")
    if stage in ("llm", "all"):
        started = time.time()
        record("human_gold", "deepseek_flash", gold, C.predict_llm(gold, "deepseek", "gold"), (time.time() - started) * 1000)
    if stage in ("local", "llm", "all"):
        safe("human_gold", "qwen35_4b", gold, ctx["train_a"], lambda: C.predict_llm(gold, "qwen35-local", "gold"))

    # in-domain hold-out on video A (131)
    run_local(ctx["in_domain"], ctx["train_a"], "in_domain", "A")
    if stage in ("local", "llm", "all"):
        safe("in_domain", "qwen35_4b", ctx["in_domain"], ctx["train_a"],
             lambda: C.predict_llm(ctx["in_domain"], "qwen35-local", "A"))

    # new videos: leave-one-video-out training (all other videos except the test one and A? use all others)
    for bvid, rows in ctx["new_videos"].items():
        train = [r for other_bvid, other_rows in data.items() if other_bvid != bvid for r in other_rows]
        run_local(rows, train, f"new:{bvid}", bvid)
        if stage in ("local", "llm", "all"):
            safe(f"new:{bvid}", "qwen35_4b", rows, train, lambda: C.predict_llm(rows, "qwen35-local", bvid))

    (C.RESULTS / "matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", C.RESULTS / "matrix.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("local", "llm", "all"), default="local")
    args = parser.parse_args()
    run(args.stage)
