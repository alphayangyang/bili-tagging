"""Train the shipped per-video linear taggers from teacher-labelled rows.

Gold comments are excluded from training so the saved models can be evaluated
on the gold set without text-level leakage. Each video gets a centroid model
and a softmax model; the kind with the better held-out accuracy on a 20%
split of its own teacher rows is the one saved to data/study/models/.

Usage::

    python3 scripts/study/train_models_v4.py --labeled data/study/teacher_v4.jsonl
    python3 scripts/study/train_models_v4.py --labeled data/study/teacher_sample.jsonl,data/study/teacher_v4.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

GOLD = REPO / "data" / "study" / "gold.jsonl"


def norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gold_label(row: dict) -> str:
    return str(row.get("label") or row.get("dominant"))


def load_rows(spec: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in spec.split(","):
        path = Path(raw.strip())
        if not path.is_absolute():
            path = REPO / path
        for row in read_jsonl(path):
            key = (row["bvid"], norm(row["text"]))
            if key in seen or not gold_label(row):
                continue
            seen.add(key)
            rows.append(row)
    return rows


def split(rows: list[dict], dev_fraction: float, seed: int):
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    cut = max(1, int(len(shuffled) * dev_fraction))
    return shuffled[cut:], shuffled[:cut]


def accuracy(model: LinearTagger, rows: list[dict]) -> float:
    if not rows:
        return 0.0
    preds = model.predict([r["text"] for r in rows])
    return sum(1 for p, r in zip(preds, rows) if p == gold_label(r)) / len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labeled", default=str(REPO / "data" / "study" / "teacher_v4.jsonl"))
    ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--out-dir", default=str(REPO / "data" / "study" / "models"))
    ap.add_argument("--min-rows", type=int, default=80)
    ap.add_argument("--dev-fraction", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=20261118)
    args = ap.parse_args()

    gold = read_jsonl(Path(args.gold))
    gold_texts = {norm(r["text"]) for r in gold}
    rows = [r for r in load_rows(args.labeled) if norm(r["text"]) not in gold_texts]
    by_video: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_video[row["bvid"]].append(row)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for bvid, vid_rows in sorted(by_video.items()):
        if len(vid_rows) < args.min_rows:
            print(f"{bvid}: skipped, only {len(vid_rows)} rows")
            continue
        train_rows, dev_rows = split(vid_rows, args.dev_fraction, args.seed)
        best_kind, best_acc, best_model = "", -1.0, None
        for kind in ("centroid", "softmax"):
            model = LinearTagger(kind=kind)
            model.train([r["text"] for r in train_rows], [gold_label(r) for r in train_rows])
            score = accuracy(model, dev_rows)
            if score > best_acc:
                best_kind, best_acc, best_model = kind, score, model
        path = best_model.save(out_dir / f"{bvid}.json")
        summary[bvid] = {
            "rows": len(vid_rows),
            "kind": best_kind,
            "dev_accuracy": round(best_acc, 4),
            "labels": len(best_model.labels),
            "size_kb": round(path.stat().st_size / 1024, 1),
            "distribution": dict(Counter(gold_label(r) for r in vid_rows)),
        }
        print(bvid, json.dumps(summary[bvid], ensure_ascii=False))

    print(json.dumps({"videos": len(summary), "rows": len(rows), "models": summary},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())