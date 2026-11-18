"""Evaluate flat vs hierarchical students on the study gold set.

Compares three label sources (`--labeled`): the batch-labelled pool, the
per-item two-vote pool (unanimous rows only), and their union. Gold texts are
always removed from training so the numbers are leak-free.

Usage:
    python3 scripts/study/eval_hier.py --labeled data/study/teacher_v4.jsonl
    python3 scripts/study/eval_hier.py --labeled data/study/teacher_v5.jsonl --unanimous-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from danmaku_filter.classify.hier import HierTagger
from danmaku_filter.classify.linear import LinearTagger
from scripts.f1_eval import summarize

GOLD = ROOT / "data" / "study" / "gold_v2.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_train(paths: list[Path], gold_texts: set[str], unanimous_only: bool) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for path in paths:
        for row in read_jsonl(path):
            text = str(row.get("text", "")).strip()
            if not text or text in gold_texts or text in seen:
                continue
            if unanimous_only and row.get("unanimous") == 0.0:
                continue
            seen.add(text)
            rows.append(row)
    return rows


def evaluate(name: str, preds: list[str], gold: list[dict]) -> dict:
    scores = summarize(list(zip([row["label"] for row in gold], preds)))
    result = {"name": name, **scores}
    print("%-28s acc=%.3f macro=%.3f weighted=%.3f family=%.3f" % (
        name, scores["accuracy"], scores["macro_f1"], scores["weighted_f1"], scores["family_macro_f1"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labeled", required=True, help="comma separated jsonl files")
    parser.add_argument("--gold", default=str(GOLD))
    parser.add_argument("--unanimous-only", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    gold = read_jsonl(Path(args.gold))
    gold_texts = {row["text"].strip() for row in gold}
    train = load_train([Path(p) for p in args.labeled.split(",")], gold_texts, args.unanimous_only)
    texts = [row["text"] for row in train]
    labels = [row["dominant"] for row in train]
    print("train rows: %d (gold excluded)" % len(train))

    results = []
    flat = LinearTagger(kind="softmax").train(texts, labels)
    results.append(evaluate("flat-softmax", flat.predict([row["text"] for row in gold]), gold))
    hier = HierTagger().train(texts, labels)
    results.append(evaluate("hierarchical", hier.predict([row["text"] for row in gold]), gold))

    if args.out:
        Path(args.out).write_text(json.dumps({"rows": len(train), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

