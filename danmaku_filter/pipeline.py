"""Offline precompute: fetch every danmaku segment, label it, cache the result.

The runtime server must never call the network or an LLM (contracts.md §4), so
all expensive labelling happens here and lands in ``data/tags/<bvid>.json`` as
schema v2. The HTTP service only reads that file (plus user overrides).

A failed run never leaves a half-written cache: the file is written once, at
the very end, through `cache.save_tags` (temporary file + os.replace).
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import cache, dmproto, fetch
from .classify import get_classifier
from .taxonomy import CATEGORY_IDS, FALLBACK_ID
from .types import Danmaku

SCHEMA_VERSION = 2
CLASSIFY_BATCH = 100
KNOWN_LABELS = set(CATEGORY_IDS) | {FALLBACK_ID}


def _now_iso() -> str:
    """Current UTC time in the ``2026-10-21T12:00:00Z`` form used by schema v2."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_classifier(backend: str, video_context: str):
    """Build the classifier and resolve the LLM model name before any I/O.

    ``classify.llm.settings()`` raises when the API key is missing, so a
    misconfigured LLM run fails before the network is touched and before the
    cache directory is created.
    """
    if backend == "llm":
        from .classify import llm

        _, _, model = llm.settings()
        return get_classifier("llm", video_context=video_context), model
    return get_classifier(backend), None


def _collect(segments: list[bytes]) -> list:
    """Parse every segment and deduplicate by dmid, preserving first-seen order."""
    items = []
    seen: set[str] = set()
    for segment in segments:
        for danmaku in dmproto.parse_segment(segment):
            dmid = str(getattr(danmaku, "dmid", "") or "")
            if not dmid or dmid in seen:
                continue
            seen.add(dmid)
            items.append(danmaku)
    return items


def _clamp_score(value: object) -> float:
    """Coerce a model score into [0, 1]; unusable values become 0.0."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, score))


def _build_item(danmaku, result) -> dict:
    """Convert (Danmaku, ClassificationResult) into one schema v2 item."""
    raw_tags = getattr(result, "tags", None)
    tags: dict[str, float] = {}
    if isinstance(raw_tags, dict):
        for label, score in raw_tags.items():
            if isinstance(label, str) and label in KNOWN_LABELS:
                tags[label] = _clamp_score(score)
    label = str(getattr(result, "label", "") or "")
    if label not in KNOWN_LABELS:
        label = FALLBACK_ID
    if label not in tags:
        tags[label] = _clamp_score(getattr(result, "confidence", 0.0))
    return {
        "text": str(getattr(danmaku, "text", "")),
        "time_ms": int(getattr(danmaku, "time_ms", 0) or 0),
        "mode": int(getattr(danmaku, "mode", 1) or 1),
        "tags": tags,
        "dominant": label,
        "score": tags[label],
        "evidence": str(getattr(result, "rationale", "") or ""),
        "source": str(getattr(result, "source", "rules") or "rules"),
    }


def _classify(classifier, items: list) -> list:
    """Label items in bounded batches so huge videos stay memory-friendly."""
    results = []
    for start in range(0, len(items), CLASSIFY_BATCH):
        batch = items[start : start + CLASSIFY_BATCH]
        batch_results = classifier.classify_batch(batch)
        if len(batch_results) != len(batch):
            raise RuntimeError(
                f"classifier returned {len(batch_results)} labels for {len(batch)} items"
            )
        results.extend(batch_results)
    return results


def precompute(
    bvid: str,
    backend: str = "rules",
    tags_dir: str = "data/tags",
    limit: int = 0,
    video_context: str = "",
    max_segments: int = 60,
) -> dict:
    """Fetch all segments, label the danmaku and write one schema v2 cache file.

    ``limit=0`` labels every unique dmid; a positive limit caps how many are
    labelled (the summary still reports the full unique total). The returned
    summary is the object the CLI prints as one JSON line.
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    classifier, model = _make_classifier(backend, video_context)

    info = fetch.get_video_info(bvid)
    aid = int(info["aid"])
    cid = int(info["cid"])
    duration = int(info.get("duration") or 0)
    segments = fetch.fetch_all_segments(aid, cid, duration, max_segments=max_segments)

    danmaku = _collect(segments)
    total = len(danmaku)
    if limit:
        danmaku = danmaku[:limit]
    results = _classify(classifier, danmaku)
    items = {
        str(danmaku_item.dmid): _build_item(danmaku_item, result)
        for danmaku_item, result in zip(danmaku, results)
    }

    path = cache.tags_path(bvid, tags_dir)
    cache.save_tags(
        bvid,
        {
            "schema": SCHEMA_VERSION,
            "bvid": bvid,
            "aid": aid,
            "cid": cid,
            "duration": duration,
            "segments": len(segments),
            "backend": backend,
            "model": model,
            "created": _now_iso(),
            "items": items,
        },
        tags_dir,
    )
    return {
        "bvid": bvid,
        "aid": aid,
        "cid": cid,
        "segments": len(segments),
        "total": total,
        "labeled": len(items),
        "backend": backend,
        "model": model,
        "path": str(path),
    }


def _dominant_counts(items: dict) -> dict[str, int]:
    """Count items per dominant label, sorted by label for stable output."""
    counts: dict[str, int] = {}
    for item in items.values():
        if not isinstance(item, dict):
            continue
        label = str(item.get("dominant", "") or FALLBACK_ID)
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def reclassify(
    bvid: str,
    backend: str = "rules",
    tags_dir: str = "data/tags",
    limit: int = 0,
    video_context: str = "",
    dry_run: bool = False,
) -> dict:
    """Re-label an existing schema v2 cache in place, without touching the network.

    Reuses the recorded metadata (aid/cid/duration/segments) and the cached
    text/time/mode, so a prompt or backend change can be tried on the same
    comments. limit relabels only the first N cached items and keeps the
    remaining cached labels untouched (useful for a quick quality probe).
    Writes are atomic through cache.save_tags; dry_run reports the would-be
    label distribution without saving anything.
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    payload = cache.load_tags(bvid, tags_dir)
    if not payload:
        raise FileNotFoundError(f"no tag cache for {bvid} in {tags_dir}")
    raw_items = payload.get("items")
    if not isinstance(raw_items, dict) or not raw_items:
        raise ValueError(f"tag cache for {bvid} has no items")

    classifier, model = _make_classifier(backend, video_context)

    dmids = list(raw_items.keys())
    if limit:
        dmids = dmids[:limit]
    danmaku = []
    for dmid in dmids:
        item = raw_items.get(dmid)
        if not isinstance(item, dict):
            continue
        danmaku.append(
            Danmaku(
                text=str(item.get("text", "")),
                time_ms=int(item.get("time_ms", 0) or 0),
                mode=int(item.get("mode", 1) or 1),
                dmid=str(dmid),
            )
        )
    results = _classify(classifier, danmaku)
    fresh = {
        str(danmaku_item.dmid): _build_item(danmaku_item, result)
        for danmaku_item, result in zip(danmaku, results)
    }

    # Keep the original item order; items beyond limit keep their cached labels.
    items = {
        str(dmid): fresh.get(str(dmid), item) for dmid, item in raw_items.items()
    }
    previous = {"backend": payload.get("backend"), "model": payload.get("model")}
    payload.update(
        {
            "schema": SCHEMA_VERSION,
            "backend": backend,
            "model": model,
            "created": _now_iso(),
            "relabeled_from": previous,
            "items": items,
        }
    )
    path = cache.tags_path(bvid, tags_dir)
    if not dry_run:
        cache.save_tags(bvid, payload, tags_dir)
    return {
        "bvid": bvid,
        "backend": backend,
        "model": model,
        "total": len(raw_items),
        "labeled": len(fresh),
        "dry_run": dry_run,
        "path": str(path),
        "was": previous,
        "distribution": _dominant_counts(items),
    }
