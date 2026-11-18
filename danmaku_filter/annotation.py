"""Pilot annotation utilities: stratified sampling, coding CSVs, Cohen kappa.

The pilot study needs (a) a sample that exercises every category, not just the
dominant one, and (b) an inter-coder agreement statistic computed offline from
two coders CSV files. Both pieces are pure functions so the code that produces
the numbers is unit-tested; the thin wrappers live in scripts/.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Iterable, Sequence

from .taxonomy import CATEGORY_IDS, FALLBACK_ID

LABELS = tuple(CATEGORY_IDS) + (FALLBACK_ID,)

# Quotas for a 120-item pilot sheet; absent labels fall back to the pool.
DEFAULT_QUOTAS = {
    "other": 30,
    "information": 26,
    "humor": 22,
    "emotional_resonance": 14,
    "irrelevant_repetition": 14,
    "spoiler": 10,
    "attack": 4,
    "conflict": 0,
    "political": 8,
    "inflammatory": 8,
}


def load_items(cache_payload: dict) -> dict[str, dict]:
    """Return {dmid: item} from a schema v2 cache payload."""
    items = cache_payload.get("items") if isinstance(cache_payload, dict) else None
    if not isinstance(items, dict):
        raise ValueError("cache payload has no items dict")
    return {str(dmid): item for dmid, item in items.items() if isinstance(item, dict)}


def stratified_sample(
    items: dict[str, dict],
    n: int,
    seed: int = 7,
    quotas: dict[str, int] | None = None,
) -> list[tuple[str, dict]]:
    """Pick ``n`` items, filling per-label quotas first, then any leftovers.

    Quotas make sure rare categories (attack, spoiler) appear even though the
    cache is dominated by ``other``. Missing categories are compensated from the
    remaining pool, so the result always has exactly ``min(n, len(items))`` rows.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    quotas = dict(DEFAULT_QUOTAS if quotas is None else quotas)
    for label in quotas:
        if label not in LABELS:
            raise ValueError(f"unknown label in quotas: {label}")
    rng = random.Random(seed)

    by_label: dict[str, list[str]] = {label: [] for label in LABELS}
    for dmid, item in items.items():
        label = str(item.get("dominant", "") or FALLBACK_ID)
        by_label.setdefault(label if label in LABELS else FALLBACK_ID, []).append(dmid)

    picked: list[str] = []
    for label in LABELS:
        candidates = by_label.get(label, [])
        rng.shuffle(candidates)
        take = min(quotas.get(label, 0), len(candidates))
        picked.extend(candidates[:take])
        by_label[label] = candidates[take:]

    if len(picked) < n:
        rest = [dmid for label in LABELS for dmid in by_label.get(label, [])]
        rng.shuffle(rest)
        picked.extend(rest[: n - len(picked)])
    rng.shuffle(picked)
    return [(dmid, items[dmid]) for dmid in picked[:n]]


def write_coder_sheet(path: str | Path, rows: Sequence[dict], label_col: str = "label") -> None:
    """Write one coder sheet: item_id,text,label,note with label/note blank."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["item_id", "text", label_col, "note"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "item_id": row["item_id"],
                    "text": row["text"],
                    label_col: "",
                    "note": "",
                }
            )


def read_labels(path: str | Path, label_col: str = "label", key: str = "item_id") -> dict[str, str]:
    """Read {item_id: label} from a coded CSV, skipping blank rows."""
    labels: dict[str, str] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            item = str(row.get(key, "") or "").strip()
            label = str(row.get(label_col, "") or "").strip()
            if item and label:
                labels[item] = label
    return labels


def coded_pairs(
    labels_a: dict[str, str], labels_b: dict[str, str]
) -> list[tuple[str, str]]:
    """Pairs for items both coders labelled, in stable item order."""
    return [(labels_a[key], labels_b[key]) for key in sorted(labels_a) if key in labels_b]


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> dict:
    """Nominal (unweighted) Cohen kappa plus observed/expected agreement.

    Returns n, observed agreement, expected agreement, kappa, per-label counts
    and a confusion dict (coder A label -> coder B label -> count). ``kappa`` is
    None when there is nothing to score; degenerate cases where expected
    agreement is 1.0 (all coders used one identical label) report kappa=1.0.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "observed": None, "expected": None, "kappa": None, "per_label": {}, "confusion": {}}
    agree = sum(1 for a, b in pairs if a == b)
    observed = agree / n
    counts_a: dict[str, int] = {}
    counts_b: dict[str, int] = {}
    confusion: dict[str, dict[str, int]] = {}
    for a, b in pairs:
        counts_a[a] = counts_a.get(a, 0) + 1
        counts_b[b] = counts_b.get(b, 0) + 1
        row = confusion.setdefault(a, {})
        row[b] = row.get(b, 0) + 1
    expected = sum(counts_a[label] * counts_b.get(label, 0) for label in counts_a) / (n * n)
    kappa = 1.0 if expected >= 1.0 else (observed - expected) / (1.0 - expected)
    return {
        "n": n,
        "observed": round(observed, 4),
        "expected": round(expected, 4),
        "kappa": round(kappa, 4),
        "per_label": {
            label: {"a": counts_a.get(label, 0), "b": counts_b.get(label, 0)}
            for label in sorted(set(counts_a) | set(counts_b))
        },
        "confusion": confusion,
    }


def disagreements(
    labels_a: dict[str, str], labels_b: dict[str, str]
) -> list[dict]:
    """Items the two coders disagree on, for the adjudication meeting."""
    rows = []
    for key in sorted(labels_a):
        if key in labels_b and labels_a[key] != labels_b[key]:
            rows.append({"item_id": key, "coder_a": labels_a[key], "coder_b": labels_b[key]})
    return rows


def validate_labels(labels: Iterable[str]) -> list[str]:
    """Return labels outside the frozen taxonomy (typos must be fixed)."""
    return sorted({label for label in labels if label not in LABELS})
