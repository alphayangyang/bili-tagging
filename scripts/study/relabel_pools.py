"""Label a large per-video sample from the cached pools with the LLM teacher.

Gold comments are always excluded so the resulting labels can be used to train
models that are benchmarked on the gold set without text-level leakage.

Usage::

    python3 scripts/study/relabel_pools.py --per-video 500
    python3 scripts/study/relabel_pools.py --bvid BV1Ho4y1D7Wz --per-video 300

Output rows are appended as {bvid, title, text, time_ms, dominant, score,
tags, label_source} so an interrupted run can be resumed.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from danmaku_filter.classify.llm import LLMClassifier, batch_size  # noqa: E402
from danmaku_filter.types import Danmaku  # noqa: E402

STIMULI = REPO / "data" / "study" / "stimuli.json"
GOLD = REPO / "data" / "study" / "gold.jsonl"
TAGS = REPO / "data" / "tags"


def norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def pool_items(bvid: str) -> list[dict]:
    path = TAGS / f"{bvid}.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") or {}
    rows = []
    for dmid, item in items.items():
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        rows.append({"dmid": str(dmid), "text": text,
                     "time_ms": int(item.get("time_ms") or 0)})
    return rows


def stratified(rows: list[dict], limit: int, seed: int) -> list[dict]:
    """Prefer a time-stratified sample, fall back to a seeded shuffle."""
    if len(rows) <= limit:
        return list(rows)
    timed = [r for r in rows if r["time_ms"] > 0]
    if len(timed) >= limit:
        timed.sort(key=lambda r: r["time_ms"])
        step = len(timed) / limit
        return [timed[min(len(timed) - 1, int(i * step))] for i in range(limit)]
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    return shuffled[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-video", type=int, default=500)
    ap.add_argument("--bvid", default="")
    ap.add_argument("--out", default=str(REPO / "data" / "study" / "teacher_v4.jsonl"))
    ap.add_argument("--seed", type=int, default=20261118)
    args = ap.parse_args()

    stimuli = json.loads(STIMULI.read_text(encoding="utf-8"))
    videos = list(stimuli.get("stimuli") or stimuli.get("videos") or [])
    if stimuli.get("placeholder"):
        raise SystemExit("stimuli.json is still a placeholder")
    if args.bvid:
        videos = [v for v in videos if v["bvid"] == args.bvid]
        if not videos:
            raise SystemExit(f"unknown stimulus {args.bvid}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = read_jsonl(out)
    done = {(r["bvid"], norm(r["text"])) for r in existing}
    gold = read_jsonl(GOLD)
    gold_texts = {norm(r["text"]) for r in gold}

    started = time.time()
    total_written = 0
    for video in videos:
        bvid = video["bvid"]
        title = video.get("title") or ""
        classifier = LLMClassifier(
            batch_size=max(1, min(100, batch_size())), video_context=title
        )
        rows = pool_items(bvid)
        seen = set()
        candidates = []
        for row in rows:
            key = norm(row["text"])
            if key in gold_texts or key in seen:
                continue
            seen.add(key)
            candidates.append(row)
        sample = stratified(candidates, args.per_video, args.seed)
        sample = [r for r in sample if (bvid, norm(r["text"])) not in done]
        print(f"{bvid}: pool={len(rows)} unique_candidates={len(candidates)} "
              f"sample={len(sample)} already_done={len(rows) - len(candidates)}", flush=True)
        for start in range(0, len(sample), classifier.batch_size):
            chunk = sample[start:start + classifier.batch_size]
            items = [Danmaku(text=r["text"], time_ms=r["time_ms"]) for r in chunk]
            results = classifier.classify_batch(items)
            lines = []
            for row, result in zip(chunk, results):
                lines.append(json.dumps({
                    "bvid": bvid,
                    "title": title,
                    "text": row["text"],
                    "time_ms": row["time_ms"],
                    "dominant": result.label,
                    "score": round(float(result.confidence), 4),
                    "tags": {k: round(float(v), 4) for k, v in (result.tags or {}).items()},
                    "label_source": "llm-v4",
                }, ensure_ascii=False))
            with out.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            total_written += len(lines)
            done.update((bvid, norm(r["text"])) for r in chunk)
            print(f"  wrote {total_written} rows, elapsed {time.time() - started:.0f}s", flush=True)

    rows = read_jsonl(out)
    print("total rows", len(rows), "distribution", dict(Counter(r["dominant"] for r in rows)),
          "per video", dict(Counter(r["bvid"] for r in rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())