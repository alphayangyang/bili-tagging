"""Benchmark labelers (rules vs LLM) on a gold set.

Usage:
    python3 scripts/bench_labeler.py --gold examples/gold_example.jsonl
    DM_LLM_BASE_URL=http://10.211.24.105:11435/v1 DM_LLM_MODEL=lfm2:latest \
        python3 scripts/bench_labeler.py --gold data/annotation/pilot.jsonl --backends rules,llm

Gold rows: {"text": "...", "label": "<taxonomy id>"}. Reports accuracy, macro F1,
per-item latency and writes predictions plus a JSON report next to the gold set
(or under --out-dir). This is the script the paper's RQ2 evaluation will use once
the human-annotated pilot set exists.
"""
from __future__ import annotations

import argparse
import json
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


def read_jsonl(path: Path) -> list[dict]:
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


def run_backend(name: str, items: list[Danmaku], labels: list[str], batch: int, context: str) -> dict:
    kwargs = {"video_context": context} if name == "llm" else {}
    classifier = get_classifier(name, **kwargs)
    started = time.perf_counter()
    results = classifier.classify_batch(items)
    elapsed = time.perf_counter() - started
    per_item = [elapsed / len(items)] * len(items) if items else []
    predictions = [
        {
            "text": dm.text,
            "label": result.label,
            "confidence": result.confidence,
            "rationale": result.rationale,
            "source": result.source,
        }
        for dm, result in zip(items, results)
    ]
    report = prf(
        [{"text": item.text, "label": gold} for item, gold in zip(items, labels)],
        predictions,
        CATEGORY_IDS + [FALLBACK_ID],
    )
    report.update(
        {
            "backend": name,
            "model": os.environ.get("DM_LLM_MODEL", "") if name == "llm" else "rules",
            "total_seconds": round(elapsed, 2),
            "mean_ms_per_item": round(1000 * elapsed / len(items), 1) if items else 0.0,
            "p50_ms": round(1000 * percentile(per_item, 0.5), 1),
            "p95_ms": round(1000 * percentile(per_item, 0.95), 1),
        }
    )
    return {"report": report, "predictions": predictions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--backends", default="rules,llm")
    parser.add_argument("--batch", type=int, default=8, help="LLM batch size")
    parser.add_argument("--context", default="", help="video title or description for the LLM prompt")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args(argv)

    gold_path = Path(args.gold)
    rows = read_jsonl(gold_path)
    golds = [str(row["label"]) for row in rows]
    items = [Danmaku(text=str(row["text"]), time_ms=int(row.get("time_ms", 0))) for row in rows]
    out_dir = Path(args.out_dir) if args.out_dir else gold_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"gold: {gold_path} ({len(rows)} rows) -> {out_dir}")

    for backend in [name.strip() for name in args.backends.split(",") if name.strip()]:
        try:
            outcome = run_backend(backend, items, golds, args.batch, args.context)
        except Exception as exc:  # keep the other backend's numbers
            print(f"- {backend}: FAILED ({type(exc).__name__}: {exc})")
            continue
        report = outcome["report"]
        pred_path = out_dir / f"pred_{backend}.jsonl"
        with pred_path.open("w", encoding="utf-8") as handle:
            for row in outcome["predictions"]:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        report_path = out_dir / f"bench_{backend}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"- {backend:5s} acc={report['accuracy']:.3f} macro_f1={report['macro_f1']:.3f} "
            f"mean={report['mean_ms_per_item']:.0f}ms/item total={report['total_seconds']:.1f}s "
            f"-> {pred_path.name}, {report_path.name}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
