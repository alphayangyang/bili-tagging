"""Distillation ablation: does the v3 decision order survive into the L1 student?

The deployed path is not the teacher but the per-video linear student trained on
teacher labels. If a prompt fix only improves the teacher, users never see it.
This script trains the student on old (v2) labels and on fresh v3 labels and
evaluates both on the human-audited 29 comments and the minimal-pair probes.

Usage
  PYTHONPATH=. python3 scripts/eval_distill.py --backend deepseek --rows 400
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))
import common as C  # noqa: E402
import eval_prompt as EP  # noqa: E402

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

RESULTS = ROOT / "data" / "experiments" / "results"


def boundary_scores(rows: list[dict], labels: list[str]) -> dict:
    """Dominant accuracy on probes, overall and on labels the student knows."""
    covered = set(labels)
    truth = [r.get("dominant") or r.get("label") for r in rows]
    hits = [1 if t == p else 0 for t, p in zip(truth, labels)]
    subset = [h for t, h in zip(truth, hits) if t in covered]
    must_hits, must_total = 0, 0
    for row, pred in zip(rows, labels):
        must = row.get("must") or []
        if (row.get("dominant") or row.get("label")) in covered and must:
            must_total += 1
            if pred in must:
                must_hits += 1
    return {
        "n": len(rows),
        "dominant_accuracy": round(sum(hits) / max(1, len(hits)), 4),
        "covered_n": len(subset),
        "covered_accuracy": round(sum(subset) / max(1, len(subset)), 4) if subset else None,
        "must_hit_rate": round(must_hits / must_total, 4) if must_total else None,
    }


def run_variant(name: str, texts: list[str], labels: list[str], bundle: dict) -> dict:
    out = {"variant": name, "rows": len(texts), "distribution": dict(Counter(labels).most_common())}
    for kind in ("centroid", "softmax"):
        started = time.time()
        tagger = LinearTagger(kind=kind).train(texts, labels)
        fit_s = time.time() - started
        entry = {"fit_s": round(fit_s, 3), "labels": len(tagger.labels)}
        audit_rows = bundle["audit"]
        audit_pred = tagger.predict([r["text"] for r in audit_rows])
        golds = [r.get("dominant") or r.get("label") for r in audit_rows]
        entry["audit"] = {
            "n": len(golds),
            "agreement": round(sum(1 for g, p in zip(golds, audit_pred) if g == p) / len(golds), 4),
            "per_label_recall": EP.per_label_recall(golds, audit_pred),
        }
        probes = bundle["boundary"]
        probe_pred = tagger.predict([r["text"] for r in probes])
        entry["boundary"] = boundary_scores(probes, probe_pred)
        out[kind] = entry
        print("[%s/%s] audit %s boundary %s" % (name, kind, entry["audit"]["agreement"],
                                                entry["boundary"]["covered_accuracy"]), flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="deepseek")
    parser.add_argument("--rows", type=int, default=400)
    parser.add_argument("--style", default="v4")
    parser.add_argument("--tag", default="")
    parser.add_argument("--out", default=str(RESULTS / "distill_prompt.json"))
    args = parser.parse_args()

    bundle = EP.sets()
    data = C.load_dataset()
    video_rows = data["BV1GJ411x7h7"]
    holdout = {r["text"] for r in bundle["audit"]} | {r["text"] for r in bundle["boundary"]}
    trainable = [r for r in video_rows if r["text"] not in holdout]
    sample = C.stratified_sample(trainable, min(args.rows, len(trainable)), seed=20261105)

    os.environ["DM_LLM_PROMPT"] = args.style
    started = time.time()
    tag = args.tag or "prompt%s-%sa" % (args.style, len(sample))
    preds = C.predict_llm(sample, args.backend, tag, context="Bilibili danmaku")
    relabel_s = time.time() - started

    v2_texts = [r["text"] for r in trainable]
    v2_labels = [r["dominant"] for r in trainable]
    v3_texts = [r["text"] for r in sample]
    v3_labels = [p["label"] for p in preds]

    combined = {
        "backend": args.backend,
        "relabel_seconds": round(relabel_s, 1),
        "sample_n": len(sample),
        "sample_v2_distribution": dict(Counter(r["dominant"] for r in sample).most_common()),
        "sample_v3_distribution": dict(Counter(v3_labels).most_common()),
        "variants": [
            run_variant("student_on_v2_%d" % len(trainable), v2_texts, v2_labels, bundle),
            run_variant("student_on_v2_%d" % len(sample),
                        [r["text"] for r in sample],
                        [r["dominant"] for r in sample], bundle),
            run_variant("student_on_%s_%d" % (args.style, len(sample)), v3_texts, v3_labels, bundle),
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(combined, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
