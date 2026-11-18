"""Canonical F1 / harm evaluation for the danmaku taggers (no train-test leakage).

Gold comments are removed from the teacher rows before training, because the
gold set was sampled from the same pools as the teacher sample and otherwise
the evaluation is measuring memorisation of the teacher labels.

Reports, for each method:

* fine macro-F1 over the labels that occur in the gold set, plus weighted F1,
  accuracy (micro F1 for single-label data) and bootstrap 95% CIs;
* family macro-F1 over five coarse families (information / positive affect /
  noise / hostile / spoiler);
* cost-weighted accuracy under a confusion-cost matrix that treats adjacent
  labels as cheaper mistakes than distant ones;
* product-level expected harm per 1,000 comments for the hide+fold policy,
  with a false hide weighted 2x a missed removal by default;
* a reject option (max score below tau becomes other), tuned by 5-fold CV over
  the gold set for either macro-F1 or expected harm.

Usage::

    python3 scripts/f1_eval.py --labeled data/study/teacher_v4.jsonl
    python3 scripts/f1_eval.py --labeled data/study/teacher_sample.jsonl --bootstrap 400
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

GOLD = REPO / "data" / "study" / "gold.jsonl"
TEACHER = REPO / "data" / "study" / "teacher_sample.jsonl"

ALL_LABELS = [
    "information", "humor", "emotional_resonance", "irrelevant_repetition",
    "attack", "conflict", "spoiler", "political", "inflammatory", "other",
]

FAMILY = {
    "information": "information",
    "humor": "positive",
    "emotional_resonance": "positive",
    "irrelevant_repetition": "noise",
    "other": "noise",
    "attack": "hostile",
    "conflict": "hostile",
    "inflammatory": "hostile",
    "spoiler": "spoiler",
    "political": "political",
}
FAMILIES = ["information", "positive", "noise", "hostile", "spoiler", "political"]

# Cost of predicting column when the gold label is the row, 0 = free.
COSTS = {
    ("humor", "emotional_resonance"): 0.2,
    ("emotional_resonance", "humor"): 0.2,
    ("humor", "information"): 0.5,
    ("information", "humor"): 0.5,
    ("emotional_resonance", "information"): 0.5,
    ("information", "emotional_resonance"): 0.5,
    ("humor", "other"): 0.4,
    ("other", "humor"): 0.4,
    ("emotional_resonance", "other"): 0.4,
    ("other", "emotional_resonance"): 0.4,
    ("irrelevant_repetition", "other"): 0.2,
    ("other", "irrelevant_repetition"): 0.2,
    ("irrelevant_repetition", "humor"): 0.3,
    ("humor", "irrelevant_repetition"): 0.3,
    ("attack", "conflict"): 0.2,
    ("conflict", "attack"): 0.2,
    ("attack", "inflammatory"): 0.2,
    ("inflammatory", "attack"): 0.2,
    ("conflict", "inflammatory"): 0.2,
    ("inflammatory", "conflict"): 0.2,
    ("attack", "humor"): 0.8,
    ("humor", "attack"): 0.8,
    ("conflict", "humor"): 0.6,
    ("humor", "conflict"): 0.6,
    ("inflammatory", "humor"): 0.7,
    ("humor", "inflammatory"): 0.7,
    ("attack", "information"): 0.8,
    ("information", "attack"): 0.8,
    ("spoiler", "humor"): 0.5,
    ("humor", "spoiler"): 0.5,
    ("spoiler", "information"): 0.6,
    ("information", "spoiler"): 0.6,
    ("political", "information"): 0.4,
    ("information", "political"): 0.4,
    ("political", "conflict"): 0.4,
    ("conflict", "political"): 0.4,
}
DEFAULT_COST = 0.6

# Product policy used by the C condition: these labels are hidden or folded.
REMOVE = {"attack", "conflict", "inflammatory", "irrelevant_repetition", "spoiler"}


def norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gold_label(row: dict) -> str:
    return str(row.get("label") or row.get("dominant"))


def cost(gold: str, pred: str) -> float:
    if gold == pred:
        return 0.0
    return COSTS.get((gold, pred), DEFAULT_COST)


def prf(pairs, label):
    tp = sum(1 for g, p in pairs if g == label and p == label)
    fp = sum(1 for g, p in pairs if g != label and p == label)
    fn = sum(1 for g, p in pairs if g == label and p != label)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return prec, rec, (2 * prec * rec / (prec + rec) if prec + rec else 0.0), tp + fn


def summarize(pairs, w_miss: float = 1.0, w_false_hide: float = 2.0) -> dict:
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
    fam_support = Counter(FAMILY[g] for g, _ in pairs)
    fam_labels = [f for f in FAMILIES if fam_support.get(f)]
    fam_f1 = []
    for fam in fam_labels:
        tp = sum(1 for g, p in pairs if FAMILY[g] == fam and FAMILY[p] == fam)
        fp = sum(1 for g, p in pairs if FAMILY[g] != fam and FAMILY[p] == fam)
        fn = sum(1 for g, p in pairs if FAMILY[g] == fam and FAMILY[p] != fam)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        fam_f1.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    fam_macro = sum(fam_f1) / len(fam_f1) if fam_f1 else 0.0
    mean_cost = sum(cost(g, p) for g, p in pairs) / n if n else 0.0
    tp = sum(1 for g, p in pairs if g in REMOVE and p in REMOVE)
    fp = sum(1 for g, p in pairs if g not in REMOVE and p in REMOVE)
    fn = sum(1 for g, p in pairs if g in REMOVE and p not in REMOVE)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    filter_f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    harm = (w_miss * fn + w_false_hide * fp) / n * 1000 if n else 0.0
    confusion = Counter((g, p) for g, p in pairs if g != p)
    return {
        "n": n,
        "accuracy": round(sum(1 for g, p in pairs if g == p) / n, 4) if n else 0.0,
        "macro_f1": round(macro, 4),
        "weighted_f1": round(weighted, 4),
        "family_macro_f1": round(fam_macro, 4),
        "mean_cost": round(mean_cost, 4),
        "cost_weighted_accuracy": round(1.0 - mean_cost, 4),
        "per_label": per,
        "filter": {"precision": round(prec, 4), "recall": round(rec, 4),
                   "f1": round(filter_f1, 4), "tp": tp, "fp": fp, "fn": fn},
        "harm_per_1000": round(harm, 2),
        "top_confusions": [
            {"gold": g, "pred": p, "count": c}
            for (g, p), c in confusion.most_common(8)
        ],
    }


def bootstrap_ci(pairs, metric, rounds: int, seed: int = 20261118):
    """Percentile bootstrap CI for a scalar metric over the gold rows."""
    if rounds <= 0 or not pairs:
        return None
    rng = random.Random(seed)
    n = len(pairs)
    values = []
    for _ in range(rounds):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        try:
            values.append(metric(sample))
        except (ValueError, ZeroDivisionError):
            continue
    if not values:
        return None
    values.sort()
    lo = values[int(0.025 * (len(values) - 1))]
    hi = values[int(0.975 * (len(values) - 1))]
    return [round(lo, 4), round(hi, 4)]


def normalized_scores(scores: dict) -> dict:
    total = sum(max(0.0, v) for v in scores.values())
    if total <= 0:
        return {k: 1.0 / len(scores) for k in scores}
    return {k: max(0.0, v) / total for k, v in scores.items()}


def train_pooled(rows, kind):
    model = LinearTagger(kind=kind)
    model.train([r["text"] for r in rows], [gold_label(r) for r in rows])
    return model


def score_gold_pooled(model, gold):
    return model.predict_scores([row["text"] for row in gold])


def train_per_video(rows, kind):
    by_video = defaultdict(list)
    for row in rows:
        by_video[row["bvid"]].append(row)
    models = {}
    for bvid, vid_rows in by_video.items():
        model = LinearTagger(kind=kind)
        model.train([r["text"] for r in vid_rows], [gold_label(r) for r in vid_rows])
        models[bvid] = model
    return models


def score_gold(models, gold):
    out = [None] * len(gold)
    by_video = defaultdict(list)
    for idx, row in enumerate(gold):
        by_video[row["bvid"]].append(idx)
    for bvid, idxs in by_video.items():
        model = models.get(bvid)
        if model is None:
            continue
        scored = model.predict_scores([gold[i]["text"] for i in idxs])
        for i, scores in zip(idxs, scored):
            out[i] = scores
    return out


def decide(scores: dict, tau: float) -> str:
    best = max(scores, key=lambda k: scores[k])
    return "other" if scores[best] < tau else best


def tune_tau(scores_list, labels, objective, grid):
    """Pick tau on the full gold set and report the 5-fold CV score."""
    def score_at(tau, idxs):
        pairs = [(labels[i], decide(scores_list[i], tau)) for i in idxs]
        summary = summarize(pairs)
        if objective == "harm":
            return -summary["harm_per_1000"]
        return summary["macro_f1"]

    best_tau = max(grid, key=lambda tau: score_at(tau, range(len(labels))))
    n = len(labels)
    total = 0.0
    for k in range(5):
        train_idx = [i for i in range(n) if i % 5 != k]
        test_idx = [i for i in range(n) if i % 5 == k]
        tau = max(grid, key=lambda t: score_at(t, train_idx))
        total += score_at(tau, test_idx)
    return best_tau, total / 5


def policy_counts(scores_list, labels, gamma, w_miss: float = 1.0, w_false_hide: float = 2.0):
    """Hide comments whose unwanted-probability mass reaches gamma."""
    tp = fp = fn = 0
    for scores, gold in zip(scores_list, labels):
        p_unwanted = sum(scores.get(lab, 0.0) for lab in REMOVE)
        hide = p_unwanted >= gamma
        gold_hide = gold in REMOVE
        if gold_hide and hide:
            tp += 1
        elif gold_hide:
            fn += 1
        elif hide:
            fp += 1
    n = len(labels)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    harm = (w_miss * fn + w_false_hide * fp) / n * 1000 if n else 0.0
    return {"gamma": gamma, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "harm_per_1000": round(harm, 2)}


def tune_gamma(scores_list, labels, grid, w_miss: float = 1.0, w_false_hide: float = 2.0):
    """Pick the hide threshold on all rows and report the 5-fold CV harm."""
    def harm_at(gamma, idxs):
        counts = policy_counts([scores_list[i] for i in idxs],
                               [labels[i] for i in idxs], gamma, w_miss, w_false_hide)
        return counts["harm_per_1000"]

    best = min(grid, key=lambda g: harm_at(g, range(len(labels))))
    n = len(labels)
    total = 0.0
    for k in range(5):
        train_idx = [i for i in range(n) if i % 5 != k]
        test_idx = [i for i in range(n) if i % 5 == k]
        gamma = min(grid, key=lambda g: harm_at(g, train_idx))
        total += harm_at(gamma, test_idx)
    return best, total / 5


def harm_from_pairs(pairs, w_miss: float = 1.0, w_false_hide: float = 2.0) -> float:
    """Harm per 1,000 comments if every prediction in REMOVE is hidden."""
    n = len(pairs)
    if not n:
        return 0.0
    fn = fp = 0
    for gold, pred in pairs:
        gold_hide = gold in REMOVE
        pred_hide = pred in REMOVE
        if gold_hide and not pred_hide:
            fn += 1
        elif pred_hide and not gold_hide:
            fp += 1
    return (w_miss * fn + w_false_hide * fp) / n * 1000


def harm_curve(pairs, weights=(0.25, 0.5, 1.0, 2.0, 4.0)) -> list[dict]:
    """Compare hiding at the argmax operating point against showing everything."""
    n = len(pairs)
    n_pos = sum(1 for gold, _ in pairs if gold in REMOVE)
    rows = []
    for w in weights:
        filter_harm = harm_from_pairs(pairs, w_false_hide=w)
        show_harm = 1.0 * n_pos / n * 1000 if n else 0.0
        rows.append({"w_false_hide": w, "filter": round(filter_harm, 1),
                     "show_all": round(show_harm, 1), "delta": round(filter_harm - show_harm, 1)})
    return rows


def evaluate_method(name, scores_list, labels, tau_objective, bootstrap, grid, dev_idx=None, test_idx=None):
    """Tune the reject and policy thresholds on dev_idx, report on test_idx."""
    rep_idx = list(test_idx) if test_idx is not None else list(range(len(labels)))
    tune_idx = list(dev_idx) if dev_idx is not None else list(range(len(labels)))
    rep_scores = [scores_list[i] for i in rep_idx]
    rep_labels = [labels[i] for i in rep_idx]
    tune_scores = [scores_list[i] for i in tune_idx]
    tune_labels = [labels[i] for i in tune_idx]

    argmax_pairs = [(rep_labels[i], decide(rep_scores[i], 0.0)) for i in range(len(rep_labels))]
    base = summarize(argmax_pairs)
    tau, cv = tune_tau(tune_scores, tune_labels, tau_objective, grid)
    tuned_pairs = [(rep_labels[i], decide(rep_scores[i], tau)) for i in range(len(rep_labels))]
    tuned = summarize(tuned_pairs)
    result = {
        "report_rows": len(rep_idx),
        "tune_rows": len(tune_idx),
        "argmax": base,
        "tau_objective": tau_objective,
        "tau": tau,
        "reject_cv": round(cv, 4),
        "reject": tuned,
    }
    if bootstrap > 0:
        result["ci"] = {
            "macro_f1": bootstrap_ci(argmax_pairs, lambda p: summarize(p)["macro_f1"], bootstrap),
            "harm_per_1000": bootstrap_ci(argmax_pairs, lambda p: summarize(p)["harm_per_1000"], bootstrap),
            "reject_macro_f1": bootstrap_ci(tuned_pairs, lambda p: summarize(p)["macro_f1"], bootstrap),
            "reject_harm_per_1000": bootstrap_ci(tuned_pairs, lambda p: summarize(p)["harm_per_1000"], bootstrap),
        }
    print(f"[{name}] n={len(rep_idx)} argmax acc={base['accuracy']:.3f} macroF1={base['macro_f1']:.3f} "
          f"weighted={base['weighted_f1']:.3f} family={base['family_macro_f1']:.3f} "
          f"cost={base['mean_cost']:.3f} harm={base['harm_per_1000']:.0f}/1k filterF1={base['filter']['f1']:.3f}")
    print(f"         tau={tau} ({tau_objective}, cv={cv:.3f}) -> acc={tuned['accuracy']:.3f} "
          f"macroF1={tuned['macro_f1']:.3f} weighted={tuned['weighted_f1']:.3f} "
          f"family={tuned['family_macro_f1']:.3f} harm={tuned['harm_per_1000']:.0f}/1k")
    print(f"         per-label:", {k: v["f1"] for k, v in tuned["per_label"].items()})
    print(f"         top confusions:", [(c["gold"], c["pred"], c["count"]) for c in tuned["top_confusions"][:5]])
    show_all = policy_counts(rep_scores, rep_labels, 2.0)
    gamma, cv_harm = tune_gamma(tune_scores, tune_labels, grid)
    policy = policy_counts(rep_scores, rep_labels, gamma)
    harm_mild = policy_counts(rep_scores, rep_labels, gamma, w_miss=1.0, w_false_hide=1.0)["harm_per_1000"]
    harm_harsh = policy_counts(rep_scores, rep_labels, gamma, w_miss=1.0, w_false_hide=4.0)["harm_per_1000"]
    curve = harm_curve(argmax_pairs)
    result["harm_curve"] = curve
    print("         harm/1k by false-hide weight (argmax filter vs show-all):",
          " | ".join(f"w={r['w_false_hide']}: {r['filter']:.0f} vs {r['show_all']:.0f}" for r in curve))
    result["policy"] = {"show_all": show_all, "gamma": gamma,
                        "cv_harm": round(cv_harm, 2), "at_gamma": policy,
                        "harm_w1": harm_mild, "harm_w4": harm_harsh}
    break_even = 2.0 / 3.0
    print(f"         break-even precision at w_false_hide=2 is {break_even:.2f}; current P={policy['precision']:.2f}")
    print(f"         policy(gamma={gamma}, cv harm={cv_harm:.0f}) P={policy['precision']:.3f} "
          f"R={policy['recall']:.3f} F1={policy['f1']:.3f} harm={policy['harm_per_1000']:.0f}/1k "
          f"(w=1: {harm_mild:.0f}, w=4: {harm_harsh:.0f}) | show_all harm={show_all['harm_per_1000']:.0f}/1k")
    if bootstrap > 0:
        print(f"         CI argmax macroF1={result['ci']['macro_f1']} harm={result['ci']['harm_per_1000']}")
        print(f"         CI reject macroF1={result['ci']['reject_macro_f1']} harm={result['ci']['reject_harm_per_1000']}")
    return result


def split_gold(gold: list[dict], fraction: float, seed: int = 20261118) -> tuple[list[int], list[int]]:
    """Stratified dev/test split of the gold set by label."""
    rng = random.Random(seed)
    by_label: dict[str, list[int]] = {}
    for i, row in enumerate(gold):
        by_label.setdefault(gold_label(row), []).append(i)
    dev: list[int] = []
    test: list[int] = []
    for idxs in by_label.values():
        shuffled = list(idxs)
        rng.shuffle(shuffled)
        cut = int(round(len(shuffled) * fraction))
        dev.extend(shuffled[:cut])
        test.extend(shuffled[cut:])
    return sorted(dev), sorted(test)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labeled", default=str(TEACHER),
                    help="one jsonl path, or a comma separated list merged by (bvid, text)")
    ap.add_argument("--gold", default=str(GOLD))
    ap.add_argument("--methods", default="centroid,softmax,ensemble")
    ap.add_argument("--tau-objective", default="harm", choices=["harm", "macro_f1"])
    ap.add_argument("--pooled", action="store_true", help="train one global model instead of one per video")
    ap.add_argument("--use-models", default="", help="evaluate the saved LinearTagger files in this directory")
    ap.add_argument("--split-dev", type=float, default=0.0,
                    help="stratified fraction of gold used to tune tau/gamma; metrics are reported on the rest")
    ap.add_argument("--bootstrap", type=int, default=400)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    gold = read_jsonl(Path(args.gold))
    paths = [p.strip() for p in args.labeled.split(",") if p.strip()]
    teacher: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for path in paths:
        for row in read_jsonl(Path(path)):
            key = (row["bvid"], norm(row["text"]))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            teacher.append(row)
    gold_texts = {norm(r["text"]) for r in gold}
    train_rows = [r for r in teacher if gold_label(r) and norm(r["text"]) not in gold_texts]
    print(f"gold={len(gold)} labeled={len(teacher)} train(deduped)={len(train_rows)}")
    print("train per video:", dict(Counter(r["bvid"] for r in train_rows)))
    labels = [gold_label(r) for r in gold]

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    kind_scores = {}
    if args.use_models:
        from danmaku_filter.classify.linear import LinearTagger

        models_dir = Path(args.use_models)
        loaded = {}
        for bvid in {r["bvid"] for r in gold}:
            path = models_dir / f"{bvid}.json"
            if not path.exists():
                path = models_dir / "_global.json"
            loaded[bvid] = LinearTagger.load(path)
        scores = []
        for row in gold:
            raw = loaded[row["bvid"]].predict_scores([row["text"]])[0]
            scores.append(normalized_scores(raw))
        report = {"gold_rows": len(gold), "use_models": args.use_models,
                  "models": sorted(p.name for p in models_dir.glob("*.json")),
                  "tau_objective": args.tau_objective, "methods": {}}
        label_list = [gold_label(r) for r in gold]
        grid = [round(0.05 + 0.05 * k, 2) for k in range(17)]
        dev_idx = test_idx = None
        if args.split_dev > 0:
            dev_idx, test_idx = split_gold(gold, args.split_dev)
            print(f"tuning on dev n={len(dev_idx)}, reporting on test n={len(test_idx)}")
        report["methods"]["shipped"] = evaluate_method(
            "shipped", scores, label_list, args.tau_objective, args.bootstrap, grid, dev_idx, test_idx
        )
        out = Path(args.out) if args.out else REPO / "data" / "experiments" / "results" / "f1_eval_shipped.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", out)
        return 0

    for kind in ("centroid", "softmax"):
        if kind in methods or "ensemble" in methods:
            if args.pooled:
                kind_scores[kind] = score_gold_pooled(train_pooled(train_rows, kind), gold)
            else:
                kind_scores[kind] = score_gold(train_per_video(train_rows, kind), gold)

    grid = [round(0.05 + 0.05 * k, 2) for k in range(17)]
    dev_idx: list[int] | None = None
    test_idx: list[int] | None = None
    if args.split_dev > 0:
        dev_idx, test_idx = split_gold(gold, args.split_dev)
        print(f"tuning on dev n={len(dev_idx)}, reporting on test n={len(test_idx)}")
    report = {"labeled": args.labeled, "pooled": bool(args.pooled), "split_dev": args.split_dev, "train_rows": len(train_rows),
              "gold_rows": len(gold), "tau_objective": args.tau_objective, "methods": {}}
    for method in methods:
        if method == "ensemble":
            scores = [
                normalized_scores({k: 0.5 * kind_scores["centroid"][i].get(k, 0.0) +
                                   0.5 * kind_scores["softmax"][i].get(k, 0.0)
                                   for k in set(kind_scores["centroid"][i]) | set(kind_scores["softmax"][i])})
                for i in range(len(gold))
            ]
        else:
            scores = [normalized_scores(s) for s in kind_scores[method]]
        report["methods"][method] = evaluate_method(
            method, scores, labels, args.tau_objective, args.bootstrap, grid, dev_idx, test_idx
        )

    out = Path(args.out) if args.out else REPO / "data" / "experiments" / "results" / "f1_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())