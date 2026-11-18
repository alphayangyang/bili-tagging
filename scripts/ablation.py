"""Ablation harness: rule baseline vs local / cloud LLM taggers.

Compares backends on the same set and reports dominant-label agreement, macro
F1, relevance-vector MAE, tag-level Spearman, and latency percentiles.

Usage:
    DM_LLM_BASE_URL=http://10.211.24.105:11435/v1 DM_LLM_MODEL=qwen3:4b \
        python3 scripts/ablation.py --set /tmp/ablation/teacher.jsonl \
        --backends rules,llm --batch 8 --out /tmp/ablation/teacher_qwen3_4b.json

Rows: {"text": ..., "label": <dominant>, "intensity": <0-1>, "scores": {tag: 0-1}}
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danmaku_filter.classify import get_classifier  # noqa: E402
from danmaku_filter.metrics import prf  # noqa: E402
from danmaku_filter.taxonomy import CATEGORY_IDS, FALLBACK_ID  # noqa: E402
from danmaku_filter.types import Danmaku  # noqa: E402

ALL_LABELS = list(CATEGORY_IDS) + [FALLBACK_ID]


def read_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def spearman(pairs: list[tuple[float, float]]) -> float:
    n = len(pairs)
    if n < 3:
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = rank
            i = j + 1
        return out

    xs = ranks([p[0] for p in pairs])
    ys = ranks([p[1] for p in pairs])
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys))
    return num / den if den else 0.0


def score_backend(rows: list[dict], predictions: list[dict], elapsed: float) -> dict:
    golds = [row["label"] for row in rows]
    pred_labels = [pred["label"] for pred in predictions]
    report = prf(
        [{"text": row["text"], "label": row["label"]} for row in rows],
        [{"text": row["text"], "label": pred["label"]} for row, pred in zip(rows, predictions)],
        ALL_LABELS,
    )
    agreement = sum(1 for g, p in zip(golds, pred_labels) if g == p) / len(rows) if rows else 0.0
    abs_err = [abs(row["intensity"] - pred["confidence"]) for row, pred in zip(rows, predictions)]

    tag_pairs: list[tuple[float, float]] = []
    tag_abs: list[float] = []
    for row, pred in zip(rows, predictions):
        for tag, gold_score in (row.get("scores") or {}).items():
            pred_score = float(pred.get("tags", {}).get(tag, 0.0))
            tag_pairs.append((float(gold_score), pred_score))
            tag_abs.append(abs(float(gold_score) - pred_score))

    per_item = [elapsed / len(rows)] * len(rows) if rows else []
    report.update(
        {
            "n": len(rows),
            "dominant_agreement": round(agreement, 4),
            "argmax_score_mae": round(sum(abs_err) / len(abs_err), 4) if abs_err else 0.0,
            "tag_mae": round(sum(tag_abs) / len(tag_abs), 4) if tag_abs else 0.0,
            "tag_spearman": round(spearman(tag_pairs), 4) if tag_pairs else 0.0,
            "total_seconds": round(elapsed, 2),
            "mean_ms_per_item": round(1000 * elapsed / len(rows), 1) if rows else 0.0,
            "p50_ms": round(1000 * percentile(per_item, 0.5), 1),
            "p95_ms": round(1000 * percentile(per_item, 0.95), 1),
            "pred_distribution": distribution(pred_labels),
        }
    )
    return report


def distribution(labels: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for label in labels:
        out[label] = out.get(label, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def run_backend(name: str, rows: list[dict], batch: int, context: str) -> dict:
    items = [Danmaku(text=row["text"], time_ms=0) for row in rows]
    kwargs = {"video_context": context, "batch_size": batch} if name == "llm" else {}
    classifier = get_classifier(name, **kwargs)
    started = time.perf_counter()
    results = classifier.classify_batch(items)
    elapsed = time.perf_counter() - started
    predictions = [
        {"label": result.label, "confidence": result.confidence, "tags": dict(result.tags or {}), "note": result.rationale}
        for result in results
    ]
    report = score_backend(rows, predictions, elapsed)
    report["backend"] = name
    if name == "llm":
        report["model"] = os.environ.get("DM_LLM_MODEL", "")
        report["base_url"] = os.environ.get("DM_LLM_BASE_URL", "")
    return {"report": report, "predictions": predictions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", required=True, dest="dataset")
    parser.add_argument("--backends", default="rules,llm")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--context", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    rows = read_rows(Path(args.dataset))
    print(f"set {args.dataset}: {len(rows)} rows")
    merged = {}
    for backend in [name.strip() for name in args.backends.split(",") if name.strip()]:
        try:
            outcome = run_backend(backend, rows, args.batch, args.context)
        except Exception as exc:
            print(f"- {backend}: FAILED ({type(exc).__name__}: {exc})")
            continue
        report = outcome["report"]
        merged[backend] = report
        print(
            f"- {backend:5s} model={report.get('model', 'rules'):20s} agree={report['dominant_agreement']:.3f} "
            f"macro_f1={report['macro_f1']:.3f} tag_mae={report['tag_mae']:.3f} "
            f"spearman={report['tag_spearman']:.3f} mean={report['mean_ms_per_item']:.0f}ms total={report['total_seconds']:.1f}s"
        )
        print(f"  pred: {report['pred_distribution']}")
    if args.out:
        Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
