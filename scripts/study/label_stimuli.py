"""Label a time-stratified sample of each study stimulus with the LLM teacher.

S2 needs teacher labels per stimulus to (a) pass the material checks, (b) train
one per-video linear model, and (c) report the label distribution that the C
condition filters. Labeling the whole stream would cost hours, so this samples
N comments uniformly over playback time and writes them in the frozen-dataset
schema (bvid, text, time_ms, dominant, tags, label_source="llm").

Usage::

    python3 scripts/study/label_stimuli.py                 # all six stimuli
    python3 scripts/study/label_stimuli.py --limit 120     # cheaper pilot
    python3 scripts/study/label_stimuli.py --bvid BV1... --force
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from danmaku_filter import fetch  # noqa: E402
from danmaku_filter.classify.llm import LLMClassifier, batch_size  # noqa: E402
from danmaku_filter.types import Danmaku  # noqa: E402

DEFAULT_STIMULI = REPO / "data" / "study" / "stimuli.json"
DEFAULT_OUT = REPO / "data" / "study" / "teacher_sample.jsonl"


def stratified(items: list[Danmaku], limit: int) -> list[Danmaku]:
    """Take up to limit comments spread uniformly over the playback timeline."""
    if limit <= 0 or limit >= len(items):
        return list(items)
    ordered = sorted(items, key=lambda dm: dm.time_ms)
    step = len(ordered) / float(limit)
    picked = []
    for index in range(limit):
        picked.append(ordered[min(len(ordered) - 1, int(index * step))])
    return picked


def load_done(path: Path) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("bvid") and row.get("text"):
            done.add((str(row["bvid"]), str(row["text"])))
    return done


def label_video(bvid: str, title: str, limit: int, cls: LLMClassifier) -> list[dict]:
    items = fetch.fetch_danmaku(bvid)
    if not items:
        return []
    picked = stratified(items, limit)
    started = time.time()
    results = cls.classify_batch(picked)
    rows = []
    for dm, result in zip(picked, results):
        rows.append(
            {
                "bvid": bvid,
                "title": title,
                "text": dm.text,
                "time_ms": int(dm.time_ms),
                "dominant": result.label,
                "score": round(float(result.confidence), 3),
                "tags": {key: round(float(value), 3) for key, value in (result.tags or {}).items() if value >= 0.1},
                "label_source": "llm",
            }
        )
    elapsed = time.time() - started
    print(
        "  %s: %d comments in %.1fs (%.0f ms/item)"
        % (bvid, len(rows), elapsed, 1000.0 * elapsed / max(1, len(rows))),
        flush=True,
    )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stimuli", default=str(DEFAULT_STIMULI))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--bvid", action="append", default=[])
    parser.add_argument("--force", action="store_true", help="re-label the videos listed in --bvid")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.stimuli).read_text(encoding="utf-8"))
    stimuli = manifest.get("stimuli") if isinstance(manifest, dict) else manifest
    wanted = set(args.bvid)
    out = Path(args.out)
    if args.dry_run:
        print(json.dumps({"stimuli": len(stimuli or []), "limit": args.limit, "out": str(out)}, ensure_ascii=False))
        return 0

    done = load_done(out)
    cls = LLMClassifier(batch_size=min(40, max(1, batch_size())), video_context="Bilibili short video, Chinese danmaku")
    written = 0
    with out.open("a", encoding="utf-8") as stream:
        for entry in stimuli or []:
            bvid = str(entry.get("bvid") or "")
            if not bvid:
                continue
            if wanted and bvid not in wanted:
                continue
            if not wanted and not args.force:
                existing = sum(1 for key in done if key[0] == bvid)
                if existing >= args.limit:
                    print("  %s: already has %d rows" % (bvid, existing), flush=True)
                    continue
            print("labelling %s (%s)" % (bvid, entry.get("label") or ""), flush=True)
            try:
                rows = label_video(bvid, str(entry.get("title") or ""), args.limit, cls)
            except Exception as error:  # keep the batch alive
                print("  %s failed: %s: %s" % (bvid, type(error).__name__, error), file=sys.stderr, flush=True)
                continue
            for row in rows:
                key = (row["bvid"], row["text"])
                if key in done:
                    continue
                done.add(key)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            stream.flush()
    print("wrote %d rows to %s" % (written, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
