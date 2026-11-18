"""Per-label precision / recall / F1 over a gold set.

Predictions are joined to gold rows by normalized comment text, so duplicate
texts collapse to their last prediction. That is fine for the pilot set; the
annotation tool should eventually carry stable danmaku ids.
"""
from __future__ import annotations

from typing import Iterable


def prf(gold: Iterable[dict], pred: Iterable[dict], labels: Iterable[str]) -> dict:
    pred_by_text: dict[str, str] = {}
    for row in pred:
        pred_by_text[str(row["text"]).strip()] = str(row["label"])

    pairs: list[tuple[str, str, str]] = []
    unmatched = 0
    for row in gold:
        key = str(row["text"]).strip()
        if key in pred_by_text:
            pairs.append((key, str(row["label"]), pred_by_text[key]))
        else:
            unmatched += 1

    label_list = list(labels)
    per_label = {}
    for label in label_list:
        tp = sum(1 for _, gold_label, pred_label in pairs if gold_label == label and pred_label == label)
        fp = sum(1 for _, gold_label, pred_label in pairs if gold_label != label and pred_label == label)
        fn = sum(1 for _, gold_label, pred_label in pairs if gold_label == label and pred_label != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }

    correct = sum(1 for _, gold_label, pred_label in pairs if gold_label == pred_label)
    macro_f1 = sum(item["f1"] for item in per_label.values()) / len(label_list) if label_list else 0.0
    return {
        "n": len(pairs),
        "unmatched": unmatched,
        "accuracy": round(correct / len(pairs), 4) if pairs else 0.0,
        "macro_f1": round(macro_f1, 4),
        "per_label": per_label,
    }
