"""Prepare controlled-study stimuli: teacher labels, models, density, latency.

The lab protocol (docs/study/protocol.md) runs a within-subject experiment over
six short videos, three information-type and three entertainment-type, each
carrying its real danmaku stream. This script turns data/study/stimuli.json into
ready-to-run material and writes data/study/report.json:

* make sure the tag cache for each bvid is LLM-teacher labelled (or borrow the
  frozen teacher rows from data/experiments/dataset.jsonl when the live cache
  was polluted by online rules/linear labels);
* train one LinearTagger per video (sample 200 teacher rows) into
  data/study/models/<bvid>.json;
* measure comment density (comments per minute, plus the peak 60-second window),
  the label distribution and the hide-class counts;
* decide whether the video is a valid stimulus (duration, volume, density
  reachability, teacher coverage, enough attack/conflict/spoiler comments for the
  entertainment materials) and write the reasons when it is not;
* measure decision latency through the local server (p50 / p95 over 20 sampled
  danmaku segments) for the cached path and the online-linear path.

Usage::

    python3 scripts/study/prepare_stimuli.py
    python3 scripts/study/prepare_stimuli.py --retrain --skip-latency
    python3 scripts/study/prepare_stimuli.py --refresh-tags   # call the LLM teacher

Only reads data/tags; writes data/study. Network is used for bilibili metadata
and (with --refresh-tags) the LLM teacher, never for anything else.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from danmaku_filter import cache, taxonomy  # noqa: E402
from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

DEFAULT_STIMULI = REPO / "data" / "study" / "stimuli.json"
DEFAULT_REPORT = REPO / "data" / "study" / "report.json"
DEFAULT_TAGS = REPO / "data" / "tags"
DEFAULT_MODELS = REPO / "data" / "study" / "models"
DEFAULT_DATASET = REPO / "data" / "experiments" / "dataset.jsonl"
GENRES = ("information", "entertainment")
DENSITIES = ("low", "high")
DENSITY_TBD = "tbd"
MIN_ITEMS = 300
MIN_DURATION_S = 180
MAX_DURATION_S = 300
HIGH_DENSITY_PER_MIN = 150
LOW_DENSITY_PER_MIN = 30
MIN_TEACHER_ROWS = 50
MIN_SOCIAL_COUNT = 5
MIN_MODEL_ROWS = 20
DEFAULT_SAMPLE = 200
DEFAULT_LATENCY_SAMPLES = 20

# Labels the prototype hides (taxonomy visibility == "hide"). The protocol §2.2
# contrast criterion names attack/conflict/spoiler explicitly, but inflammatory
# is hidden too, so it is reported alongside them.
HIDE_LABELS = tuple(
    category["id"] for category in taxonomy.CATEGORY_DEFS if category["visibility"] == "hide"
)
SOCIAL_LABELS = ("attack", "conflict", "spoiler")
REPORTED_LABELS = ("attack", "conflict", "spoiler", "inflammatory", "irrelevant_repetition")

# Placeholder list used until the lead pins the real stimuli bvids. The videos
# are the six frozen-dataset videos, which already carry LLM teacher labels.
# Density is deliberately left "tbd": protocol §2.2 requires measuring it first.
PLACEHOLDER_STIMULI = [
    {"bvid": "BV1rCxe6wEN9", "genre": "information", "density": DENSITY_TBD, "label": "politics"},
    {"bvid": "BV1GJ411x7h7", "genre": "entertainment", "density": DENSITY_TBD, "label": "music/meme"},
    {"bvid": "BV1bnxr6cEJ9", "genre": "information", "density": DENSITY_TBD, "label": "politics/conflict"},
    {"bvid": "BV1mV4F6mE5Z", "genre": "entertainment", "density": DENSITY_TBD, "label": "gaming"},
    {"bvid": "BV1w5xh6sEEz", "genre": "entertainment", "density": DENSITY_TBD, "label": "music/meme"},
    {"bvid": "BV1W18Y61Ee5", "genre": "entertainment", "density": DENSITY_TBD, "label": "story/drama"},
]

_VIDEO_INFO_CACHE: dict[str, dict] = {}
_STREAM_CACHE: dict[str, dict] = {}


def _write_json(path: str | Path, payload: object) -> None:
    """Write JSON through a temp file + os.replace so readers never see half a file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; returns 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _validate_stimulus(row: object) -> dict:
    if not isinstance(row, dict):
        raise ValueError(f"stimulus must be an object: {row!r}")
    bvid = str(row.get("bvid") or "").strip()
    if not cache.BVID_PATTERN.match(bvid):
        raise ValueError(f"invalid bvid in stimuli: {bvid!r}")
    genre = str(row.get("genre") or "").strip()
    if genre not in GENRES:
        raise ValueError(f"genre must be one of {GENRES}, got {genre!r}")
    density = str(row.get("density") or DENSITY_TBD).strip() or DENSITY_TBD
    if density not in DENSITIES + (DENSITY_TBD,):
        raise ValueError(f"density must be one of {DENSITIES + (DENSITY_TBD,)}, got {density!r}")
    label = str(row.get("label") or "").strip()
    return {"bvid": bvid, "genre": genre, "density": density, "label": label}


def read_manifest(path: str | Path = DEFAULT_STIMULI, create: bool = True) -> tuple[list[dict], bool]:
    """Read the stimuli manifest, writing the placeholder when absent.

    Returns (stimuli, placeholder) where placeholder is True when the list still
    holds the frozen-dataset stand-ins rather than the final bvids.
    """
    target = Path(path)
    if not target.exists():
        if not create:
            raise FileNotFoundError(f"missing stimuli manifest: {target}")
        _write_json(target, {"schema": 1, "placeholder": True, "stimuli": PLACEHOLDER_STIMULI})
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("stimuli")
        placeholder = bool(payload.get("placeholder"))
    else:
        rows, placeholder = payload, False
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"stimuli manifest has no stimuli: {target}")
    return [_validate_stimulus(row) for row in rows], placeholder


def load_stimuli(path: str | Path = DEFAULT_STIMULI, create: bool = True) -> list[dict]:
    """Read the validated stimuli list (writing the placeholder when absent)."""
    return read_manifest(path, create)[0]


def read_dataset_rows(path: str | Path = DEFAULT_DATASET) -> dict[str, list[dict]]:
    """Group frozen teacher rows from dataset.jsonl by bvid (missing file -> {})."""
    grouped: dict[str, list[dict]] = {}
    target = Path(path)
    if not target.is_file():
        return grouped
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        source = row.get("label_source")
        if source not in (None, "") and not str(source).startswith("llm"):
            continue
        bvid = str(row.get("bvid") or "").strip()
        text = str(row.get("text") or "").strip()
        label = str(row.get("dominant") or row.get("label") or "").strip()
        if not bvid or not text or not label:
            continue
        grouped.setdefault(bvid, []).append(
            {"text": text, "label": label, "time_ms": int(row.get("time_ms") or 0)}
        )
    return grouped


def cache_items(payload: dict | None) -> list[dict]:
    """Return every usable item of a schema v2 tag cache as a flat row list."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        return []
    rows = []
    for item in payload["items"].values():
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "text": text,
                "label": str(item.get("dominant") or item.get("label") or "").strip(),
                "time_ms": int(item.get("time_ms") or 0),
                "source": str(item.get("source") or ""),
            }
        )
    return rows


def teacher_rows_from_cache(payload: dict | None) -> list[dict]:
    """Extract the LLM-sourced rows from one schema v2 tag cache."""
    return [row for row in cache_items(payload) if row["label"] and row["source"].startswith("llm")]


def cache_label_rows(payload: dict | None) -> list[dict]:
    """Extract every labelled row from a cache, whatever its source."""
    return [row for row in cache_items(payload) if row["label"]]


def teacher_status(payload: dict | None) -> dict:
    """Report whether a cache is fully LLM-teacher labelled."""
    rows = cache_label_rows(payload)
    llm_rows = teacher_rows_from_cache(payload)
    return {
        "items": len(rows),
        "teacher_rows": len(llm_rows),
        "backend": (payload or {}).get("backend") if isinstance(payload, dict) else None,
        "model": (payload or {}).get("model") if isinstance(payload, dict) else None,
        "teacher": bool(rows) and len(llm_rows) == len(rows),
    }


def video_info(bvid: str, enabled: bool = True) -> dict:
    """Fetch {title, duration_s, aid, cid} for one bvid, caching failures too."""
    if not enabled:
        return {}
    if bvid in _VIDEO_INFO_CACHE:
        return _VIDEO_INFO_CACHE[bvid]
    try:
        from danmaku_filter import fetch

        info = fetch.get_video_info(bvid)
        result = {
            "title": str(info.get("title") or ""),
            "duration_s": int(info.get("duration") or 0),
            "aid": int(info.get("aid") or 0),
            "cid": int(info.get("cid") or 0),
        }
    except Exception as error:  # offline or API change must not kill the batch
        result = {"error": "%s: %s" % (type(error).__name__, error)}
    _VIDEO_INFO_CACHE[bvid] = result
    return result


def fetch_stream(bvid: str, info: dict, max_segments: int = 60) -> dict:
    """Fetch the whole danmaku stream to measure real playback density.

    Cache files written through the online server can carry ``time_ms == 0`` for
    every item, which makes per-minute density unmeasurable.  The live stream
    still has real offsets, so it is used as the density source when available.
    Returns ``{count, items, segments}`` or ``{error}``; failures are cached too.
    """
    if bvid in _STREAM_CACHE:
        return _STREAM_CACHE[bvid]
    aid = int(info.get("aid") or 0)
    cid = int(info.get("cid") or 0)
    duration_s = int(info.get("duration_s") or 0)
    if not aid or not cid:
        result: dict = {"error": "missing aid/cid from metadata"}
        _STREAM_CACHE[bvid] = result
        return result
    try:
        from danmaku_filter import dmproto, fetch

        rows: list[dict] = []
        segments: list[bytes] = []
        stream_source = "legacy"
        # Segmented requests only return the windows the player asked for and
        # cap each window, so summing them understates density (410 vs 1200 for
        # the same video).  list.so exposes the whole (capped) pool with real
        # offsets, so it is the primary density source; segments are the fallback.
        try:
            legacy = fetch.fetch_danmaku(bvid)
            if legacy:
                rows = [
                    {
                        "text": danmaku.text,
                        "time_ms": int(danmaku.time_ms or 0),
                        "mode": int(danmaku.mode or 0),
                    }
                    for danmaku in legacy
                ]
        except Exception:
            rows = []
        if not rows:
            stream_source = "segment"
        for attempt in range(2 if not rows else 0):  # the segment API can answer 304 sporadically
            segments = fetch.fetch_all_segments(aid, cid, duration_s, max_segments=max_segments)
            rows = []
            seen: set[str] = set()
            for raw in segments:
                for danmaku in dmproto.parse_segment(raw):
                    if danmaku.dmid in seen:
                        continue
                    seen.add(danmaku.dmid)
                    rows.append({
                        "text": danmaku.text,
                        "time_ms": int(danmaku.time_ms or 0),
                        "mode": int(danmaku.mode or 0),
                    })
            if rows:
                break
            time.sleep(0.5)
        if not rows:
            # The seg.so API is sometimes rate limited; the legacy list.so
            # endpoint still exposes real playback offsets (capped at ~1.2k).
            try:
                legacy = fetch.fetch_danmaku(bvid)
                if legacy:
                    rows = [
                        {
                            "text": danmaku.text,
                            "time_ms": int(danmaku.time_ms or 0),
                            "mode": int(danmaku.mode or 0),
                        }
                        for danmaku in legacy
                    ]
                    stream_source = "legacy"
            except Exception:
                rows = []
        if rows:
            result = {
                "count": len(rows),
                "items": rows,
                "segments": len(segments),
                "source": stream_source,
            }
        else:
            result = {
                "error": "live stream returned no danmaku (segment and legacy endpoints empty)",
                "segments": len(segments),
            }
    except Exception as error:  # network problems must not kill the batch
        result = {"error": "%s: %s" % (type(error).__name__, error)}
    _STREAM_CACHE[bvid] = result
    return result


def sample_rows(rows: list[dict], limit: int, seed: int) -> list[dict]:
    """Stratify a sample by text length so short reactions and long sentences both appear."""
    usable = [row for row in rows if 2 <= len(row["text"].strip()) <= 60]
    useful = usable or rows
    useful = sorted(useful, key=lambda row: (len(row["text"]), row["text"]))
    if len(useful) <= limit:
        return useful[:limit]
    import random

    rng = random.Random(seed)
    buckets = 5
    per_bucket = max(1, limit // buckets)
    picked: list[dict] = []
    for index in range(buckets):
        chunk = useful[index * len(useful) // buckets : (index + 1) * len(useful) // buckets]
        picked.extend(rng.sample(chunk, min(per_bucket, len(chunk))))
    picked.extend(rng.sample(useful, max(0, limit - len(picked))))
    seen, unique = set(), []
    for row in picked:
        key = (row["text"], row["label"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique[:limit]


def train_video_model(
    rows: list[dict],
    out_path: str | Path,
    sample: int = DEFAULT_SAMPLE,
    seed: int = 20261105,
    kind: str = "centroid",
) -> dict | None:
    """Train one per-video linear tagger and save it as JSON; None when data is thin."""
    if len(rows) < MIN_MODEL_ROWS:
        return None
    picked = sample_rows(rows, sample, seed)
    texts = [row["text"] for row in picked]
    labels = [row["label"] for row in picked]
    if len(set(labels)) < 2:
        return None
    tagger = LinearTagger(kind=kind).train(texts, labels)
    target = Path(out_path)
    tagger.save(target)
    distribution = label_distribution(picked)
    return {
        "path": str(target),
        "size_kb": round(target.stat().st_size / 1024, 1),
        "kind": kind,
        "train_rows": len(picked),
        "labels": distribution,
    }


def _peak_per_minute(times_ms: list[int], window_ms: int = 60000) -> float:
    """Largest number of comments inside any window, expressed per minute."""
    ordered = sorted(value for value in times_ms if value > 0)
    best = 0
    start = 0
    for end, value in enumerate(ordered):
        while value - ordered[start] >= window_ms:
            start += 1
        best = max(best, end - start + 1)
    return best * (60000.0 / window_ms) if best else 0.0


def density_stats(items: list[dict], teacher_rows: list[dict], duration_s: int) -> dict:
    """Compute duration, item count and comments per minute for one video."""
    times = [row["time_ms"] for row in items if row.get("time_ms", 0) > 0]
    span_s = round((max(times) - min(times)) / 1000.0, 1) if len(times) >= 2 else 0.0
    total_s = duration_s or span_s
    per_min = round(len(items) / (total_s / 60.0), 1) if total_s > 0 and items else None
    per_min_span = (
        round(len(items) / (span_s / 60.0), 1) if span_s > 0 and items else None
    )
    teacher_times = [row["time_ms"] for row in teacher_rows if row.get("time_ms", 0) > 0]
    teacher_span_s = (
        round((max(teacher_times) - min(teacher_times)) / 1000.0, 1)
        if len(teacher_times) >= 2
        else 0.0
    )
    teacher_per_min = (
        round(len(teacher_times) / (teacher_span_s / 60.0), 1) if teacher_span_s > 0 else None
    )
    return {
        "duration_s": duration_s,
        "items": len(items),
        "span_s": span_s,
        "comments_per_min": per_min,
        "comments_per_min_span": per_min_span,
        "peak_per_min": round(_peak_per_minute(times), 1),
        "teacher_rows": len(teacher_rows),
        "teacher_span_s": teacher_span_s,
        "teacher_comments_per_min": teacher_per_min,
    }


def label_distribution(rows: list[dict]) -> dict[str, int]:
    """Count dominant labels, most frequent first."""
    counts: dict[str, int] = {}
    for row in rows:
        label = row["label"]
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def social_counts(distribution: dict[str, int]) -> dict:
    """Report the hide-class counts the C condition needs to differ from A."""
    counts = {label: int(distribution.get(label, 0)) for label in REPORTED_LABELS}
    total = sum(int(value) for value in distribution.values())
    hide_total = sum(counts[label] for label in HIDE_LABELS if label in counts)
    return {
        **counts,
        "hide_total": hide_total,
        "labelled": total,
        "hide_share": round(hide_total / total, 3) if total else 0.0,
    }


def density_feasibility(stats: dict) -> dict:
    """Decide whether the measured stream can serve the high and low density cells."""
    items = int(stats.get("items") or 0)
    peak = float(stats.get("peak_per_min") or 0.0)
    can_high = items >= MIN_ITEMS and peak >= HIGH_DENSITY_PER_MIN
    can_low = items >= MIN_ITEMS and peak >= LOW_DENSITY_PER_MIN
    suggested = "high" if can_high else ("low" if can_low else None)
    return {"can_high": can_high, "can_low": can_low, "suggested": suggested}


def _post_json(base: str, path: str, body: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def latency_stats(samples: list[float]) -> dict:
    """Summarise per-request latencies in milliseconds."""
    if not samples:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    ms = sorted(value * 1000.0 for value in samples)
    return {
        "n": len(ms),
        "p50_ms": round(percentile(ms, 0.5), 2),
        "p95_ms": round(percentile(ms, 0.95), 2),
        "mean_ms": round(statistics.fmean(ms), 2),
    }


def _chunks(values: list, parts: int) -> list[list]:
    """Split a list into at most parts contiguous chunks (never empty ones)."""
    if not values or parts <= 0:
        return []
    parts = min(parts, len(values))
    chunks: list[list] = []
    size = len(values) / float(parts)
    for index in range(parts):
        start, end = int(index * size), int((index + 1) * size)
        if end > start:
            chunks.append(values[start:end])
    return chunks


def measure_latency(
    bvid: str,
    tags_dir: str | Path,
    models_dir: str | Path,
    cached_dmids: list[str],
    online_texts: list[str],
    online_ready: bool,
    samples: int = DEFAULT_LATENCY_SAMPLES,
    host: str = "127.0.0.1",
) -> dict:
    """Time the real decisions endpoint in-process for cached and online-linear paths.

    Each request carries one danmaku segment (protocol §7.2 segment event), so the
    p50/p95 numbers cover the same work a browser batch would trigger. The
    online-linear path is served by the per-video model trained earlier in this
    run; the LLM teacher is disabled (online_classifier=False) so no request can
    reach the network.
    """
    from danmaku_filter.server import make_server

    server = make_server(
        host,
        0,
        tags_dir,
        online_backend="linear" if online_ready else "rules",
        online_classifier=False,
        models_dir=str(models_dir),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://%s:%d" % server.server_address[:2]
    result: dict = {"cached": latency_stats([]), "online_linear": latency_stats([])}
    if not online_ready:
        result["online_linear"]["note"] = "no per-video model: online-linear path not measured"
    try:
        cached_groups = _chunks(cached_dmids, samples)
        if cached_groups:
            _post_json(base, "/api/v1/decisions", {"bvid": bvid, "dmids": cached_groups[0], "ephemeral": True})
            cached_times = []
            for group in cached_groups:
                started = time.perf_counter()
                _post_json(base, "/api/v1/decisions", {"bvid": bvid, "dmids": group, "ephemeral": True})
                cached_times.append(time.perf_counter() - started)
            result["cached"] = latency_stats(cached_times)

        if online_ready:
            text_groups = _chunks(online_texts, samples)
            if text_groups:
                warm = {"study-warm-%d" % index: text for index, text in enumerate(text_groups[0])}
                _post_json(
                    base,
                    "/api/v1/decisions",
                    {"bvid": bvid, "dmids": list(warm), "texts": warm, "ephemeral": True},
                )
                online_times = []
                for group_index, group in enumerate(text_groups):
                    texts = {
                        "study-online-%d-%d" % (group_index, index): text
                        for index, text in enumerate(group)
                    }
                    started = time.perf_counter()
                    _post_json(
                        base,
                        "/api/v1/decisions",
                        {"bvid": bvid, "dmids": list(texts), "texts": texts, "ephemeral": True},
                    )
                    online_times.append(time.perf_counter() - started)
                result["online_linear"] = latency_stats(online_times)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return result


def _refresh_tags(bvid: str, tags_dir: str | Path, context: str = "") -> dict:
    """Run the LLM teacher precompute for one video (network + API key required)."""
    os.environ.setdefault("DM_LLM_BATCH", "100")
    from danmaku_filter import pipeline

    return pipeline.precompute(
        bvid, backend="llm", tags_dir=str(tags_dir), limit=0, video_context=context, max_segments=60
    )


def assess_stimulus(entry: dict) -> dict:
    """Fill status, insufficient reasons and warnings from the measured numbers."""
    reasons: list[str] = []
    warnings: list[str] = []
    stats = entry.get("density") if isinstance(entry.get("density"), dict) else {}
    duration_s = int(entry.get("duration_s") or 0)
    items = int(stats.get("items") or 0)
    peak = float(stats.get("peak_per_min") or 0.0)
    teacher_rows = int((entry.get("teacher") or {}).get("rows") or 0)
    social = entry.get("social_counts") or {}

    if not duration_s:
        reasons.append("duration unknown: metadata lookup failed, cannot verify the 3-5 min window")
    elif not MIN_DURATION_S <= duration_s <= MAX_DURATION_S:
        reasons.append(
            "duration %ds outside the 3-5 min material window (%d-%d s)"
            % (duration_s, MIN_DURATION_S, MAX_DURATION_S)
        )
    if items < MIN_ITEMS:
        reasons.append("only %d danmaku in the stream, need >= %d" % (items, MIN_ITEMS))
    if items >= MIN_ITEMS and peak < HIGH_DENSITY_PER_MIN:
        reasons.append(
            "peak density %.1f/min < %d/min: high-density condition unreachable"
            % (peak, HIGH_DENSITY_PER_MIN)
        )
    if teacher_rows < MIN_TEACHER_ROWS:
        reasons.append(
            "only %d teacher-labelled comments, need >= %d for a usable distribution"
            % (teacher_rows, MIN_TEACHER_ROWS)
        )
    if entry.get("genre") == "entertainment":
        weak = [label for label in SOCIAL_LABELS if int(social.get(label) or 0) < MIN_SOCIAL_COUNT]
        if weak:
            # Coverage is judged on the 3-video entertainment set (summarise()); a
            # single short video rarely carries all three hide classes, so this is
            # only a per-video warning.
            warnings.append(
                "entertainment video has few hide-class comments for the C contrast (%s, need >= %d each per video)"
                % (
                    ", ".join("%s=%d" % (label, int(social.get(label) or 0)) for label in weak),
                    MIN_SOCIAL_COUNT,
                )
            )

    feasibility = entry.get("density_feasibility") or {}
    declared = str(entry.get("density_declared") or DENSITY_TBD)
    suggested = feasibility.get("suggested")
    # A "low" cell is produced by deterministic thinning on a dense stream, so a
    # dense measurement is expected; only an unreachable high cell is a problem.
    if declared == "high" and suggested == "low":
        warnings.append(
            "declared density 'high' but the stream only supports the low cell"
        )
    if entry.get("density_source") == "cache" and not stats.get("span_s"):
        warnings.append(
            "tag cache has no playback timestamps: per-minute density could not be measured"
        )
    stream = entry.get("stream") or {}
    if stream.get("error"):
        warnings.append("live stream density unavailable (%s)" % stream["error"])
    if not feasibility.get("can_high"):
        warnings.append("cannot serve a high-density cell from this stream")
    coverage = (entry.get("teacher") or {}).get("coverage")
    if coverage is not None and coverage < 0.2:
        warnings.append(
            "teacher labels cover only %.1f%% of the stream: label distribution is an estimate"
            % (100.0 * coverage)
        )
    if entry.get("model") is None:
        warnings.append("no per-video model trained: online-linear path unavailable")
    backend = str((entry.get("teacher") or {}).get("backend") or "")
    if backend and not backend.startswith("llm"):
        warnings.append(
            "tag cache backend is %r (online/partial labels): item count and density may understate the stream"
            % backend
        )
    entry["status"] = "insufficient" if reasons else "ok"
    entry["reasons"] = reasons
    entry["warnings"] = warnings
    return entry


def prepare_one(
    stimulus: dict,
    tags_dir: Path,
    models_dir: Path,
    dataset: dict[str, list[dict]],
    sample: int,
    seed: int,
    retrain: bool,
    latency_samples: int,
    skip_latency: bool,
    allow_refresh: bool,
    context: str = "",
    metadata: bool = True,
    live_density: bool = True,
) -> dict:
    """Collect one stimulus entry: teacher rows, model, density, verdict, latency."""
    bvid = stimulus["bvid"]
    entry: dict = dict(stimulus)
    # Keep the declared density cell before the measured stats overwrite it.
    entry["density_declared"] = str(stimulus.get("density") or DENSITY_TBD)
    payload = cache.load_tags(bvid, tags_dir)
    items = cache_items(payload)
    status = teacher_status(payload)
    teacher_rows = teacher_rows_from_cache(payload)
    source = "tags"
    if len(teacher_rows) < MIN_TEACHER_ROWS:
        frozen = dataset.get(bvid) or []
        if len(frozen) >= MIN_TEACHER_ROWS:
            teacher_rows, source = frozen, "dataset"
        elif allow_refresh:
            try:
                _refresh_tags(bvid, tags_dir, context)
                payload = cache.load_tags(bvid, tags_dir)
                items = cache_items(payload)
                status = teacher_status(payload)
                teacher_rows, source = teacher_rows_from_cache(payload), "refreshed"
            except Exception as error:  # network/key problems must not kill the batch
                entry["refresh_error"] = "%s: %s" % (type(error).__name__, error)
                teacher_rows, source = cache_label_rows(payload), "cache-fallback"
        else:
            teacher_rows, source = cache_label_rows(payload), "cache-fallback"

    info = video_info(bvid, enabled=metadata)
    duration_s = int(info.get("duration_s") or (payload or {}).get("duration") or 0)
    entry["title"] = info.get("title") or ""
    entry["duration_s"] = duration_s
    entry["aid"] = info.get("aid") or (payload or {}).get("aid")
    entry["cid"] = info.get("cid") or (payload or {}).get("cid")
    if info.get("error"):
        entry["metadata_error"] = info["error"]
    stream = fetch_stream(bvid, info) if (metadata and live_density) else {"error": "live density disabled"}
    measure_items = items
    density_source = "cache"
    if stream.get("items"):
        measure_items = stream["items"]
        density_source = "live"
    entry["items"] = len(measure_items)
    entry["cache_items"] = len(items)
    entry["density_source"] = density_source
    entry["stream"] = {
        "count": len(measure_items) if density_source == "live" else None,
        "source": stream.get("source"),
        "segments": stream.get("segments"),
        "error": stream.get("error"),
    }
    entry["teacher"] = {
        "source": source,
        "rows": len(teacher_rows),
        "coverage": round(len(teacher_rows) / len(measure_items), 3) if measure_items else None,
        **{key: value for key, value in status.items() if key != "items"},
        "items": len(measure_items),
        "cache_items": len(items),
    }
    if not teacher_rows:
        entry["error"] = "no labels available"
        entry["density"] = density_stats(measure_items, [], duration_s)
        entry["density_feasibility"] = density_feasibility(entry["density"])
        entry["label_distribution"] = {}
        entry["social_counts"] = social_counts({})
        entry["model"] = None
        entry["status"] = "insufficient"
        entry["reasons"] = ["no teacher labels available"]
        entry["warnings"] = []
        return entry

    model_path = models_dir / ("%s.json" % bvid)
    model: dict | None = None
    if retrain or not model_path.is_file():
        model = train_video_model(teacher_rows, model_path, sample, seed)
    if model is None and model_path.is_file():
        model = {
            "path": str(model_path),
            "size_kb": round(model_path.stat().st_size / 1024, 1),
            "kind": None,
            "train_rows": None,
            "labels": label_distribution(teacher_rows),
            "reused": True,
        }

    stats = density_stats(measure_items, teacher_rows, duration_s)
    distribution = label_distribution(teacher_rows)
    entry["model"] = model
    entry["density"] = stats
    entry["density_feasibility"] = density_feasibility(stats)
    entry["label_distribution"] = distribution
    entry["social_counts"] = social_counts(distribution)
    if not skip_latency:
        cached_dmids = []
        if isinstance(payload, dict) and isinstance(payload.get("items"), dict):
            cached_dmids = list(payload["items"].keys())
        try:
            entry["latency"] = measure_latency(
                bvid,
                tags_dir,
                models_dir,
                cached_dmids,
                [row["text"] for row in teacher_rows],
                online_ready=model is not None and model_path.is_file(),
                samples=latency_samples,
            )
        except Exception as error:
            entry["latency_error"] = "%s: %s" % (type(error).__name__, error)
    return assess_stimulus(entry)


def summarise(entries: list[dict], placeholder: bool) -> dict:
    """Roll the per-video verdicts up to the six-stimulus material set."""
    genres = Counter(entry.get("genre") for entry in entries)
    insufficient = [
        {"bvid": entry["bvid"], "reasons": entry.get("reasons") or []}
        for entry in entries
        if entry.get("status") == "insufficient"
    ]
    reasons: list[str] = []
    if len(entries) != 6:
        reasons.append("need 6 stimuli, have %d" % len(entries))
    for genre in GENRES:
        if genres.get(genre, 0) != 3:
            reasons.append("need 3 %s videos, have %d" % (genre, genres.get(genre, 0)))
    # The C condition (prototype filtering) is judged over the whole entertainment
    # set: every participant watches all three entertainment videos, so the
    # material needs enough attack / conflict / spoiler comments in total rather
    # than in each single video.
    social_totals = {label: 0 for label in SOCIAL_LABELS}
    social_seen = False
    for entry in entries:
        if entry.get("genre") != "entertainment":
            continue
        counts = entry.get("social_counts") or {}
        if counts:
            social_seen = True
        for label in SOCIAL_LABELS:
            social_totals[label] += int(counts.get(label) or 0)
    warnings: list[str] = []
    if social_seen:
        # attack and conflict are the hide classes the C condition actually
        # removes; spoilers are scarce in real danmaku (1-2 per thousand) and are
        # only collapsed, so they are reported but not required in bulk.
        required = ("attack", "conflict")
        weak = [label for label in required if social_totals[label] < MIN_SOCIAL_COUNT]
        if weak:
            reasons.append(
                "material: entertainment set lacks hide-class comments for the C contrast (%s, need >= %d each across the three videos)"
                % (
                    ", ".join("%s=%d" % (label, social_totals[label]) for label in weak),
                    MIN_SOCIAL_COUNT,
                )
            )
        if social_totals.get("spoiler", 0) < MIN_SOCIAL_COUNT:
            warnings.append(
                "material: only %d spoiler comments across the entertainment set; spoiler collapse will be exercised rarely"
                % social_totals.get("spoiler", 0)
            )
    pairs = []
    for index in range(0, len(entries) - 1, 2):
        first, second = entries[index], entries[index + 1]
        problems: list[str] = []
        if {first.get("genre"), second.get("genre")} != set(GENRES):
            problems.append("pair needs one information + one entertainment video")
        feasibility = [entry.get("density_feasibility") or {} for entry in (first, second)]
        if not any(item.get("can_high") for item in feasibility):
            problems.append("no video in the pair can reach the high-density cell")
        if not any(item.get("can_low") for item in feasibility):
            problems.append("no video in the pair can serve the low-density cell")
        pairs.append(
            {
                "index": index // 2 + 1,
                "bvids": [first["bvid"], second["bvid"]],
                "ok": not problems,
                "problems": problems,
            }
        )
        if problems:
            reasons.append("pair P%d: %s" % (index // 2 + 1, "; ".join(problems)))
    return {
        "stimuli": len(entries),
        "genres": {genre: genres.get(genre, 0) for genre in GENRES},
        "ok": len(entries) - len(insufficient),
        "insufficient": insufficient,
        "pairs": pairs,
        "social": social_totals,
        "placeholder": placeholder,
        "ready": not reasons and not insufficient,
        "reasons": reasons,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stimuli", default=str(DEFAULT_STIMULI))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--tags-dir", default=str(DEFAULT_TAGS))
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--latency-samples", type=int, default=DEFAULT_LATENCY_SAMPLES)
    parser.add_argument("--retrain", action="store_true", help="retrain every per-video model")
    parser.add_argument("--skip-latency", action="store_true")
    parser.add_argument("--skip-metadata", action="store_true", help="do not query bilibili metadata")
    parser.add_argument(
        "--no-live-density",
        action="store_true",
        help="do not fetch the live stream; measure density from the tag cache only",
    )
    parser.add_argument(
        "--refresh-tags",
        action="store_true",
        help="call the LLM teacher for videos whose cache lacks teacher rows",
    )
    parser.add_argument(
        "--no-refresh-tags",
        action="store_true",
        help="never call the LLM teacher when a cache has no teacher rows",
    )
    parser.add_argument("--no-create", action="store_true", help="fail instead of writing the placeholder manifest")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tags_dir = Path(args.tags_dir)
    models_dir = Path(args.models_dir)
    stimuli, placeholder = read_manifest(args.stimuli, create=not args.no_create)
    dataset = read_dataset_rows(args.dataset)
    models_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "schema": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "stimuli": args.stimuli,
            "tags_dir": str(tags_dir),
            "models_dir": str(models_dir),
            "dataset": args.dataset,
            "sample": args.sample,
            "seed": args.seed,
            "latency_samples": args.latency_samples,
            "live_density": not args.no_live_density,
        },
        "stimuli": [],
    }
    for stimulus in stimuli:
        entry = prepare_one(
            stimulus,
            tags_dir,
            models_dir,
            dataset,
            args.sample,
            args.seed,
            args.retrain,
            args.latency_samples,
            args.skip_latency,
            allow_refresh=not args.no_refresh_tags,
            metadata=not args.skip_metadata,
            live_density=not args.no_live_density,
        )
        report["stimuli"].append(entry)
        if args.quiet:
            continue
        density = entry.get("density") or {}
        model = entry.get("model") or {}
        latency = entry.get("latency") or {}
        cached = latency.get("cached") or {}
        online = latency.get("online_linear") or {}
        print(
            "%-14s %-13s %-12s dur=%-4s items=%-6s /min=%-7s peak=%-7s teacher=%-5s "
            "model=%-7s p50=%s/%s p95=%s/%s %s"
            % (
                entry["bvid"],
                entry.get("genre", "?"),
                entry.get("status", "?"),
                entry.get("duration_s", "?"),
                "%s(%s)" % (density.get("items", "?"), entry.get("density_source", "?")),
                density.get("comments_per_min", "?"),
                density.get("peak_per_min", "?"),
                (entry.get("teacher") or {}).get("rows", "?"),
                ("%sKB" % model.get("size_kb")) if model.get("size_kb") else "-",
                cached.get("p50_ms", "-"),
                online.get("p50_ms", "-"),
                cached.get("p95_ms", "-"),
                online.get("p95_ms", "-"),
                entry.get("error") or entry.get("latency_error") or "",
            )
        )
        for reason in entry.get("reasons") or []:
            print("    insufficient: %s" % reason)
        for warning in entry.get("warnings") or []:
            print("    warning: %s" % warning)

    report["summary"] = summarise(report["stimuli"], placeholder)
    _write_json(args.report, report)
    if not args.quiet:
        summary = report["summary"]
        print(
            "summary: %s stimuli, %s ok, %s insufficient, ready=%s%s"
            % (
                summary["stimuli"],
                summary["ok"],
                len(summary["insufficient"]),
                summary["ready"],
                " (placeholder manifest)" if summary["placeholder"] else "",
            )
        )
        for reason in summary["reasons"]:
            print("  set reason: %s" % reason)
        print("report -> %s" % args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
