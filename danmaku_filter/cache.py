"""Local JSON cache for precomputed tags, user overrides and pending dmids.

See docs/design/contracts.md §1-§3 for the on-disk layout::

    data/tags/<bvid>.json       schema v2 tag cache (the runtime read path)
    data/overrides/<bvid>.json  user label corrections: {"items": {dmid: ...}}
    data/pending/<bvid>.json    dmids seen at runtime but missing from the cache

Writes are atomic: the payload goes to a temporary file in the destination
directory and is then swapped in with `os.replace`, so the HTTP server never
reads a half-written cache. Reads are tolerant: a missing, corrupt or
unexpectedly shaped file is reported as "no data" instead of raising.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_TAGS_DIR = "data/tags"
DEFAULT_OVERRIDES_DIR = "data/overrides"
DEFAULT_PENDING_DIR = "data/pending"

# Bilibili ids are alphanumeric; the pattern also blocks path traversal.
BVID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now_iso() -> str:
    """Current UTC time in the ``2026-10-21T12:00:00Z`` form used by schema v2."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_bvid(bvid: str) -> str:
    if not isinstance(bvid, str) or not BVID_PATTERN.match(bvid):
        raise ValueError(f"invalid bvid: {bvid!r}")
    return bvid


def _read_json(path: Path) -> dict | None:
    """Return the JSON object stored at ``path``, or None when absent/corrupt."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write(path: Path, payload: dict) -> None:
    """Write ``payload`` as pretty UTF-8 JSON, atomically replacing ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    descriptor, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def tags_dir(root: str | Path = DEFAULT_TAGS_DIR) -> Path:
    """Return the tag cache directory for ``root`` (no I/O)."""
    return Path(root)


def tags_path(bvid: str, root: str | Path = DEFAULT_TAGS_DIR) -> Path:
    """Return the tag cache file for one video."""
    return tags_dir(root) / f"{_check_bvid(bvid)}.json"


def load_tags(bvid: str, root: str | Path = DEFAULT_TAGS_DIR) -> dict | None:
    """Load one schema v2 cache file; None when it is missing or unreadable."""
    return _read_json(tags_path(bvid, root))


def save_tags(bvid: str, payload: dict, root: str | Path = DEFAULT_TAGS_DIR) -> None:
    """Atomically persist one schema v2 cache file, creating its directory."""
    if not isinstance(payload, dict):
        raise ValueError("tags payload must be a dict")
    _atomic_write(tags_path(bvid, root), payload)


def _overrides_path(bvid: str, root: str | Path = DEFAULT_OVERRIDES_DIR) -> Path:
    return Path(root) / f"{_check_bvid(bvid)}.json"


def load_overrides(bvid: str, root: str | Path = DEFAULT_OVERRIDES_DIR) -> dict:
    """Load {dmid: {"label", "at", ...}}; a missing/broken file yields {}."""
    payload = _read_json(_overrides_path(bvid, root))
    if not payload:
        return {}
    items = payload.get("items")
    if isinstance(items, dict):
        return dict(items)
    # Tolerate a flat {dmid: {"label": ...}} file from an older prototype.
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, dict) and "label" in value
    }


def save_overrides(bvid: str, overrides: dict, root: str | Path = DEFAULT_OVERRIDES_DIR) -> None:
    """Atomically write overrides as {"items": {dmid: {"label", "at"}}}."""
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be a dict")
    _atomic_write(_overrides_path(bvid, root), {"items": overrides})


def _pending_path(bvid: str, root: str | Path = DEFAULT_PENDING_DIR) -> Path:
    return Path(root) / f"{_check_bvid(bvid)}.json"


def load_pending(bvid: str, root: str | Path = DEFAULT_PENDING_DIR) -> list[str]:
    """Return the queued dmids for a video, oldest first ([] when absent)."""
    payload = _read_json(_pending_path(bvid, root))
    if not payload:
        return []
    values = payload.get("dmids")
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if isinstance(value, (str, int)) and str(value)]


def add_pending(bvid: str, dmids: Iterable[str], root: str | Path = DEFAULT_PENDING_DIR) -> int:
    """Append unseen dmids to the pending queue; return how many were new."""
    path = _pending_path(bvid, root)
    existing = load_pending(bvid, root)
    seen = set(existing)
    merged = list(existing)
    added = 0
    for raw in dmids:
        dmid = str(raw).strip()
        if not dmid or dmid in seen:
            continue
        seen.add(dmid)
        merged.append(dmid)
        added += 1
    if added:
        _atomic_write(path, {"bvid": bvid, "dmids": merged, "updated": _now_iso()})
    return added
