"""Offline end-to-end smoke test for the danmaku filter MVP.

No network, no browser: a synthetic tag cache + the real local server +
real HTTP requests. Exercises /health, /decisions, /miss, /feedback and
GET /tags, and verifies that a label correction flips the next decision.

    python3 scripts/smoke_test.py      # exit 0 = PASS
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from danmaku_filter import cache, server  # noqa: E402 - after sys.path bootstrap

BVID = "BV1SMOKE0001"
ITEMS = {
    "1001": {"text": "买挂吗", "time_ms": 1000, "mode": 1, "tags": {"attack": 0.92},
             "dominant": "attack", "score": 0.92, "evidence": "rule: ad", "source": "rules"},
    "1002": {"text": "哈哈哈哈", "time_ms": 2000, "mode": 1, "tags": {"humor": 0.9},
             "dominant": "humor", "score": 0.9, "evidence": "rule: laugh", "source": "rules"},
    "1003": {"text": "刷屏刷屏", "time_ms": 3000, "mode": 1, "tags": {"irrelevant_repetition": 0.8},
             "dominant": "irrelevant_repetition", "score": 0.8, "evidence": "rule: repeat", "source": "rules"},
    "1004": {"text": "剧情前提", "time_ms": 4000, "mode": 1, "tags": {"information": 0.85},
             "dominant": "information", "score": 0.85, "evidence": "rule: info", "source": "rules"},
}


def call(base: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def expect(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}")
        raise SystemExit(1)
    print(f"ok: {message}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dmf-smoke-") as tmp:
        tags_dir = save_cache(Path(tmp) / "data" / "tags")
        srv = server.make_server("127.0.0.1", 0, tags_dir)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            status, health = call(base, "GET", "/api/v1/health")
            expect(status == 200 and health["ok"] and health["tag_files"] == 1, "/health reports one cache")

            dmids = ["1001", "1002", "1003", "1004", "999"]
            status, payload = call(base, "POST", "/api/v1/decisions", {"bvid": BVID, "dmids": dmids})
            decisions = payload["decisions"]
            expect(status == 200 and payload["found"] is True, "/decisions finds the cache")
            expect(decisions["1001"]["action"] == "hide", "attack danmaku is hidden")
            expect(decisions["1003"]["action"] == "collapse", "repetition danmaku is collapsed")
            expect("1002" not in decisions and "1004" not in decisions, "show danmaku is omitted from decisions")
            expect(payload["miss"] == ["999"], "unknown dmid is reported as miss")

            status, payload = call(base, "POST", "/api/v1/miss", {"bvid": BVID, "dmids": ["999", "1000"]})
            expect(status == 200 and payload["added"] == 2, "/miss queues missing dmids")
            status, payload = call(base, "POST", "/api/v1/miss", {"bvid": BVID, "dmids": ["999"]})
            expect(payload["added"] == 0, "/miss is idempotent")

            status, _ = call(base, "POST", "/api/v1/feedback", {
                "bvid": BVID, "dmid": "1002", "kind": "label_correction",
                "payload": {"old_label": "humor", "new_label": "attack"},
            })
            expect(status == 200, "label correction is accepted")
            status, payload = call(base, "POST", "/api/v1/decisions", {"bvid": BVID, "dmids": ["1002"]})
            flipped = payload["decisions"]["1002"]
            expect(flipped["action"] == "hide" and flipped["evidence"] == "user correction",
                   "label correction flips the next decision")

            status, _ = call(base, "POST", "/api/v1/feedback", {
                "bvid": BVID, "kind": "prefs", "payload": {"per_type": {"attack": "show"}},
            })
            expect(status == 200, "prefs event is accepted")

            status, payload = call(base, "GET", f"/api/v1/tags?bvid={BVID}&limit=2")
            expect(status == 200 and len(payload["payload"]["items"]) == 2, "GET /tags honours the limit")

            status, _ = call(base, "GET", "/api/v1/nope")
            expect(status == 404, "unknown path returns 404")
            status, _ = call(base, "POST", "/api/v1/decisions", {"bvid": BVID})
            expect(status == 400, "missing dmids returns 400")

            files = sorted(path.name for path in (Path(tmp) / "data" / "feedback").glob("*.jsonl"))
            expect(files == ["label_corrections.jsonl", "prefs_events.jsonl"],
                   f"feedback files created: {files}")
        finally:
            srv.shutdown()
            srv.server_close()
    print("PASS: MVP smoke test")
    return 0


def save_cache(tags_dir: Path) -> Path:
    payload = {
        "schema": 2,
        "bvid": BVID,
        "aid": 1,
        "cid": 2,
        "duration": 10,
        "segments": 1,
        "backend": "rules",
        "model": None,
        "created": "2026-01-01T00:00:00Z",
        "items": ITEMS,
    }
    cache.save_tags(BVID, payload, tags_dir)
    return tags_dir


if __name__ == "__main__":
    raise SystemExit(main())
