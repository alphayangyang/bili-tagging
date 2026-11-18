"""Benchmark the per-video linear taggers against the 120-comment gold set.

Trains one tagger per stimulus video from teacher-labelled rows (gold texts are
always excluded from training), predicts the gold comments, and reports
dominant-label metrics plus the binary hide/fold filter metrics.

Usage::

    python3 scripts/f1_bench.py
    python3 scripts/f1_bench.py --labeled data/study/teacher_sample.jsonl
    python3 scripts/f1_bench.py --labeled data/study/teacher_v4.jsonl --methods centroid,softmax,ensemble --out data/experiments/results/f1_bench.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

GOLD = REPO / "data" / "study" / "gold.jsonl"
TEACHER = REPO / "data" / "study" / "teacher_sample.jsonl"
REMOVE = {"attack", "conflict", "inflammatory", "irrelevant_repetition", "spoiler"}
ALL_LABELS = [
    "information", "humor", "emotional_resonance", "irrelevant_repetition",
    "attack", "conflict", "spoiler", "political", "inflammatory", "other",
]


def norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def gold_label(row: dict) -> str:
    return str(row.get("label") or row.get("dominant"))


def prf(pairs: list[tuple[str, str]], label: str) -> tuple[float, float, float, int]:
    tp = sum(1 for g, p in pairs if g == label and p == label)
    fp = sum(1 for g, p in pairs if g != label and p == label)
    fn = sum(1 for g, p in pairs if g == label and p != label)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, tp + fn


def metrics(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    support = Counter(g for g, _ in pairs)
    labels = [lab for lab in ALL_LABELS if support.get(lab)]
    per = {}
    for lab in labels:
        prec, rec, f1, sup = prf(pairs, lab)
        per[lab] = {"precision": round(prec, 4), "recall": round(rec, 4),
                    "f1": round(f1, 4), "support": sup}
    macro = sum(v["f1"] for v in per.values()) / len(per) if per else 0.0
    weighted = sum(v["f1"] * v["support"] for v in per.values()) / n if n else 0.0
    tp = sum(1 for g, p in pairs if g in REMOVE and p in REMOVE)
    fp = sum(1 for g, p in pairs if g not in REMOVE and p in REMOVE)
    fn = sum(1 for g, p in pairs if g in REMOVE and p not in REMOVE)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "n": n,
        "accuracy": round(sum(1 for g, p in pairs if g == p) / n, 4) if n else 0.0,
        "macro_f1": round(macro, 4),
        "weighted_f1": round(weighted, 4),
        "per_label": per,
        "filter": {"precision": round(prec, 4), "recall": round(rec, 4),
                   "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn},
    }


def normalized_scores(scores: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in scores.values())
    if total <= 0:
        return {k: 1.0 / len(scores) for k in scores}
    return {k: max(0.0, v) / total for k, v in scores.items()}


def train_models(rows: list[dict], kind: str) -> dict[str, LinearTagger]:
    by_video: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_video[row["bvid"]].append(row)
    models = {}
    for bvid, vid_rows in by_video.items():
        tagger = LinearTagger(kind=kind)
        tagger.train([r["text"] for r in vid_rows], [gold_label(r) for r in vid_rows])
        models[bvid] = tagger
    return models


def predict_scores(models: dict[str, LinearTagger], gold: list[dict]) -> list[dict[str, float]]:
    by_video: dict[str, list[int]] = defaultdict(list)
    for idx, row in enumerate(gold):
        by_video[row["bvid"]].append(idx)
    out: list[dict[str, float]] = [{} for _ in gold]
    for bvid, idxs in by_video.items():
        model = models.get(bvid)
        if model is None:
            continue
        preds = model.predict_scores([gold[i]["text"] for i in idxs])
        for i, scores in zip(idxs, preds):
            out[i] = scores
    return out


def apply_reject(scores: dict[str, float], tau: float) -> str:
    best = max(scores, key=lambda k: scores[k])
    conf = scores[best]
    return "other" if conf < tau else best


def macro_at(scores_list, labels, tau):
    pairs = [(labels[i], apply_reject(s, tau)) for i, s in enumerate(scores_list)]
    support = Counter(g for g, _ in pairs)
    f1s = [prf(pairs, lab)[2] for lab in ALL_LABELS if support.get(lab)]
    return sum(f1s) / len(f1s) if f1s else 0.0


def tune_tau(scores_list, labels, grid) -> tuple[float, float]:
    """Return (tau chosen on all rows, cross-validated macro-F1 at that tau)."""
    best_tau, best_full = 0.0, -1.0
    for tau in grid:
        f = macro_at(scores_list, labels, tau)
        if f > best_full:
            best_tau, best_full = tau, f
    n = len(scores_list)
    fold_of = [i % 5 for i in range(n)]
    total = 0.0
    for k in range(5):
        train_idx = [i for i in range(n) if fold_of[i] != k]
        test_idx = [i for i in range(n) if fold_of[i] == k]
        tau_k, best = grid[0], -1.0
        for tau in grid:
            sub = [(labels[i], apply_reject(scores_list[i], tau)) for i in train_idx]
            support = Counter(g for g, _ in sub)
            f1s = [prf(sub, lab)[2] for lab in ALL_LABELS if support.get(lab)]
            f = sum(f1s) / len(f1s) if f1s else 0.0
            if f > best:
                tau_k, best = tau, f
        sub = [(labels[i], apply_reject(scores_list[i], tau_k)) for i in test_idx]
        support = Counter(g for g, _ in sub)
        f1s = [prf(sub, lab)[2] for lab in ALL_LABELS if support.get(lab)]
        total += sum(f1s) / len(f1s) if f1s else 0.0
    return best_tau, total / 5.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labeled", default=str(TEACHER), help="teacher-labelled jsonl")
    ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--methods", default="centroid,softmax,ensemble")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    gold = read_jsonl(Path(args.gold))
    teacher = read_jsonl(Path(args.labeled))
    gold_texts = {norm(r["text"]) for r in gold}
    train_rows = [r for r in teacher if norm(r["text"]) not in gold_texts]
    print(f"gold={len(gold)} teacher={len(teacher)} train(no gold overlap)={len(train_rows)}")

    labels = [gold_label(r) for r in gold]
    gold_by_video = Counter(r["bvid"] for r in gold)
    print("gold per video:", dict(gold_by_video))
    print("train per video:", dict(Counter(r["bvid"] for r in train_rows)))

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    kind_scores: dict[str, list[dict[str, float]]] = {}
    for kind in ("centroid", "softmax"):
        if kind in methods or "ensemble" in methods:
            kind_scores[kind] = predict_scores(train_models(train_rows, kind), gold)

    report = {"labeled": args.labeled, "train_rows": len(train_rows),
              "gold_rows": len(gold), "methods": {}}
    argmax_macro = None
    for method in methods:
        if method == "ensemble":
            scores = [
                normalized_scores({k: (kind_scores["centroid"][i].get(k, 0.0) +
                                      kind_scores["softmax"][i].get(k, 0.0)) / 2.0
                                 for k in ALL_LABELS})
                for i in range(len(gold))
            ]
        else:
            scores = [normalized_scores(s) for s in kind_scores[method]]
        preds = [apply_reject(s, 0.0) for s in scores]
        pair = list(zip(labels, preds))
        m = metrics(pair)
        if argmax_macro is None:
            argmax_macro = m["macro_f1"]
        grid = [round(0.10 + 0.05 * k, 2) for k in range(0, 17)]
        tau, cv_macro = tune_tau(scores, labels, grid)
        tuned = [apply_reject(s, tau) for s in scores]
        mt = metrics(list(zip(labels, tuned)))
        report["methods"][method] = {"argmax": m, "reject_tau": tau,
                                     "reject_cv_macro_f1": round(cv_macro, 4),
                                     "reject_full": mt}
        print()
        print(f"[{method}] argmax: acc={m['accuracy']:.4f} macroF1={m['macro_f1']:.4f} "
              f"weighted={m['weighted_f1']:.4f} filterF1={m['filter']['f1']:.4f} "
              f"P={m['filter']['precision']:.3f} R={m['filter']['recall']:.3f}")
        print(f"          per-label F1:", {k: v["f1"] for k, v in m["per_label"].items()})
        print(f"[{method}] reject tau={tau}: acc={mt['accuracy']:.4f} macroF1={mt['macro_f1']:.4f} "
              f"cv_macroF1={cv_macro:.4f} filterF1={mt['filter']['f1']:.4f}")
        print(f"          per-label F1:", {k: v["f1"] for k, v in mt["per_label"].items()})

    out = Path(args.out) if args.out else REPO / "data" / "experiments" / "results" / "f1_bench.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())