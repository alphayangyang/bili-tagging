
from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from danmaku_filter import cache
from danmaku_filter.server import _LLMWorker, _TagStore, make_server


def _write_tags(tags_dir: Path, bvid: str, rows: int) -> None:
    items = {}
    for index in range(rows):
        items[str(index)] = {
            "text": f"comment {index}",
            "time_ms": index,
            "mode": 1,
            "tags": {"humor": 0.9},
            "dominant": "humor",
            "score": 0.9,
            "evidence": "",
            "source": "llm",
        }
    cache.save_tags(
        bvid,
        {
            "schema": 2,
            "bvid": bvid,
            "backend": "llm",
            "model": "test",
            "created": "2026-11-06T00:00:00Z",
            "video_context": "",
            "items": items,
        },
        tags_dir,
    )


class TagStoreTest(unittest.TestCase):
    def test_repeated_requests_parse_the_cache_file_once(self):
        with TemporaryDirectory() as tmp:
            tags_dir = Path(tmp) / "tags"
            tags_dir.mkdir(parents=True)
            _write_tags(tags_dir, "BV1CACHE00001", 200)
            server = make_server(port=0, tags_dir=tags_dir, online_backend="rules")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                import urllib.request

                def ask() -> dict:
                    payload = json.dumps(
                        {"bvid": "BV1CACHE00001", "dmids": ["1", "2", "3"]}
                    ).encode("utf-8")
                    request = urllib.request.Request(
                        f"http://{host}:{port}/api/v1/decisions",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return json.loads(response.read())

                with mock.patch.object(cache, "load_tags", wraps=cache.load_tags) as spy:
                    for _ in range(3):
                        body = ask()
                        self.assertTrue(body["found"])
                    self.assertEqual(spy.call_count, 1)
                self.assertEqual(server.tag_store.loads, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_online_persist_keeps_the_store_warm(self):
        with TemporaryDirectory() as tmp:
            tags_dir = Path(tmp) / "tags"
            tags_dir.mkdir(parents=True)
            server = make_server(port=0, tags_dir=tags_dir, online_backend="rules")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                import urllib.request

                def ask() -> dict:
                    payload = json.dumps(
                        {
                            "bvid": "BV1CACHE00002",
                            "dmids": ["900001"],
                            "texts": {"900001": "哈哈哈哈这翻译"},
                        }
                    ).encode("utf-8")
                    request = urllib.request.Request(
                        f"http://{host}:{port}/api/v1/decisions",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return json.loads(response.read())

                first = ask()
                self.assertEqual(first["online"], 1)
                loads = server.tag_store.loads
                second = ask()
                self.assertTrue(second["found"])
                # The server wrote the file itself, so it must not re-parse it.
                self.assertEqual(server.tag_store.loads, loads)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class CostGuardTest(unittest.TestCase):
    def test_queue_cap_drops_instead_of_growing(self):
        worker = _LLMWorker(server=None, classifier=None)
        worker.max_pending = 3
        for index in range(3):
            self.assertTrue(worker.submit("BV1", str(index), f"text {index}"))
        self.assertFalse(worker.submit("BV1", "3", "text 3"))
        self.assertEqual(worker.dropped, 1)
        self.assertEqual(worker.pending(), 3)

    def test_duplicate_submit_is_ignored(self):
        worker = _LLMWorker(server=None, classifier=None)
        self.assertTrue(worker.submit("BV1", "7", "same"))
        self.assertFalse(worker.submit("BV1", "7", "same"))
        self.assertEqual(worker.pending(), 1)
        self.assertEqual(worker.dropped, 0)


if __name__ == "__main__":
    unittest.main()
