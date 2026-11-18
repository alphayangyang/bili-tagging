"""Compute inter-coder agreement (Cohen kappa) for the pilot annotation sheets.

Usage:
    python3 scripts/cohen_kappa.py --a data/annotation/coder_a.csv \
        --b data/annotation/coder_b.csv

Both CSVs must have item_id and label columns (produced by
scripts/export_pilot_sample.py). Blank labels are skipped, so an unfinished
coder is fine for an early check; the report says how many items each coder did.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danmaku_filter import annotation  # noqa: E402


def _summary(report: dict, labels_a: dict[str, str], labels_b: dict[str, str], rows: list[dict]) -> dict:
    kappa = report["kappa"]
    if kappa is None:
        verdict = "no overlapping labels yet"
    elif kappa >= 0.8:
        verdict = "good agreement - freeze the coding guide"
    elif kappa >= 0.7:
        verdict = "acceptable for the pilot - adjudicate the disagreements"
    else:
        verdict = "below 0.7 - revise category definitions and recode"
    return {
        "coder_a_labeled": len(labels_a),
        "coder_b_labeled": len(labels_b),
        "overlap": report["n"],
        "observed_agreement": report["observed"],
        "expected_agreement": report["expected"],
        "cohen_kappa": kappa,
        "verdict": verdict,
        "per_label": report["per_label"],
        "confusion": report["confusion"],
        "disagreements": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--out", default="data/annotation/kappa_report.json")
    args = parser.parse_args(argv)

    labels_a = annotation.read_labels(args.a)
    labels_b = annotation.read_labels(args.b)
    invalid = annotation.validate_labels(list(labels_a.values()) + list(labels_b.values()))
    pairs = annotation.coded_pairs(labels_a, labels_b)
    report = annotation.cohen_kappa(pairs)
    rows = annotation.disagreements(labels_a, labels_b)
    summary = _summary(report, labels_a, labels_b, rows)
    summary["invalid_labels"] = invalid

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if invalid:
        print(f"WARNING: labels outside the taxonomy: {invalid}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
