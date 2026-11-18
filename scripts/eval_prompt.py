"""Prompt ablation: v2 (flat scoring) versus v3 (decision order + anchors).

Datasets
  audit     29 comments with human labels (data/pilot/audit_gold.json)
  indomain 131 held-out comments from BV1GJ411x7h7, gold = deepseek-flash labels
  boundary 46 minimal-pair probes from danmaku_filter/boundary.py

Usage
  PYTHONPATH=. python3 scripts/eval_prompt.py --backend deepseek --styles v2,v3
  PYTHONPATH=. python3 scripts/eval_prompt.py --backend qwen35-local --styles v2,v3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))
import common as C  # noqa: E402

from danmaku_filter import boundary  # noqa: E402

RESULTS = ROOT / "data" / "experiments" / "results"


def sets() -> dict[str, list[dict]]:
    audit = C.human_gold()
    data = C.load_dataset()
    rows = data["BV1GJ411x7h7"]
    gold_texts = {r["text"] for r in audit}
    holdout_pool = [r for r in rows if r["text"] not in gold_texts]
    indomain = C.stratified_sample(holdout_pool, 131, seed=20261104)
    probes = []
    for row in boundary.probe_items():
        probes.append(
            {
                "bvid": "boundary",
                "text": row["text"],
                "dominant": row["expect_dominant"],
                "tags": {},
                "must": row["must"],
                "focus": row["focus"],
                "id": row["id"],
            }
        )
    return {"audit": audit, "indomain": indomain, "boundary": probes}


def per_label_recall(golds: list[str], preds: list[str]) -> dict[str, float]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for g, p in zip(golds, preds):
        buckets[g].append(1 if g == p else 0)
    return {g: round(sum(v) / len(v), 4) for g, v in sorted(buckets.items())}


def confusion(golds: list[str], preds: list[str], top: int = 8) -> list[dict]:
    pairs = Counter((g, p) for g, p in zip(golds, preds) if g != p)
    totals = Counter(golds)
    out = []
    for (g, p), n in pairs.most_common(top):
        out.append({"gold": g, "pred": p, "n": n, "share_of_label": round(n / totals[g], 3)})
    return out


def summarize(name: str, rows: list[dict], preds: list[dict], elapsed_s: float) -> dict:
    golds = [r.get("dominant") or r.get("label") for r in rows]
    preds_l = [p["label"] for p in preds]
    result = {
        "set": name,
        "n": len(rows),
        "agreement": round(C.agreement(golds, preds_l), 4),
        "macro_f1": round(C.macro_f1(golds, preds_l), 4),
        "per_label_recall": per_label_recall(golds, preds_l),
        "confusion": confusion(golds, preds_l),
        "pred_dist": dict(Counter(preds_l)),
        "elapsed_s": round(elapsed_s, 1),
        "ms_per_item": round(1000.0 * elapsed_s / max(1, len(rows)), 1),
    }
    if name == "indomain":
        info = [i for i, r in enumerate(rows) if golds[i] == "information"]
        leak = sum(1 for i in info if preds_l[i] == "humor")
        result["information_to_humor_leak"] = round(leak / max(1, len(info)), 3)
    if name == "boundary":
        hits = [1 if g == p else 0 for g, p in zip(golds, preds_l)]
        result["dominant_accuracy"] = round(sum(hits) / max(1, len(hits)), 4)
        must_hits, must_total = 0, 0
        by_focus: dict[str, list[int]] = defaultdict(list)
        failures = []
        for row, pred in zip(rows, preds):
            ok = True
            for tag, floor in row.get("must", {}).items():
                must_total += 1
                if float(pred.get("tags", {}).get(tag, 0.0)) >= floor:
                    must_hits += 1
                else:
                    ok = False
            by_focus[row["focus"]].append(1 if ok else 0)
            if not ok or pred["label"] != row["dominant"]:
                failures.append(
                    {
                        "id": row["id"],
                        "text": row["text"],
                        "expect": row["dominant"],
                        "got": pred["label"],
                        "tags": {k: round(v, 2) for k, v in sorted(pred.get("tags", {}).items())},
                    }
                )
        result["must_hit_rate"] = round(must_hits / max(1, must_total), 4)
        result["by_focus"] = {
            k: round(sum(v) / len(v), 3) for k, v in sorted(by_focus.items())
        }
        result["failures"] = failures
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["deepseek", "qwen35-local"], default="deepseek")
    ap.add_argument("--styles", default="v2,v3")
    ap.add_argument("--sets", default="audit,indomain,boundary")
    args = ap.parse_args()

    wanted = sets()
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    chosen = [s.strip() for s in args.sets.split(",") if s.strip()]
    out = RESULTS / ("e8_%s.json" % args.backend)
    combined: dict = {"backend": args.backend, "runs": {}}
    if out.exists():
        combined = json.loads(out.read_text(encoding="utf-8"))
        combined.setdefault("runs", {})
    for style in styles:
        for name in chosen:
            rows = wanted[name]
            tag = "prompt%s-%s" % (style, name)
            started = time.time()
            os.environ["DM_LLM_PROMPT"] = style
            preds = C.predict_llm(rows, args.backend, tag, context="Bilibili danmaku")
            elapsed = time.time() - started
            summary = summarize(name, rows, preds, elapsed)
            summary["style"] = style
            combined["runs"]["%s/%s" % (style, name)] = summary
            print(
                "[%s/%s] %s n=%d agree=%.4f f1=%.4f ms=%.0f"
                % (args.backend, style, name, summary["n"], summary["agreement"],
                   summary["macro_f1"], summary["ms_per_item"]),
                flush=True,
            )
    out.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
