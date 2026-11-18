"""Presentation policy: turn tags into show / hide / collapse.

Kept deliberately pure and classifier-free so both the local server and the
userscript can implement the same rules and be tested in isolation.
See docs/design/contracts.md §5.
"""
from __future__ import annotations

import copy

from .taxonomy import CATEGORY_IDS, FALLBACK_ID

ACTIONS = ("show", "hide", "collapse")

DEFAULT_PREFS: dict = {
    "version": 1,
    "enabled": True,
    "min_confidence": 0.3,
    "per_type": {
        "information": "show",
        "humor": "show",
        "emotional_resonance": "show",
        "irrelevant_repetition": "collapse",
        "attack": "hide",
        "conflict": "hide",
        "spoiler": "hide",
        "political": "show",
        "inflammatory": "hide",
        "other": "show",
    },
    "density": {"window_seconds": 10, "max_visible": 30, "exceed": "hide"},
}


def normalize_prefs(prefs: dict | None) -> dict:
    """Merge a (possibly partial or untrusted) prefs dict onto the defaults."""
    merged = copy.deepcopy(DEFAULT_PREFS)
    if not isinstance(prefs, dict):
        return merged
    if "enabled" in prefs:
        merged["enabled"] = bool(prefs["enabled"])
    try:
        merged["version"] = int(prefs.get("version", merged["version"]))
    except (TypeError, ValueError):
        pass
    try:
        merged["min_confidence"] = min(1.0, max(0.0, float(prefs.get("min_confidence", 0.3))))
    except (TypeError, ValueError):
        pass
    per_type = prefs.get("per_type")
    if isinstance(per_type, dict):
        for label, action in per_type.items():
            if label in merged["per_type"] and action in ACTIONS:
                merged["per_type"][label] = action
    density = prefs.get("density")
    if isinstance(density, dict):
        for key in ("window_seconds", "max_visible"):
            try:
                value = int(density.get(key))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                merged["density"][key] = value
        if density.get("exceed") in ("hide", "collapse"):
            merged["density"]["exceed"] = density["exceed"]
    return merged


def resolve_item(item: dict | None, override: dict | None) -> dict | None:
    """Apply a user label correction on top of a cached item (highest priority)."""
    if not isinstance(override, dict):
        return item
    label = override.get("label")
    if label not in CATEGORY_IDS and label != FALLBACK_ID:
        return item
    resolved = dict(item or {})
    resolved["dominant"] = label
    resolved["tags"] = {label: 1.0}
    resolved["score"] = 1.0
    resolved["source"] = "user"
    resolved["evidence"] = override.get("evidence") or "user correction"
    return resolved


def decide(item: dict | None, prefs: dict | None = None) -> dict:
    """Return {"action", "label", "score", "reason"} for one cached item."""
    normalized = normalize_prefs(prefs)
    if not item:
        return {"action": "show", "label": FALLBACK_ID, "score": 0.0, "reason": "not_tagged"}

    tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    label = item.get("dominant")
    if label not in CATEGORY_IDS and label != FALLBACK_ID:
        label = max(tags, key=lambda key: float(tags[key])) if tags else FALLBACK_ID
    try:
        score = float(item.get("score", tags.get(label, 0.0)))
    except (TypeError, ValueError):
        score = 0.0

    if not normalized["enabled"]:
        return {"action": "show", "label": label, "score": score, "reason": "disabled"}
    if score < normalized["min_confidence"]:
        return {"action": "show", "label": label, "score": score, "reason": "low_confidence"}

    action = normalized["per_type"].get(label, "show")
    reason = "default_show" if action == "show" else f"pref_{action}_{label}"
    return {"action": action, "label": label, "score": score, "reason": reason}
