"""Export a stratified pilot annotation sample plus two blank coder sheets.

Usage:
    python3 scripts/export_pilot_sample.py --bvid BV1GJ411x7h7 --n 120

The sheets deliberately leave the label and note columns empty and do not show
the cached model label, so the two human coders stay blind to the classifier.
The JSONL keeps the cached label for later model-vs-human comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danmaku_filter import annotation, cache  # noqa: E402
from danmaku_filter.taxonomy import CATEGORY_IDS, FALLBACK_ID  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bvid", default="BV1GJ411x7h7")
    parser.add_argument("--tags-dir", default="data/tags")
    parser.add_argument("--n", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out-dir", default="data/annotation")
    args = parser.parse_args(argv)

    payload = cache.load_tags(args.bvid, args.tags_dir)
    if not payload:
        raise SystemExit(f"no tag cache for {args.bvid} under {args.tags_dir}")
    items = annotation.load_items(payload)
    picked = annotation.stratified_sample(items, n=args.n, seed=args.seed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "item_id": dmid,
            "text": str(item.get("text", "")),
            "time_ms": int(item.get("time_ms", 0) or 0),
            "current_label": str(item.get("dominant", "") or FALLBACK_ID),
        }
        for dmid, item in picked
    ]
    sample_path = out_dir / f"sample_{len(rows)}.jsonl"
    with sample_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    for coder in ("coder_a", "coder_b"):
        annotation.write_coder_sheet(out_dir / f"{coder}.csv", rows)

    distribution: dict[str, int] = {}
    for row in rows:
        distribution[row["current_label"]] = distribution.get(row["current_label"], 0) + 1
    print(json.dumps({
        "bvid": args.bvid,
        "n": len(rows),
        "seed": args.seed,
        "sample": str(sample_path),
        "sheets": [str(out_dir / "coder_a.csv"), str(out_dir / "coder_b.csv")],
        "model_labels": dict(sorted(distribution.items())),
        "labels": list(CATEGORY_IDS) + [FALLBACK_ID],
    }, ensure_ascii=False, indent=2))
    print("Next: each coder fills the label column in their own CSV, then run scripts/cohen_kappa.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
