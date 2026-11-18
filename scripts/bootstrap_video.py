"""Bootstrap a per-video linear model from LLM teacher labels.

The cascade in docs/design/ablation-v1.md needs a handful of labelled
comments before the local linear model can take over. This script fetches the
danmaku for one video, labels a stratified sample with the LLM teacher, trains
LinearTagger and writes data/models/<bvid>.json so the local server can switch
from --online-backend llm to --online-backend linear for that video.

Usage::

    python3 scripts/bootstrap_video.py --bvid BV1PF4g6ME26 --sample 80
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from danmaku_filter.classify.linear import LinearTagger
from danmaku_filter.classify.llm import LLMClassifier, batch_size, settings
from danmaku_filter.fetch import fetch_danmaku
from danmaku_filter.pipeline import _build_item

REPO_ROOT = Path(__file__).resolve().parents[1]


def sample_rows(danmaku, limit: int, seed: int):
    """Diversify by length: short reactions and long sentences both matter."""
    rows = [d for d in danmaku if 2 <= len(d.text.strip()) <= 60]
    rows.sort(key=lambda d: (len(d.text), d.text))
    if len(rows) <= limit:
        return rows
    rng = random.Random(seed)
    buckets = 5
    per_bucket = max(1, limit // buckets)
    picked = []
    for index in range(buckets):
        chunk = rows[index * len(rows) // buckets : (index + 1) * len(rows) // buckets]
        picked.extend(rng.sample(chunk, min(per_bucket, len(chunk))))
    picked.extend(rng.sample(rows, max(0, limit - len(picked))))
    seen, unique = set(), []
    for row in picked:
        if row.text in seen:
            continue
        seen.add(row.text)
        unique.append(row)
    return unique[:limit]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="bootstrap a linear model for one video")
    parser.add_argument("--bvid", required=True)
    parser.add_argument("--sample", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--kind", choices=("centroid", "softmax"), default="centroid")
    parser.add_argument("--context", default="")
    parser.add_argument("--tags-dir", default=str(REPO_ROOT / "data" / "tags"))
    parser.add_argument("--models-dir", default=str(REPO_ROOT / "data" / "models"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    base, _key, model = settings()
    danmaku = fetch_danmaku(args.bvid)
    rows = sample_rows(danmaku, args.sample, args.seed)
    if len(rows) < 20:
        print("error: only %d comments available, need >= 20" % len(rows), file=sys.stderr)
        return 1

    classifier = LLMClassifier(
        batch_size=max(1, min(40, batch_size())),
        video_context=args.context or "Bilibili video, Chinese danmaku",
    )
    labelled = []
    for start in range(0, len(rows), classifier.batch_size):
        chunk = rows[start : start + classifier.batch_size]
        results = classifier.classify_batch(chunk)
        for row, result in zip(chunk, results):
            labelled.append(_build_item(row, result))

    texts = [item["text"] for item in labelled]
    labels = [item["dominant"] for item in labelled]
    tagger = LinearTagger(kind=args.kind).train(texts, labels)
    out_path = Path(args.models_dir) / ("%s.json" % args.bvid)
    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tagger.save(out_path)

    from collections import Counter

    print(
        json.dumps(
            {
                "bvid": args.bvid,
                "fetched": len(danmaku),
                "labelled": len(labelled),
                "kind": args.kind,
                "teacher": model,
                "endpoint": base,
                "distribution": Counter(labels).most_common(),
                "path": str(out_path),
                "size_kb": round(out_path.stat().st_size / 1024, 1) if out_path.exists() else 0,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
