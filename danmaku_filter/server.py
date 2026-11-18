"""Local HTTP API for the danmaku filter (127.0.0.1 only, stdlib only).

The server reads the precomputed tag cache and user overrides from disk and
applies ``policy.decide`` per requested dmid; it never touches the network.
See docs/design/contracts.md §4 for the wire format and §1 for the directories.

Run with::

    python3 -m danmaku_filter.server --host 127.0.0.1 --port 8765

Data directories are derived from ``--tags-dir``: its parent directory holds
``overrides/``, ``pending/`` and ``feedback/`` (so the default
``data/tags`` becomes ``data/overrides``, ``data/pending``,
``data/feedback``).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, cache, policy
from .classify.linear import LinearTagger
from .classify.rules import RuleClassifier
from .taxonomy import CATEGORY_IDS, FALLBACK_ID
from .types import ClassificationResult, Danmaku

HOST = "127.0.0.1"
PORT = 8765
MAX_DMIDS = 20_000
MAX_ONLINE_ITEMS = 200_000
MAX_TEXT_CHARS = 400
MAX_BODY_BYTES = 4 * 1024 * 1024
# Linear labels below this score are re-checked by the LLM teacher (L2).
LOW_CONFIDENCE_UPGRADE = 0.45
KNOWN_LABELS = set(CATEGORY_IDS) | {FALLBACK_ID}

LABEL_CORRECTIONS_FILE = "label_corrections.jsonl"
POLICY_EVENTS_FILE = "policy_events.jsonl"
PREFS_EVENTS_FILE = "prefs_events.jsonl"
FEEDBACK_FILES = {
    "label_correction": LABEL_CORRECTIONS_FILE,
    "policy_event": POLICY_EVENTS_FILE,
    "prefs": PREFS_EVENTS_FILE,
}

# path -> {HTTP method: handler method name}; anything else is a 404 / 405.
_ROUTES = {
    "/api/v1/health": {"GET": "_health"},
    "/api/v1/decisions": {"POST": "_decisions"},
    "/api/v1/miss": {"POST": "_miss"},
    "/api/v1/feedback": {"POST": "_feedback"},
    "/api/v1/tags": {"GET": "_tags"},
    "/api/v1/model": {"GET": "_model"},
}


_ONLINE_CLASSIFIER = RuleClassifier()


def _online_item(text: str, dmid: str) -> dict:
    """Classify one danmaku that has no offline cache entry.

    The rule baseline is cheap, deterministic and high precision, which is what
    an online fallback needs: it fires on attack/spoiler/conflict/repetition and
    leaves everything else to other so the default policy shows it.
    """
    result = _ONLINE_CLASSIFIER.classify(Danmaku(text=text, time_ms=0, mode=1, dmid=str(dmid)))
    return _online_item_from(result, dmid, text, source="rules-online")


def _online_item_from(result, dmid: str, text: str, source: str) -> dict:
    """Turn a classifier result into a schema-v2 cache item for one danmaku."""
    from .pipeline import _build_item

    danmaku = Danmaku(text=text, time_ms=0, mode=1, dmid=str(dmid))
    item = _build_item(danmaku, result)
    item["source"] = source
    return item


class _ModelStore:
    """Loads per-video linear taggers from the models directory (mtime aware)."""

    def __init__(self, models_dir) -> None:
        self.dir = Path(models_dir) if models_dir else None
        self._cache = {}
        self._global = None

    def _load(self, path: Path):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(path.stem)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            tagger = LinearTagger.load(path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None
        self._cache[path.stem] = (mtime, tagger)
        return tagger

    def for_bvid(self, bvid: str):
        """Return a tagger for the video, falling back to the pooled model."""
        if self.dir is None:
            return None
        tagger = self._load(self.dir / (bvid + ".json"))
        if tagger is not None:
            return tagger
        try:
            mtime = (self.dir / "_global.json").stat().st_mtime
        except OSError:
            return None
        if self._global is None or self._global[0] != mtime:
            tagger = self._load(self.dir / "_global.json")
            if tagger is None:
                return None
            self._global = (mtime, tagger)
        return self._global[1]

    def count(self) -> int:
        if self.dir is None or not self.dir.is_dir():
            return 0
        return len(list(self.dir.glob("*.json")))

    def raw_for_bvid(self, bvid: str):
        """Return (model, source) raw JSON for the browser, cached by mtime.

        The browser needs the exact JSON on disk (it re-implements the scorer),
        so this bypasses LinearTagger and parses the file itself.
        """
        if self.dir is None:
            return None, None
        raw_cache = getattr(self, "_raw_cache", None)
        if raw_cache is None:
            raw_cache = {}
            self._raw_cache = raw_cache
        candidates = []
        if bvid:
            candidates.append((self.dir / (bvid + ".json"), "video"))
        candidates.append((self.dir / "_global.json", "global"))
        for path, source in candidates:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = raw_cache.get(path.stem)
            if cached is not None and cached[0] == mtime:
                return cached[1], cached[2]
            try:
                model = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            raw_cache[path.stem] = (mtime, model, source)
            return model, source
        return None, None


def _build_models(models_dir):
    """Return a model store when at least one model file exists."""
    store = _ModelStore(models_dir)
    return store if store.count() else None


def _linear_online_item(tagger, text: str, dmid: str) -> dict:
    """Label one comment with the local linear tagger (microseconds)."""
    scores = tagger.predict_scores([text])[0]
    labels = {k: float(v) for k, v in scores.items() if float(v) > 0.01}
    if labels:
        best_label, best_score = max(labels.items(), key=lambda kv: kv[1])
    else:
        best_label, best_score = FALLBACK_ID, 1.0
        labels = {FALLBACK_ID: 1.0}
    result = ClassificationResult(
        label=best_label, confidence=float(best_score), rationale="", source="linear", tags=labels
    )
    return _online_item_from(result, dmid, text, source="linear-online")


def _build_online_llm():
    """Build the local LLM tagger, or None when no endpoint is configured."""
    try:
        from .classify.llm import LLMClassifier, batch_size, settings

        settings()  # raises when a remote endpoint has no API key
        return LLMClassifier(
            batch_size=max(1, min(8, batch_size())), video_context="Bilibili danmaku"
        )
    except Exception:
        return None


class _LLMWorker(threading.Thread):
    """Asynchronously upgrade online labels from the rule baseline to the LLM.

    The first response for a fresh segment must never wait for the model: the
    server answers with the rule label immediately and this worker re-labels the
    same comments in small batches. Later requests (the 300 ms DOM tick, or the
    next visit) see the richer relevance vector.
    """

    def __init__(self, server, classifier, batch_size: int = 8) -> None:
        super().__init__(daemon=True, name="dmf-llm-worker")
        self.server = server
        self.classifier = classifier
        self.batch_size = batch_size
        self.queue: list[tuple[str, str, str]] = []
        self.pending_keys: set[str] = set()
        self.condition = threading.Condition()
        self.stopped = False
        self.done = 0
        self.failed = 0
        self.dropped = 0
        self.last_error = ""
        # A dense video can offer tens of thousands of comments in minutes; an
        # unbounded queue would keep a paid API busy for hours, so cap it. The
        # comments dropped here keep their fast linear label.
        self.max_pending = 4000

    def submit(self, bvid: str, dmid: str, text: str) -> bool:
        key = bvid + "|" + dmid
        with self.condition:
            if key in self.pending_keys:
                return False
            if len(self.queue) >= self.max_pending:
                self.dropped += 1
                return False
            self.pending_keys.add(key)
            self.queue.append((bvid, dmid, text))
            self.condition.notify()
            return True

    def pending(self) -> int:
        with self.condition:
            return len(self.queue)

    def stop(self) -> None:
        with self.condition:
            self.stopped = True
            self.condition.notify()

    def run(self) -> None:
        while True:
            with self.condition:
                while not self.queue and not self.stopped:
                    self.condition.wait(0.5)
                if self.stopped:
                    return
                batch = self.queue[: self.batch_size]
                del self.queue[: len(batch)]
            self._label(batch)

    def _label(self, batch: list[tuple[str, str, str]]) -> None:
        danmaku = [Danmaku(text=text, time_ms=0, mode=1, dmid=dmid) for _bvid, dmid, text in batch]
        try:
            results = self.classifier.classify_batch(danmaku)
        except Exception as exc:  # keep serving rule labels, never crash the server
            with self.condition:
                for bvid, dmid, _text in batch:
                    self.pending_keys.discard(bvid + "|" + dmid)
                self.failed += len(batch)
                self.last_error = type(exc).__name__ + ": " + str(exc)
            return
        with self.server.lock:
            for (bvid, dmid, text), result in zip(batch, results):
                self.server.online[bvid + "|" + dmid] = _online_item_from(
                    result, dmid, text, source="llm-online"
                )
        with self.condition:
            for bvid, dmid, _text in batch:
                self.pending_keys.discard(bvid + "|" + dmid)
            self.done += len(batch)


def _queue_llm(server, bvid: str, dmid: str, text: str) -> None:
    worker = getattr(server, "llm_worker", None)
    if worker is not None:
        worker.submit(bvid, dmid, text)


def _now_iso() -> str:
    """Current UTC time in the ``2026-10-21T12:00:00Z`` form used in the data dirs."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, row: dict) -> None:
    """Append one JSON event, creating the feedback directory on demand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class _Handler(BaseHTTPRequestHandler):
    """One request on the local JSON API; see ``_ROUTES`` for endpoints."""

    server_version = "DanmakuFilter/0.1"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        allowed = _ROUTES.get(path)
        if allowed is None:
            self._send_json(404, {"ok": False, "error": f"unknown path: {path}"})
            return
        handler_name = allowed.get(method)
        if handler_name is None:
            self._send_json(
                405, {"ok": False, "error": f"method {method} is not allowed for {path}"}
            )
            return
        try:
            getattr(self, handler_name)(parsed)
        except ValueError as error:
            self._send_json(400, {"ok": False, "error": str(error)})
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            traceback.print_exc()
            self._send_json(500, {"ok": False, "error": "internal server error"})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The page navigated away or aborted a timed-out request; nothing
            # to answer and nothing worth printing a traceback for.
            self.close_connection = True

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    # -- request validation ----------------------------------------------

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise ValueError("invalid Content-Length header") from error
        if length > MAX_BODY_BYTES:
            raise ValueError(f"request body too large (max {MAX_BODY_BYTES} bytes)")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            raise ValueError("request body must be a JSON object")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid JSON body") from error
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    @staticmethod
    def _require_bvid(body: dict) -> str:
        bvid = body.get("bvid")
        if not isinstance(bvid, str) or not bvid.strip():
            raise ValueError("bvid is required")
        bvid = bvid.strip()
        if not cache.BVID_PATTERN.match(bvid):
            raise ValueError(f"invalid bvid: {bvid!r}")
        return bvid

    @staticmethod
    def _require_dmids(body: dict) -> list[str]:
        values = body.get("dmids")
        if not isinstance(values, list):
            raise ValueError("dmids must be a list of strings")
        dmids = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("dmids must be non-empty strings")
            dmids.append(value.strip())
        return dmids

    # -- endpoints --------------------------------------------------------

    def _health(self, _parsed: urllib.parse.ParseResult) -> None:
        server = self.server
        tag_files = 0
        if server.tags_dir.is_dir():
            tag_files = sum(1 for _ in server.tags_dir.glob("*.json"))
        self._send_json(
            200,
            {
                "ok": True,
                "version": __version__,
                "tag_files": tag_files,
                "tags_dir": str(server.tags_dir),
                "online_backend": server.online_backend,
                "llm_pending": server.llm_worker.pending() if server.llm_worker else 0,
                "llm_done": server.llm_worker.done if server.llm_worker else 0,
                "llm_failed": server.llm_worker.failed if server.llm_worker else 0,
                "llm_dropped": server.llm_worker.dropped if server.llm_worker else 0,
                "tag_cache_loads": server.tag_store.loads,
                "llm_last_error": server.llm_worker.last_error if server.llm_worker else "",
                "models": server.models.count() if server.models else 0,
            },
        )

    def _model(self, parsed: urllib.parse.ParseResult) -> None:
        """Serve the raw linear model JSON so the userscript can tag locally.

        The whole-pool design needs a first pass over every comment before
        playback: doing that through /api/v1/decisions costs a round trip per
        batch, while the model itself is a few hundred KB and scores at
        microseconds per comment in the page.
        """
        query = urllib.parse.parse_qs(parsed.query)
        raw_bvid = (query.get("bvid") or [""])[0].strip()
        if raw_bvid and not cache.BVID_PATTERN.match(raw_bvid):
            raise ValueError(f"invalid bvid: {raw_bvid!r}")
        server = self.server
        if server.models is None:
            self._send_json(200, {"ok": True, "bvid": raw_bvid, "source": None, "model": None})
            return
        model, source = server.models.raw_for_bvid(raw_bvid)
        self._send_json(
            200,
            {
                "ok": True,
                "bvid": raw_bvid,
                "source": source,
                "model": model,
            },
        )

    def _decisions(self, _parsed: urllib.parse.ParseResult) -> None:
        body = self._read_json_body()
        bvid = self._require_bvid(body)
        dmids = self._require_dmids(body)
        prefs = body.get("prefs")
        if prefs is not None and not isinstance(prefs, dict):
            raise ValueError("prefs must be an object")
        raw_texts = body.get("texts")
        if raw_texts is not None and not isinstance(raw_texts, dict):
            raise ValueError("texts must be an object")
        texts = raw_texts if isinstance(raw_texts, dict) else {}
        # Live DOM checks ask about a handful of already-rendered comments on
        # every pref change. They must not grow the tag cache, so they opt out
        # of persistence and ask for a label for every dmid (not only the ones
        # that end up hidden).
        ephemeral = body.get("ephemeral") is True
        want_labels = body.get("labels") is True
        unique = list(dict.fromkeys(dmids))
        if len(unique) > MAX_DMIDS:
            raise ValueError(f"too many dmids (max {MAX_DMIDS})")

        server = self.server
        payload = server.tag_store.get(bvid)
        found = payload is not None
        raw_items = payload.get("items") if found else None
        items = raw_items if isinstance(raw_items, dict) else {}
        overrides = cache.load_overrides(bvid, server.overrides_dir)

        decisions: dict[str, dict] = {}
        labels: dict[str, dict] = {}
        miss: list[str] = []
        online_added = 0
        changed = False
        tagger = None
        if server.models is not None:
            tagger = server.models.for_bvid(bvid)
        for dmid in unique:
            item = items.get(dmid)
            key = bvid + "|" + dmid
            with server.lock:
                upgraded = server.online.get(key)
            if isinstance(upgraded, dict):
                # The background LLM worker may have replaced the fast rule
                # label with a richer vector after the first response.
                if upgraded is not item:
                    item = upgraded
                    if not ephemeral:
                        items[dmid] = upgraded
                        changed = True
            if not isinstance(item, dict):
                text = texts.get(dmid)
                if isinstance(text, str) and text.strip():
                    clean_text = text.strip()[:MAX_TEXT_CHARS]
                    used_model = False
                    with server.lock:
                        item = server.online.get(key)
                        fresh = item is None
                        if fresh:
                            if len(server.online) >= MAX_ONLINE_ITEMS:
                                server.online.clear()
                            if tagger is not None:
                                # L1: a per-video linear model trained offline
                                # from LLM teacher labels; microseconds, no GPU.
                                item = _linear_online_item(tagger, clean_text, dmid)
                                used_model = True
                            else:
                                item = _online_item(clean_text, dmid)
                            if not ephemeral:
                                server.online[key] = item
                                online_added += 1
                    # A local LLM (when configured) re-labels in the background;
                    # the response below already uses the fast label.
                    if fresh and not used_model:
                        _queue_llm(server, bvid, dmid, clean_text)
                    elif fresh and used_model:
                        # Low-confidence linear labels are sent back to the
                        # teacher: the next DOM tick sees the upgraded vector.
                        confidence = float(item.get("score") or 0.0)
                        if confidence < LOW_CONFIDENCE_UPGRADE:
                            _queue_llm(server, bvid, dmid, clean_text)
                    if not ephemeral:
                        items[dmid] = item
                else:
                    miss.append(dmid)
                    continue
            resolved = policy.resolve_item(item, overrides.get(dmid))
            if not isinstance(resolved, dict):
                resolved = item
            decision = policy.decide(resolved, prefs)
            action = decision.get("action")
            if want_labels:
                labels[dmid] = {
                    "label": decision.get("label", FALLBACK_ID),
                    "score": decision.get("score", 0.0),
                }
            if action == "show":
                continue
            decisions[dmid] = {
                "action": action,
                "label": decision.get("label", FALLBACK_ID),
                "score": decision.get("score", 0.0),
                "reason": decision.get("reason", ""),
                "text": resolved.get("text", ""),
                "evidence": resolved.get("evidence", ""),
            }
        if online_added or changed:
            self._persist_online(bvid, payload, items, found)
        response = {
            "ok": True,
            "bvid": bvid,
            "found": found,
            "decisions": decisions,
            "miss": miss,
            "online": online_added,
            "n": len(unique),
        }
        if want_labels:
            response["labels"] = labels
        self._send_json(200, response)

    def _persist_online(self, bvid: str, payload: dict | None, items: dict, found: bool) -> None:
        """Cache online-classified items so the next visit needs no rework."""
        try:
            if found and isinstance(payload, dict):
                payload["updated"] = _now_iso()
                cache.save_tags(bvid, payload, self.server.tags_dir)
                self.server.tag_store.saved(bvid, payload)
                return
            fresh = {
                "schema": 2,
                "bvid": bvid,
                "backend": "rules-online",
                "model": None,
                "created": _now_iso(),
                "video_context": "",
                "items": items,
            }
            cache.save_tags(bvid, fresh, self.server.tags_dir)
            self.server.tag_store.saved(bvid, fresh)
        except (OSError, ValueError):
            pass

    def _miss(self, _parsed: urllib.parse.ParseResult) -> None:
        body = self._read_json_body()
        bvid = self._require_bvid(body)
        dmids = self._require_dmids(body)
        server = self.server
        with server.lock:
            added = cache.add_pending(bvid, dmids, server.pending_dir)
            pending = len(cache.load_pending(bvid, server.pending_dir))
        self._send_json(200, {"ok": True, "added": added, "pending": pending})

    def _feedback(self, _parsed: urllib.parse.ParseResult) -> None:
        body = self._read_json_body()
        bvid = self._require_bvid(body)
        kind = body.get("kind")
        if kind not in FEEDBACK_FILES:
            raise ValueError(f"unknown feedback kind: {kind!r}")
        payload = body.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        payload = payload or {}
        dmid = body.get("dmid")
        if kind != "prefs" and (not isinstance(dmid, str) or not dmid.strip()):
            raise ValueError("dmid is required for this feedback kind")
        received = _now_iso()
        row: dict = {"at": received, "bvid": bvid, "kind": kind, "payload": payload}
        if isinstance(dmid, str) and dmid.strip():
            row["dmid"] = dmid.strip()

        server = self.server
        if kind == "label_correction":
            label = payload.get("new_label") or payload.get("label")
            if not isinstance(label, str) or label not in KNOWN_LABELS:
                raise ValueError("payload.new_label must be a taxonomy label")
            with server.lock:
                overrides = cache.load_overrides(bvid, server.overrides_dir)
                overrides[dmid.strip()] = {"label": label, "at": received}
                cache.save_overrides(bvid, overrides, server.overrides_dir)
                _append_jsonl(server.feedback_dir / FEEDBACK_FILES[kind], row)
        else:
            with server.lock:
                _append_jsonl(server.feedback_dir / FEEDBACK_FILES[kind], row)
        self._send_json(200, {"ok": True})

    def _tags(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        bvid = (query.get("bvid") or [""])[0].strip()
        if not bvid:
            raise ValueError("bvid query parameter is required")
        if not cache.BVID_PATTERN.match(bvid):
            raise ValueError(f"invalid bvid: {bvid!r}")
        raw_limit = (query.get("limit") or ["100"])[0]
        try:
            limit = int(raw_limit)
        except ValueError as error:
            raise ValueError("limit must be an integer") from error
        if limit < 0:
            raise ValueError("limit must be >= 0")
        payload = self.server.tag_store.get(bvid)
        if isinstance(payload, dict):
            payload = dict(payload)
            items = payload.get("items")
            if isinstance(items, dict):
                payload["items"] = dict(list(items.items())[:limit])
        self._send_json(200, {"ok": True, "payload": payload})


class _TagStore:
    """In-memory schema v2 tag cache keyed by bvid and refreshed by mtime.

    A hot video can hold tens of thousands of comments: the on-disk cache file
    for the stress video was 20 MB, and re-parsing it on every decisions call
    dominated the request time (about 900 ms for a 200-comment batch). The store
    keeps the parsed payload in memory and only re-reads a file when its mtime
    changed, which makes a request cost O(items) instead of O(file).
    """

    def __init__(self, tags_dir) -> None:
        self.tags_dir = Path(tags_dir)
        self._cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()
        self.loads = 0

    def _mtime(self, bvid: str) -> float | None:
        try:
            return cache.tags_path(bvid, self.tags_dir).stat().st_mtime
        except (OSError, ValueError):
            return None

    def get(self, bvid: str) -> dict | None:
        mtime = self._mtime(bvid)
        if mtime is None:
            with self.lock:
                self._cache.pop(bvid, None)
            return None
        cached = self._cache.get(bvid)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        payload = cache.load_tags(bvid, self.tags_dir)
        if not isinstance(payload, dict):
            return None
        with self.lock:
            self.loads += 1
            self._cache[bvid] = (mtime, payload)
        return payload

    def saved(self, bvid: str, payload: dict) -> None:
        """Record a payload the server just wrote, avoiding a re-parse."""
        mtime = self._mtime(bvid)
        if mtime is None:
            return
        with self.lock:
            self._cache[bvid] = (mtime, payload)

    def files(self) -> int:
        try:
            return len([p for p in self.tags_dir.glob("*.json") if p.is_file()])
        except OSError:
            return 0


class DanmakuFilterServer(ThreadingHTTPServer):
    """Threaded API server; data directories derive from ``tags_dir``."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        tags_dir: str | Path = "data/tags",
        online_backend: str = "rules",
        online_classifier=None,
        models_dir=None,
    ) -> None:
        super().__init__(address, _Handler)
        self.tags_dir = Path(tags_dir)
        self.data_root = self.tags_dir.parent
        self.overrides_dir = self.data_root / "overrides"
        self.pending_dir = self.data_root / "pending"
        self.feedback_dir = self.data_root / "feedback"
        self.lock = threading.Lock()
        self.quiet = False
        self.tag_store = _TagStore(self.tags_dir)
        # (bvid|dmid) -> item, populated by the online fallback so the same
        # comment is only classified once per process; the LLM worker upgrades
        # these entries in place.
        self.online: dict[str, dict] = {}
        self.online_backend = online_backend
        self.models = _build_models(
            models_dir if models_dir is not None else self.data_root / "models"
        )
        self.llm_worker = None
        if online_backend == "linear" and self.models is None:
            # No trained model yet: fall back to the LLM teacher when possible,
            # otherwise to rules, so a fresh checkout still works.
            self.online_backend = "llm"
        if self.online_backend in {"llm", "auto"}:
            classifier = online_classifier or _build_online_llm()
            if classifier is None:
                if online_backend == "llm":
                    raise RuntimeError(
                        "online_backend=llm needs DM_LLM_BASE_URL and DM_LLM_MODEL "
                        "(plus DM_LLM_API_KEY for remote endpoints)"
                    )
                if self.online_backend != "rules":
                    self.online_backend = "rules"
            else:
                self.llm_worker = _LLMWorker(self, classifier)
                self.llm_worker.start()
        elif self.online_backend == "linear":
            # Cascade L2: the linear model answers in microseconds, and a
            # configured LLM re-labels low-confidence comments in the
            # background so the cache keeps improving. Pass
            # online_classifier=False to disable the teacher entirely.
            if online_classifier is False:
                classifier = None
            elif online_classifier is not None:
                classifier = online_classifier
            else:
                classifier = _build_online_llm()
            if classifier is not None:
                self.llm_worker = _LLMWorker(self, classifier)
                self.llm_worker.start()

    def handle_error(self, request, client_address) -> None:
        """Connection resets are normal for a browser-side hook; stay quiet."""
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def server_close(self) -> None:
        worker = getattr(self, "llm_worker", None)
        if worker is not None:
            worker.stop()
        super().server_close()


def make_server(
    host: str = HOST,
    port: int = PORT,
    tags_dir: str | Path = "data/tags",
    online_backend: str = "rules",
    online_classifier=None,
    models_dir=None,
) -> DanmakuFilterServer:
    """Bind (but do not start) the API server; ``port=0`` picks a free port."""
    return DanmakuFilterServer(
        (host, int(port)), tags_dir, online_backend, online_classifier, models_dir
    )


def serve(
    host: str = HOST,
    port: int = PORT,
    tags_dir: str | Path = "data/tags",
    online_backend: str = "rules",
    online_classifier=None,
    models_dir=None,
) -> None:
    """Serve until interrupted; Ctrl+C closes the socket and returns normally."""
    server = make_server(host, port, tags_dir, online_backend, online_classifier, models_dir)
    bound_host, bound_port = server.server_address[:2]
    print(
        f"danmaku filter server listening on http://{bound_host}:{bound_port} "
        f"(tags: {server.tags_dir})",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m danmaku_filter.server",
        description="Serve the local danmaku filter API on 127.0.0.1 (stdlib only).",
    )
    parser.add_argument("--host", default=HOST, help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=PORT, help="TCP port (default: 8765)")
    parser.add_argument(
        "--tags-dir", default="data/tags", help="tag cache directory (default: data/tags)"
    )
    parser.add_argument(
        "--online-backend",
        default="rules",
        choices=("rules", "llm", "auto", "linear"),
        help="label uncached danmaku with rules, a local LLM, a trained linear model, "
        "or llm-with-rules-fallback",
    )
    parser.add_argument(
        "--models-dir",
        default=None,
        help="trained linear models (default: <tags-dir>/../models)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        serve(
            host=args.host,
            port=args.port,
            tags_dir=args.tags_dir,
            online_backend=args.online_backend,
            models_dir=args.models_dir,
        )
    except KeyboardInterrupt:
        print("server stopped")
        return 0
    except OSError as error:
        print(f"error: cannot listen on {args.host}:{args.port}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
