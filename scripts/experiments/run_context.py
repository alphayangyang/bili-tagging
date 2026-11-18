"""E2 experiment: does video context help cross-video tagging?

Protocol
--------
Leave-one-video-out over the videos in ``data/experiments/dataset.jsonl``.
For each held-out video, the pure-stdlib linear tagger
(``LinearTagger(kind="centroid")``) is trained on the other videos and the
same comments are re-scored under five input variants:

V0  text
V1  text + video title
V2  text + video title + domain
V3  text + video title + position bucket (5 buckets, order / total)
V4  text + video title + previous and next comment (20 chars each)

Context is appended after the comment text, so the comment's own character
n-grams stay contiguous. Position buckets are relative (0..4 over the whole
video), which is the only signal that transfers when absolute timestamps do
not.

The raw-concatenation variants turn out to be dominated by out-of-vocabulary
grams: the held-out video title never appears in training, and the frozen
hashed TF-IDF gives unseen grams the df = 0 fallback idf, which swamps the
comment itself after L2 normalisation. Three supplementary variants isolate
the context channels that do not carry OOV text, so the report can separate
"context does not help" from "raw title text breaks the vectoriser":

S2  text + domain
S3  text + position bucket (5)
S4  text + previous/next comment (20 chars each)

Metrics
-------
* dominant-label agreement
* macro-F1 over the full taxonomy via ``danmaku_filter.metrics.prf``
* tag MAE on tags shared by the gold and the predicted multi-label vectors
* diagnostics: prediction diversity and the majority-class baseline

Usage
-----
    python3 scripts/experiments/run_context.py

Outputs
-------
data/experiments/results/context.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402
from danmaku_filter.metrics import prf  # noqa: E402
from danmaku_filter.taxonomy import CATEGORY_IDS, FALLBACK_ID  # noqa: E402

DATASET = ROOT / "data" / "experiments" / "dataset.jsonl"
RESULTS = ROOT / "data" / "experiments" / "results" / "context.json"
ALL_LABELS = list(CATEGORY_IDS) + [FALLBACK_ID]
VARIANTS = ("V0", "V1", "V2", "V3", "V4")
VARIANT_NOTES = {
    "V0": "text only",
    "V1": "text + video title",
    "V2": "text + title + domain",
    "V3": "text + title + position bucket (5)",
    "V4": "text + title + previous/next comment (20 chars)",
}
SUPPLEMENTARY_VARIANTS = ("S2", "S3", "S4")
SUPPLEMENTARY_NOTES = {
    "S2": "text + domain",
    "S3": "text + position bucket (5)",
    "S4": "text + previous/next comment (20 chars)",
}
ALL_VARIANTS = VARIANTS + SUPPLEMENTARY_VARIANTS
POSITION_BUCKETS = 5
NEIGHBOR_CHARS = 20
MAX_TRAIN_ROWS = 2000
SEED = 20261105


def load_rows(path: Path) -> list[dict]:
    """Read the JSONL experiment dataset."""
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def group_by_video(rows: list[dict]) -> dict[str, list[dict]]:
    """Group rows by bvid and sort each group by temporal order."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["bvid"]), []).append(row)
    for video_rows in grouped.values():
        video_rows.sort(key=lambda row: int(row.get("order", 0)))
    return grouped


def _clean(text: object) -> str:
    """Collapse whitespace so context join is stable."""
    return " ".join(str(text or "").split())


def position_bucket(index: int, total: int) -> int:
    """Map a sorted comment index to one of POSITION_BUCKETS buckets."""
    return min(POSITION_BUCKETS - 1, index * POSITION_BUCKETS // max(total, 1))


def build_inputs(video_rows: list[dict], variant: str) -> list[str]:
    """Render one model input string per row for the given variant."""
    if variant not in ALL_VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    total = len(video_rows)
    title = _clean(video_rows[0].get("title")) if video_rows else ""
    domain = _clean(video_rows[0].get("domain")) if video_rows else ""
    inputs: list[str] = []
    for index, row in enumerate(video_rows):
        parts = [_clean(row.get("text"))]
        if variant == "V1":
            parts.append(title)
        elif variant == "V2":
            parts.extend([title, domain])
        elif variant == "V3":
            parts.extend([title, f"pos{position_bucket(index, total)}"])
        elif variant == "V4":
            parts.extend([title, _neighbor(video_rows, index - 1), _neighbor(video_rows, index + 1)])
        elif variant == "S2":
            parts.append(domain)
        elif variant == "S3":
            parts.append(f"pos{position_bucket(index, total)}")
        elif variant == "S4":
            parts.extend([_neighbor(video_rows, index - 1), _neighbor(video_rows, index + 1)])
        inputs.append(" ".join(part for part in parts if part))
    return inputs


def _neighbor(video_rows: list[dict], index: int) -> str:
    """Return the truncated text of a neighbouring comment, or "" at the edge."""
    if index < 0 or index >= len(video_rows):
        return ""
    return _clean(video_rows[index].get("text"))[:NEIGHBOR_CHARS]


def video_examples(grouped: dict[str, list[dict]], bvids: list[str], variant: str) -> list[tuple[str, str]]:
    """Flatten (input, label) pairs for the given videos."""
    examples: list[tuple[str, str]] = []
    for bvid in bvids:
        rows = grouped[bvid]
        for text, row in zip(build_inputs(rows, variant), rows):
            examples.append((text, str(row["dominant"])))
    return examples


def cap_examples(
    examples: list[tuple[str, str]], max_rows: int, rng: random.Random
) -> tuple[list[tuple[str, str]], bool]:
    """Deterministically subsample the training set for speed."""
    if max_rows <= 0 or len(examples) <= max_rows:
        return examples, False
    indices = sorted(rng.sample(range(len(examples)), max_rows))
    return [examples[index] for index in indices], True


def tag_mae(test_rows: list[dict], score_rows: list[dict[str, float]]) -> tuple[float | None, int, int]:
    """Mean |pred - gold| over tags present in both vectors.

    Returns (mae, rows with at least one shared tag, shared tag pairs).
    """
    total = 0.0
    pairs = 0
    covered = 0
    for row, scores in zip(test_rows, score_rows):
        gold_tags = row.get("tags") or {}
        shared = [tag for tag in gold_tags if tag in scores]
        if not shared:
            continue
        covered += 1
        for tag in shared:
            total += abs(float(scores[tag]) - float(gold_tags[tag]))
            pairs += 1
    return (total / pairs if pairs else None), covered, pairs


def prediction_diagnostics(
    train_examples: list[tuple[str, str]],
    test_rows: list[dict],
    predictions: list[str],
    score_rows: list[dict[str, float]],
) -> dict:
    """Describe prediction diversity so collapsed variants are obvious."""
    golds = [str(row["dominant"]) for row in test_rows]
    counts = Counter(predictions)
    top_label, top_count = counts.most_common(1)[0]
    train_counts = Counter(label for _, label in train_examples)
    majority_label = train_counts.most_common(1)[0][0]
    test_majority_label, test_majority_count = Counter(golds).most_common(1)[0]
    return {
        "top_pred_label": top_label,
        "top_pred_share": round(top_count / len(predictions), 4),
        "distinct_pred_labels": len(counts),
        "mean_max_prob": round(sum(max(row.values()) for row in score_rows) / len(score_rows), 4),
        "train_majority_label": majority_label,
        "train_majority_baseline": round(
            sum(1 for gold in golds if gold == majority_label) / len(golds), 4
        ),
        "test_majority_label": test_majority_label,
        "test_majority_share": round(test_majority_count / len(golds), 4),
    }


def evaluate_fold(
    grouped: dict[str, list[dict]],
    train_bvids: list[str],
    test_bvid: str,
    variant: str,
    max_train: int,
    seed: int,
) -> dict:
    """Train on train_bvids and score the held-out video."""
    all_examples = video_examples(grouped, train_bvids, variant)
    examples, capped = cap_examples(all_examples, max_train, random.Random(seed))
    test_rows = grouped[test_bvid]
    test_inputs = build_inputs(test_rows, variant)
    tagger = LinearTagger(kind="centroid").train(
        [text for text, _ in examples], [label for _, label in examples]
    )
    scores = tagger.predict_scores(test_inputs)
    predictions = [max(row, key=row.get) for row in scores]
    golds = [str(row["dominant"]) for row in test_rows]
    agreement = sum(gold == pred for gold, pred in zip(golds, predictions)) / len(golds)
    report = prf(
        [{"text": str(row["text"]), "label": gold} for row, gold in zip(test_rows, golds)],
        [{"text": str(row["text"]), "label": pred} for row, pred in zip(test_rows, predictions)],
        ALL_LABELS,
    )
    mae, covered, pairs = tag_mae(test_rows, scores)
    unseen = sorted(set(golds) - set(tagger.labels))
    unseen_rows = sum(1 for gold in golds if gold not in tagger.labels)
    return {
        "test_bvid": test_bvid,
        "train_bvids": list(train_bvids),
        "n_test": len(test_rows),
        "n_train": len(all_examples),
        "n_train_used": len(examples),
        "train_capped": capped,
        "train_labels": list(tagger.labels),
        "unseen_test_labels": unseen,
        "unseen_test_rows": unseen_rows,
        "agreement": round(agreement, 4),
        "agreement_ceiling": round(1.0 - unseen_rows / len(golds), 4),
        "macro_f1": report["macro_f1"],
        "prf_n": report["n"],
        "prf_unmatched": report["unmatched"],
        "per_label": report["per_label"],
        "tag_mae": round(mae, 4) if mae is not None else None,
        "tag_covered_rows": covered,
        "tag_pair_count": pairs,
        "test_labels": dict(sorted(Counter(golds).items())),
        "diagnostics": prediction_diagnostics(examples, test_rows, predictions, scores),
    }


def aggregate(
    folds: dict[str, dict[str, dict]],
    bvids: list[str],
    variants: tuple[str, ...],
    base: dict | None = None,
) -> dict:
    """Macro-average each variant across held-out videos and deltas vs V0."""
    summary: dict[str, dict] = {}
    for variant in variants:
        rows = [folds[bvid][variant] for bvid in bvids]
        n_total = sum(row["n_test"] for row in rows)
        maes = [row["tag_mae"] for row in rows if row["tag_mae"] is not None]
        summary[variant] = {
            "agreement_mean": round(sum(row["agreement"] for row in rows) / len(rows), 4),
            "agreement_weighted": round(
                sum(row["agreement"] * row["n_test"] for row in rows) / n_total, 4
            ),
            "macro_f1_mean": round(sum(row["macro_f1"] for row in rows) / len(rows), 4),
            "tag_mae_mean": round(sum(maes) / len(maes), 4) if maes else None,
            "unseen_test_rows": sum(row["unseen_test_rows"] for row in rows),
        }
    base_row = summary.get("V0") if "V0" in summary else base
    if base_row is None:
        raise ValueError("aggregate needs a V0 row or an explicit base")
    for item in summary.values():
        item["agreement_pp_vs_v0"] = round(
            (item["agreement_mean"] - base_row["agreement_mean"]) * 100, 2
        )
        item["macro_f1_pp_vs_v0"] = round(
            (item["macro_f1_mean"] - base_row["macro_f1_mean"]) * 100, 2
        )
        item["tag_mae_delta_vs_v0"] = (
            round(base_row["tag_mae_mean"] - item["tag_mae_mean"], 4)
            if base_row["tag_mae_mean"] is not None and item["tag_mae_mean"] is not None
            else None
        )
    return summary


def dataset_meta(path: Path, grouped: dict[str, list[dict]]) -> dict:
    """Describe the dataset and its per-video label distribution."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    videos = []
    observed: set[str] = set()
    for bvid, rows in grouped.items():
        dist = Counter(str(row["dominant"]) for row in rows)
        observed.update(dist)
        videos.append(
            {
                "bvid": bvid,
                "domain": rows[0].get("domain"),
                "title": rows[0].get("title"),
                "rows": len(rows),
                "dominant": dict(sorted(dist.items())),
            }
        )
    return {
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "sha256": digest,
        "rows": sum(len(rows) for rows in grouped.values()),
        "videos": videos,
        "observed_labels": sorted(observed),
        "macro_f1_labels": ALL_LABELS,
    }


def run_variants(
    grouped: dict[str, list[dict]],
    bvids: list[str],
    variants: tuple[str, ...],
    max_train: int,
    seed: int,
) -> dict[str, dict[str, dict]]:
    """Evaluate every variant on every leave-one-video-out fold."""
    folds: dict[str, dict[str, dict]] = {}
    for test_bvid in bvids:
        train_bvids = [bvid for bvid in bvids if bvid != test_bvid]
        folds[test_bvid] = {}
        for variant in variants:
            fold = evaluate_fold(grouped, train_bvids, test_bvid, variant, max_train, seed)
            folds[test_bvid][variant] = fold
            diag = fold["diagnostics"]
            print(
                f"[{variant}] test={test_bvid} n_train={fold['n_train_used']}"
                f" agreement={fold['agreement']:.4f} macro_f1={fold['macro_f1']:.4f}"
                f" tag_mae={fold['tag_mae']} top={diag['top_pred_label']}"
                f"@{diag['top_pred_share']:.2f}",
                flush=True,
            )
    return folds


def main(argv: list[str] | None = None) -> int:
    """Run the LOVO variant sweep and write context.json."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--max-train", type=int, default=MAX_TRAIN_ROWS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        raise SystemExit(f"dataset not found: {args.dataset}")
    rows = load_rows(args.dataset)
    grouped = group_by_video(rows)
    bvids = list(grouped)
    if len(bvids) < 2:
        raise SystemExit(f"need at least 2 videos, found {len(bvids)}")
    print(f"dataset: {args.dataset} rows={len(rows)} videos={len(bvids)}", flush=True)

    folds = run_variants(grouped, bvids, VARIANTS, args.max_train, args.seed)
    supplementary = run_variants(
        grouped, bvids, SUPPLEMENTARY_VARIANTS, args.max_train, args.seed
    )
    aggregate_main = aggregate(folds, bvids, VARIANTS)
    aggregate_supplementary = aggregate(
        supplementary, bvids, SUPPLEMENTARY_VARIANTS, base=aggregate_main["V0"]
    )

    payload = {
        "experiment": "e2-context",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": dataset_meta(args.dataset, grouped),
        "config": {
            "protocol": "leave-one-video-out",
            "tagger": "centroid",
            "vectorizer": "hashed char 1-3 grams, tf-idf, L2 (danmaku_filter.classify.linear)",
            "max_train_rows": args.max_train,
            "seed": args.seed,
            "position_buckets": POSITION_BUCKETS,
            "neighbor_chars": NEIGHBOR_CHARS,
            "variants": {variant: VARIANT_NOTES[variant] for variant in VARIANTS},
            "supplementary_variants": {
                variant: SUPPLEMENTARY_NOTES[variant] for variant in SUPPLEMENTARY_VARIANTS
            },
        },
        "results": folds,
        "supplementary": supplementary,
        "aggregate": aggregate_main,
        "supplementary_aggregate": aggregate_supplementary,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    for variant in VARIANTS:
        item = aggregate_main[variant]
        print(
            f"aggregate {variant}: agreement={item['agreement_mean']:.4f}"
            f" macro_f1={item['macro_f1_mean']:.4f} tag_mae={item['tag_mae_mean']}"
            f" ({item['agreement_pp_vs_v0']:+.2f} pp)",
            flush=True,
        )
    for variant in SUPPLEMENTARY_VARIANTS:
        item = aggregate_supplementary[variant]
        print(
            f"supplementary {variant}: agreement={item['agreement_mean']:.4f}"
            f" macro_f1={item['macro_f1_mean']:.4f} tag_mae={item['tag_mae_mean']}"
            f" ({item['agreement_pp_vs_v0']:+.2f} pp)",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
