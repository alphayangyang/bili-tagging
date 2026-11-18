"""Tests for the OpenAI-compatible LLM classifier using a fake local endpoint."""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from danmaku_filter.classify import llm
from danmaku_filter.types import Danmaku

FENCE = "```"


class FakeEndpoint(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        self.server.requests.append(json.loads(raw))
        self.server.paths.append(self.path)
        status, payload = self.server.responses.pop(0)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence test output
        pass


def _completion(rows: list[dict]) -> dict:
    return {"choices": [{"message": {"content": json.dumps(rows)}}]}


class LLMClassifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEndpoint)
        self.server.requests = []
        self.server.paths = []
        self.server.responses = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        port = self.server.server_address[1]
        env = {
            "DM_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "DM_LLM_MODEL": "fake-lfm2",
            "DM_LLM_BATCH": "2",
            "DM_LLM_TIMEOUT": "5",
            "DM_LLM_RETRIES": "2",
            # Keep the developer's real .env.local out of the tests.
            "DM_LLM_ENV_FILE": "/nonexistent/dmf-test.env",
        }
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("DM_LLM_API_KEY", None)

    def test_local_endpoint_uses_default_key(self) -> None:
        base, key, model = llm.settings()
        self.assertTrue(base.endswith("/v1"))
        self.assertEqual(key, "ollama")
        self.assertEqual(model, "fake-lfm2")

    def test_remote_endpoint_still_requires_key(self) -> None:
        with mock.patch.dict(os.environ, {"DM_LLM_BASE_URL": "https://api.deepseek.com/v1"}):
            with self.assertRaises(RuntimeError):
                llm.settings()

    def test_batch_order_labels_and_clamping(self) -> None:
        self.server.responses = [
            (
                200,
                _completion(
                    [
                        {"id": 1, "label": "attack", "confidence": 1.7},
                        {"id": 2, "label": "not_a_category", "confidence": -1},
                    ]
                ),
            ),
            (200, _completion([{"id": 3, "label": "spoiler", "confidence": "bad"}])),
        ]
        items = [Danmaku(text="trash", time_ms=1000 * i) for i in range(3)]
        results = llm.LLMClassifier().classify_batch(items)
        self.assertEqual([r.label for r in results], ["attack", "other", "spoiler"])
        self.assertEqual(results[0].confidence, 1.0)
        self.assertEqual(results[1].confidence, 0.0)
        self.assertEqual(results[2].confidence, 0.0)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.server.requests[0]["model"], "fake-lfm2")
        user_prompt = self.server.requests[0]["messages"][1]["content"]
        self.assertIn("trash", user_prompt)

    def test_fenced_code_block_is_unwrapped(self) -> None:
        content = FENCE + "json\n[{\"label\": \"humor\", \"confidence\": 0.5}]\n" + FENCE
        payload = {"choices": [{"message": {"content": content}}]}
        self.server.responses = [(200, payload)]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="lol")])
        self.assertEqual(results[0].label, "humor")

    def test_single_object_answer_is_accepted(self) -> None:
        content = '{"label": "information", "confidence": 0.7}'
        self.server.responses = [(200, {"choices": [{"message": {"content": content}}]})]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="cv: 123")])
        self.assertEqual(results[0].label, "information")

    def test_retries_on_server_error(self) -> None:
        self.server.responses = [
            (500, {"error": "boom"}),
            (503, {"error": "busy"}),
            (200, _completion([{"label": "emotional_resonance", "confidence": 0.9}])),
        ]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="破防了")])
        self.assertEqual(results[0].label, "emotional_resonance")
        self.assertEqual(len(self.server.requests), 3)

    def test_raw_newlines_inside_rationale_are_tolerated(self) -> None:
        # Local models sometimes emit a literal newline inside a JSON string;
        # json.loads(strict=False) must still recover the batch.
        content = (
            '[{"id": 1, "label": "humor", "confidence": 0.6,'
            ' "rationale": "first line\nsecond line"}]'
        )
        self.server.responses = [(200, {"choices": [{"message": {"content": content}}]})]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="lol")])
        self.assertEqual(results[0].label, "humor")
        self.assertIn("second line", results[0].rationale)

    def test_relevance_vector_is_parsed(self) -> None:
        self.server.responses = [
            (
                200,
                _completion(
                    [
                        {"id": 1, "scores": {"humor": 0.8, "spoiler": 0.4}, "note": "joke about ending"},
                        {"id": 2, "scores": {"attack": 0.2, "humor": 0.9}, "note": "roast"},
                    ]
                ),
            )
        ]
        items = [Danmaku(text="a"), Danmaku(text="b")]
        results = llm.LLMClassifier().classify_batch(items)
        self.assertEqual(results[0].tags, {"humor": 0.8, "spoiler": 0.4})
        self.assertEqual(results[0].label, "humor")
        self.assertEqual(results[0].confidence, 0.8)
        self.assertEqual(results[0].rationale, "joke about ending")
        # ties are impossible, but ordering must still follow the score
        self.assertEqual(results[1].label, "humor")

    def test_vector_ties_resolve_in_taxonomy_order(self) -> None:
        self.server.responses = [
            (200, _completion([{"id": 1, "scores": {"spoiler": 0.5, "attack": 0.5}}]))
        ]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="?")])
        self.assertEqual(results[0].label, "attack")
        self.assertEqual(results[0].confidence, 0.5)
        self.assertEqual(len(self.server.requests), 1)

    def test_empty_vector_falls_back_to_other(self) -> None:
        self.server.responses = [
            (200, _completion([{"id": 1, "scores": {"not_a_tag": 0.9}}, {"id": 2}]))
        ]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="a"), Danmaku(text="b")])
        self.assertEqual(results[0].label, "other")
        self.assertEqual(results[1].label, "other")

    def test_env_file_is_loaded_but_never_overrides_real_env(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env"
            path.write_text(
                "# comment\nexport DM_LLM_MODEL=from-file\nDM_LLM_BATCH=7\nQUOTED=1\n",
                encoding="utf-8",
            )
            values = llm._read_env_file(path)
        self.assertEqual(values["DM_LLM_MODEL"], "from-file")
        self.assertEqual(values["DM_LLM_BATCH"], "7")
        self.assertEqual(llm._read_env_file(Path("/nonexistent/x")), {})

    def test_client_error_fails_immediately(self) -> None:
        self.server.responses = [(400, {"error": "bad request"})]
        with self.assertRaises(RuntimeError):
            llm.LLMClassifier().classify_batch([Danmaku(text="hi")])
        self.assertEqual(len(self.server.requests), 1)


if __name__ == "__main__":
    unittest.main()


class OllamaNativeTest(unittest.TestCase):
    """The native Ollama path passes num_ctx per request so small GPUs fit."""

    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEndpoint)
        self.server.requests = []
        self.server.paths = []
        self.server.responses = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        port = self.server.server_address[1]
        env = {
            "DM_LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "DM_LLM_MODEL": "qwen3:4b",
            "DM_LLM_BATCH": "2",
            "DM_LLM_TIMEOUT": "5",
            "DM_LLM_RETRIES": "1",
            "DM_LLM_ENV_FILE": "/nonexistent/dmf-test.env",
            "DM_LLM_OLLAMA": "1",
            "DM_LLM_NUM_CTX": "4096",
        }
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("DM_LLM_API_KEY", None)

    def test_native_path_sends_options_and_parses_message(self) -> None:
        content = json.dumps([{"id": 1, "scores": {"humor": 0.9}}])
        self.server.responses = [(200, {"message": {"role": "assistant", "content": content}})]
        result = llm.LLMClassifier().classify(Danmaku(text="哈哈哈哈哈"))
        self.assertEqual(result.label, "humor")
        self.assertEqual(self.server.paths, ["/api/chat"])
        payload = self.server.requests[0]
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertFalse(payload["stream"])
        # Ollama format="json" collapses multi-row answers to one object.
        self.assertNotIn("format", payload)
        self.assertIn("JSON array with exactly 1 objects", payload["messages"][1]["content"])
        self.assertGreaterEqual(payload["options"]["num_predict"], 256)
        self.assertNotIn("Authorization", payload)

    def test_native_path_retries_server_errors(self) -> None:
        self.server.responses = [
            (503, {"error": "loading"}),
            (200, {"message": {"content": json.dumps([{"id": 1, "scores": {"other": 0.5}}])}}),
        ]
        results = llm.LLMClassifier().classify_batch([Danmaku(text="嗯")])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].label, "other")
        self.assertEqual(self.server.paths, ["/api/chat", "/api/chat"])

    def test_detection_and_num_ctx_helpers(self) -> None:
        self.assertTrue(llm.ollama_native())
        self.assertEqual(llm.num_ctx(), 4096)
        with mock.patch.dict(os.environ, {"DM_LLM_OLLAMA": "0", "DM_LLM_NUM_CTX": ""}):
            self.assertFalse(llm.ollama_native())
            self.assertEqual(llm.num_ctx(), 0)
        with mock.patch.dict(
            os.environ,
            {"DM_LLM_BASE_URL": "http://10.211.24.105:11434/v1", "DM_LLM_NUM_CTX": ""},
        ):
            os.environ.pop("DM_LLM_OLLAMA", None)
            self.assertTrue(llm.ollama_native())
            self.assertEqual(llm.num_ctx(), 8192)
        with mock.patch.dict(
            os.environ, {"DM_LLM_BASE_URL": "https://api.deepseek.com/v1", "DM_LLM_NUM_CTX": ""}
        ):
            os.environ.pop("DM_LLM_OLLAMA", None)
            self.assertFalse(llm.ollama_native())
            self.assertEqual(llm.num_ctx(), 0)


if __name__ == "__main__":
    unittest.main()
