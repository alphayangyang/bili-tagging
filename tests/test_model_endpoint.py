"""Tests for GET /api/v1/model, the raw linear-model endpoint used by v0.4."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from danmaku_filter.server import make_server

BVID = "BV1TEST00001"


def _model(kind: str = "centroid", classes=None) -> dict:
    return {
        "schema": 1,
        "kind": kind,
        "labels": classes or ["humor", "other"],
        "vectorizer": {"dim": 8, "ngram": [1, 3], "n_docs": 10, "idf": {"1": 1.5}},
        "weights": {"humor": {"1": 0.5}, "other": {"2": 0.5}},
    }


class ModelEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.tags_dir = root / "tags"
        cls.tags_dir.mkdir(parents=True, exist_ok=True)
        cls.models_dir = root / "models"
        cls.models_dir.mkdir(parents=True, exist_ok=True)
        (cls.models_dir / (BVID + ".json")).write_text(json.dumps(_model()), encoding="utf-8")
        (cls.models_dir / "_global.json").write_text(
            json.dumps(_model("softmax", ["humor", "other", "information"])), encoding="utf-8"
        )
        cls.server = make_server("127.0.0.1", 0, cls.tags_dir, models_dir=cls.models_dir)
        cls.server.quiet = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def get(self, path: str):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_serves_video_model(self) -> None:
        status, payload = self.get("/api/v1/model?bvid=" + BVID)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "video")
        self.assertEqual(payload["model"]["kind"], "centroid")
        self.assertEqual(payload["model"]["labels"], ["humor", "other"])

    def test_falls_back_to_global_model(self) -> None:
        status, payload = self.get("/api/v1/model?bvid=BV1UNKNOWN0001")
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "global")
        self.assertEqual(payload["model"]["kind"], "softmax")

    def test_rejects_invalid_bvid(self) -> None:
        status, payload = self.get("/api/v1/model?bvid=../../etc/passwd")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_no_bvid_returns_global(self) -> None:
        status, payload = self.get("/api/v1/model")
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "global")

    def test_reports_null_model_on_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            server = make_server("127.0.0.1", 0, self.tags_dir, models_dir=Path(empty) / "models")
            server.quiet = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                url = f"http://{host}:{port}/api/v1/model?bvid={BVID}"
                with urllib.request.urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response_status := response.status, 200)
                self.assertEqual(payload["model"], None)
                self.assertEqual(payload["source"], None)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()