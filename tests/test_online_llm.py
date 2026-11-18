"""Tests for the asynchronous LLM upgrade path in danmaku_filter.server."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from danmaku_filter import cache
from danmaku_filter.server import make_server
from danmaku_filter.types import ClassificationResult


class StubClassifier:
    """Stand-in for LLMClassifier: fixed vector, no network, optional failure."""

    LABEL = "emotional_resonance"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []
        self.lock = threading.Lock()

    def classify_batch(self, items):
        with self.lock:
            self.calls.append([item.text for item in items])
            if self.fail:
                raise RuntimeError("stub classifier unavailable")
            return [
                ClassificationResult(
                    label=self.LABEL,
                    confidence=0.9,
                    rationale="stub",
                    source="llm",
                    tags={self.LABEL: 0.9, "other": 0.1},
                )
                for _ in items
            ]


class OnlineLLMTest(unittest.TestCase):
    """One server per test so worker counters and the tag cache start clean."""

    BVID = "BV1LLM00001"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tags_dir = Path(self._tmp.name) / "tags"
        self.classifier = StubClassifier()
        self.server = make_server(
            "127.0.0.1", 0, self.tags_dir, online_backend="llm", online_classifier=self.classifier
        )
        self.server.quiet = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def call(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None

    def health(self) -> dict:
        status, body = self.call("GET", "/api/v1/health")
        self.assertEqual(status, 200, body)
        return body

    def decisions(self, dmids, texts=None, labels: bool = False, bvid: str | None = None,
                  ephemeral: bool = False):
        payload = {"bvid": bvid or self.BVID, "dmids": dmids}
        if texts is not None:
            payload["texts"] = texts
        if labels:
            payload["labels"] = True
        if ephemeral:
            payload["ephemeral"] = True
        status, body = self.call("POST", "/api/v1/decisions", payload)
        self.assertEqual(status, 200, body)
        return body

    def wait_idle(self, expect: int = 1, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        body = self.health()
        while time.time() < deadline:
            body = self.health()
            if body["llm_pending"] == 0 and body["llm_done"] + body["llm_failed"] >= expect:
                return body
            time.sleep(0.02)
        self.fail(f"worker did not finish: {body}")

    def test_health_reports_backend_and_counters(self):
        body = self.health()
        self.assertEqual(body["online_backend"], "llm")
        self.assertEqual(body["llm_pending"], 0)
        self.assertEqual(body["llm_done"], 0)
        self.assertEqual(body["llm_failed"], 0)
        self.assertEqual(body["llm_last_error"], "")

    def test_first_response_uses_rules_then_worker_upgrades(self):
        body = self.decisions(["1"], {"1": "哈哈哈哈笑死我了"}, labels=True)
        self.assertFalse(body["found"])
        self.assertEqual(body["online"], 1)
        first = body["labels"]["1"]
        self.assertEqual(first["label"], "humor")  # instant rule label
        self.assertNotEqual(first["label"], StubClassifier.LABEL)

        health = self.wait_idle()
        self.assertEqual(health["llm_done"], 1)
        self.assertEqual(len(self.classifier.calls), 1)

        upgraded = self.decisions(["1"], labels=True)
        self.assertTrue(upgraded["found"])
        self.assertEqual(upgraded["online"], 0)
        self.assertEqual(upgraded["labels"]["1"]["label"], StubClassifier.LABEL)
        self.assertAlmostEqual(upgraded["labels"]["1"]["score"], 0.9, places=3)
        stored = self.server.online[self.BVID + "|1"]
        self.assertEqual(stored["source"], "llm-online")
        self.assertEqual(stored["dominant"], StubClassifier.LABEL)

    def test_same_comment_is_upgraded_once(self):
        self.decisions(["2"], {"2": "这首歌太治愈了"})
        self.decisions(["2"], {"2": "这首歌太治愈了"})
        health = self.wait_idle()
        self.assertEqual(health["llm_done"], 1)
        self.decisions(["2"], {"2": "这首歌太治愈了"})
        self.assertEqual(self.health()["llm_done"], 1)
        self.assertEqual(len(self.classifier.calls), 1)

    def test_batch_upgrades_in_one_call(self):
        texts = {"10": "哈哈哈哈哈哈", "11": "歌词真好", "12": "1111"}
        body = self.decisions(list(texts), texts)
        self.assertEqual(body["online"], 3)
        health = self.wait_idle(expect=3)
        self.assertEqual(health["llm_done"], 3)
        self.assertEqual(len(self.classifier.calls), 1)
        self.assertEqual(sorted(self.classifier.calls[0]), sorted(texts.values()))

    def test_classifier_failure_keeps_the_server_serving(self):
        self.classifier.fail = True
        body = self.decisions(["3"], {"3": "哈哈哈"})
        self.assertEqual(body["online"], 1)
        health = self.wait_idle()
        self.assertEqual(health["llm_failed"], 1)
        self.assertIn("stub classifier unavailable", health["llm_last_error"])
        again = self.decisions(["3"], {"3": "哈哈哈"})
        self.assertEqual(again["online"], 0)
        self.assertTrue(again["found"])

    def test_llm_backend_without_credentials_raises(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("DM_LLM_")}
        env["DM_LLM_ENV_FILE"] = "/nonexistent/dmf-test.env"
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                make_server("127.0.0.1", 0, self.tags_dir, online_backend="llm")
            auto = make_server("127.0.0.1", 0, self.tags_dir, online_backend="auto")
        self.assertEqual(auto.online_backend, "rules")
        self.assertIsNone(auto.llm_worker)
        auto.server_close()

    def test_default_backend_stays_rules(self):
        server = make_server("127.0.0.1", 0, self.tags_dir)
        self.assertEqual(server.online_backend, "rules")
        self.assertIsNone(server.llm_worker)
        server.server_close()

    def test_rules_backend_does_not_queue_llm_work(self):
        server = make_server("127.0.0.1", 0, self.tags_dir, online_backend="rules")
        server.quiet = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            payload = json.dumps(
                {"bvid": self.BVID, "dmids": ["4"], "texts": {"4": "哈哈哈"}}
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://{host}:{port}/api/v1/decisions",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read())
            self.assertEqual(body["online"], 1)
            self.assertEqual(len(self.classifier.calls), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_ephemeral_items_are_not_written_to_the_cache(self):
        body = self.decisions(
            ["live1"], {"live1": "哈哈哈哈"}, labels=True, bvid="BV1LLM00002", ephemeral=True
        )
        self.assertEqual(body["online"], 0)
        self.assertIn("live1", body["labels"])
        self.wait_idle()
        self.assertIsNone(cache.load_tags("BV1LLM00002", self.tags_dir))


if __name__ == "__main__":
    unittest.main()
