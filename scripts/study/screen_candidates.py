"""Screen candidate study videos: stream size, density and social-label supply.

Fetches each candidate's metadata and whole danmaku list (legacy list.so via
fetch_danmaku), then reports duration, item count, peak per-minute density and
rule-based counts for attack / conflict / spoiler / inflammatory so we can pick
six S2 stimuli without LLM labelling every candidate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from danmaku_filter import fetch
from danmaku_filter.classify.rules import RuleClassifier
from danmaku_filter.types import Danmaku

LABELS = ("attack", "conflict", "spoiler", "inflammatory", "irrelevant_repetition", "political")


def density_stats(items, duration_s):
    if not items or duration_s <= 0:
        return {"items": len(items), "per_min": 0.0, "peak_per_min": 0.0}
    buckets = {}
    for dm in items:
        minute = int(max(0, dm.time_ms) // 60000)
        buckets[minute] = buckets.get(minute, 0) + 1
    peak = max(buckets.values()) / 1.0 if buckets else 0.0
    # each bucket is one minute already
    return {
        "items": len(items),
        "per_min": round(len(items) / (duration_s / 60.0), 1),
        "peak_per_min": float(max(buckets.values())),
        "span_s": round((max(dm.time_ms for dm in items) - min(dm.time_ms for dm in items)) / 1000.0, 1),
    }


def screen(bvid: str) -> dict:
    info = fetch.get_video_info(bvid)
    duration = int(info.get("duration") or 0)
    items = fetch.fetch_danmaku(bvid)
    clf = RuleClassifier()
    counts = {label: 0 for label in LABELS}
    for dm in items:
        result = clf.classify(dm)
        if result.label in counts:
            counts[result.label] += 1
    return {
        "bvid": bvid,
        "title": info.get("title"),
        "duration_s": duration,
        "aid": info.get("aid"),
        "cid": info.get("cid"),
        "items": len(items),
        "density": density_stats(items, duration),
        "rules_counts": counts,
    }


def main(argv):
    bvids = argv[1:]
    if not bvids:
        print("usage: screen_candidates.py BV1... [BV2...]", file=sys.stderr)
        return 2
    rows = []
    for bvid in bvids:
        try:
            row = screen(bvid)
        except Exception as error:
            row = {"bvid": bvid, "error": "%s: %s" % (type(error).__name__, error)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    out = Path("data/study/candidates.json")
    merged = {}
    if out.exists():
        for old in json.loads(out.read_text(encoding="utf-8")):
            merged[old["bvid"]] = old
    for row in rows:
        merged[row["bvid"]] = row
    out.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
