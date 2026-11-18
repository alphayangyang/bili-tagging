
"""Train per-video and global linear taggers from existing tag caches.

Usage:
    python3 scripts/train_models.py                     # all caches -> data/models
    python3 scripts/train_models.py --bvid BV1GJ411x7h7 # single video
    python3 scripts/train_models.py --global-only

Models are written to data/models/<bvid>.json and data/models/_global.json.
Labels come from cached items (dominant field); items labelled by an LLM
(source starts with "llm") are preferred when a cache mixes sources.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TAGS = REPO / "data" / "tags"
DEFAULT_MODELS = REPO / "data" / "models"
MIN_ROWS = 40


def load_rows(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") or {}
    rows = []
    for item in items.values():
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        label = item.get("dominant") or item.get("label") or "other"
        if not text:
            continue
        rows.append({"text": text, "label": label, "source": item.get("source") or ""})
    return rows


def prefer_llm(rows: list[dict]) -> list[dict]:
    llm = [r for r in rows if str(r["source"]).startswith("llm")]
    return llm if len(llm) >= MIN_ROWS else rows


def split(rows: list[dict], ratio: float = 0.8, seed: int = 13):
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    cut = max(1, int(len(rows) * ratio))
    return rows[:cut], rows[cut:] or rows[:1]


def evaluate(kind: str, train_rows: list[dict], test_rows: list[dict]) -> float:
    tagger = LinearTagger(kind=kind).train([r["text"] for r in train_rows], [r["label"] for r in train_rows])
    gold = [r["label"] for r in test_rows]
    pred = tagger.predict([r["text"] for r in test_rows])
    return sum(1 for p, g in zip(pred, gold) if p == g) / max(1, len(gold))


def train_one(rows: list[dict], name: str, out_dir: Path, ratios=(0.8, 0.5)) -> dict | None:
    if len(rows) < MIN_ROWS:
        print("  %-16s skip (%d rows < %d)" % (name, len(rows), MIN_ROWS))
        return None
    best = None
    for kind in ("centroid", "softmax"):
        for ratio in ratios:
            if len(rows) * ratio < 20:
                continue
            train_rows, test_rows = split(rows, ratio)
            acc = evaluate(kind, train_rows, test_rows)
            if best is None or acc > best[1]:
                best = (kind, acc, ratio)
    kind, acc, ratio = best
    train_rows, _ = split(rows, ratio)
    tagger = LinearTagger(kind=kind).train([r["text"] for r in train_rows], [r["label"] for r in train_rows])
    path = out_dir / ("%s.json" % name)
    tagger.save(path)
    size_kb = path.stat().st_size / 1024
    print("  %-16s %5d rows -> %-8s dev_acc=%.3f  %.0f KB  %s"
          % (name, len(rows), kind, acc, size_kb, path.name))
    return {"name": name, "rows": len(rows), "kind": kind, "dev_accuracy": round(acc, 4),
            "size_kb": round(size_kb, 1), "path": str(path.relative_to(REPO)),
            "distribution": dict(Counter(r["label"] for r in rows).most_common())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tags-dir", default=str(DEFAULT_TAGS))
    ap.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    ap.add_argument("--bvid", action="append", default=[], help="limit to these video ids")
    ap.add_argument("--global-only", action="store_true")
    args = ap.parse_args()

    tags_dir = Path(args.tags_dir)
    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    paths = sorted(tags_dir.glob("BV*.json"))
    if args.bvid:
        wanted = set(args.bvid)
        paths = [p for p in paths if p.stem in wanted]
    if not paths:
        print("no tag caches found in %s" % tags_dir)
        return 1

    summary = {"models": [], "global": None}
    pooled = []
    for path in paths:
        rows = prefer_llm(load_rows(path))
        pooled.extend(rows)
        if args.global_only:
            continue
        info = train_one(rows, path.stem, models_dir)
        if info:
            info["source"] = str(path.relative_to(REPO))
            summary["models"].append(info)

    if pooled:
        info = train_one(pooled, "_global", models_dir)
        if info:
            summary["global"] = info

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
