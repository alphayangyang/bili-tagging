"""Build the two ablation label sets.

1. human  - the 29-comment pilot audit with human labels (data/pilot).
2. teacher - stratified sample of the music video cache labelled by
   deepseek-flash, used as a larger proxy reference when no human labels exist.

Usage:
    python3 scripts/make_ablation_sets.py --out-dir /tmp/ablation
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data" / "pilot"
TAGS = ROOT / "data" / "tags" / "BV1GJ411x7h7.json"


def build_human() -> list[dict]:
    gold = json.loads((PILOT / "audit_gold.json").read_text(encoding="utf-8"))
    sample = json.loads((PILOT / "audit_sample.json").read_text(encoding="utf-8"))["rows"]
    rows = []
    for index, row in enumerate(sample, start=1):
        # audit_gold.json is keyed by 1-based row index of audit_sample.json.
        entry = gold.get(str(index))
        if not entry:
            continue
        label, intensity = entry[0], float(entry[1])
        rows.append({"text": row["text"], "label": label, "intensity": intensity})
    return rows


def build_teacher(limit: int, seed: int) -> list[dict]:
    payload = json.loads(TAGS.read_text(encoding="utf-8"))
    items = list(payload["items"].values())
    buckets: dict[str, list[dict]] = {}
    for item in items:
        label = item.get("dominant") or "other"
        buckets.setdefault(label, []).append(item)
    rng = random.Random(seed)
    per_label = max(8, limit // max(1, len(buckets)))
    picked: list[dict] = []
    for label, bucket in sorted(buckets.items()):
        rng.shuffle(bucket)
        picked.extend(bucket[:per_label])
    rng.shuffle(picked)
    rows = []
    for item in picked[:limit]:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "text": text,
                "label": item.get("dominant") or "other",
                "intensity": float(item.get("score") or 0.0),
                "scores": item.get("tags") or {},
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/tmp/ablation")
    parser.add_argument("--teacher-limit", type=int, default=140)
    parser.add_argument("--seed", type=int, default=20261105)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("human", build_human()), ("teacher", build_teacher(args.teacher_limit, args.seed))):
        path = out / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["label"]] = counts.get(row["label"], 0) + 1
        print(f"{name}: {len(rows)} rows -> {path} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
