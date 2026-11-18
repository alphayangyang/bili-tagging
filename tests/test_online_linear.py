"""Online labelling with a trained linear model (L1 of the cascade)."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from danmaku_filter.classify.linear import LinearTagger
from danmaku_filter.server import make_server

CORPUS = [
    ("哈哈哈哈这也太好笑了", "humor"),
    ("笑死我了哈哈哈哈", "humor"),
    ("这翻译真的太逗了", "humor"),
    ("up主就是个傻逼", "attack"),
    ("去死吧你", "attack"),
    ("什么垃圾东西", "attack"),
    ("后面剧透一下凶手是他", "spoiler"),
    ("结尾反转我还真没想到", "spoiler"),
    ("这首歌真的陪我走过很多年", "emotional_resonance"),
    ("太好听了感动哭了", "emotional_resonance"),
]


def train_model(path: Path, kind: str = "centroid") -> None:
    texts = [t for t, _ in CORPUS]
    labels = [l for _, l in CORPUS]
    LinearTagger(kind=kind).train(texts, labels).save(path)


class ServerTestBase(unittest.TestCase):
    online_backend = "linear"

    # Model files to create before the server starts (name -> kind).
    model_files = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tags = self.root / "tags"
        self.models = self.root / "models"
        self.tags.mkdir()
        self.models.mkdir()
        for name, kind in self.model_files.items():
            train_model(self.models / name, kind)
        self.server = make_server(
            port=0,
            tags_dir=self.tags,
            online_backend=self.online_backend,
            online_classifier=False,  # no LLM teacher in these tests
            models_dir=self.models,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.url = "http://%s:%d/api/v1/decisions" % (host, port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def post(self, body):
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))


class LinearBackendTest(ServerTestBase):
    model_files = {"BV1LINEAR001.json": "centroid"}

    def test_model_labelling_skips_llm_queue(self):
        out = self.post(
            {
                "bvid": "BV1LINEAR001",
                "dmids": ["1", "2"],
                "texts": {"1": "up主就是个傻逼", "2": "笑死我了哈哈哈哈"},
                "labels": True,
                "prefs": {"min_confidence": 0.0},
            }
        )
        self.assertEqual(out["online"], 2)
        self.assertEqual(out["labels"]["1"]["label"], "attack")
        self.assertEqual(out["labels"]["2"]["label"], "humor")
        self.assertEqual(self.server.llm_worker, None)

    def test_model_items_are_persisted_with_linear_source(self):
        train_model(self.models / "BV1LINEAR002.json")
        self.post(
            {
                "bvid": "BV1LINEAR002",
                "dmids": ["1"],
                "texts": {"1": "后面剧透一下凶手是他"},
            }
        )
        payload = json.loads((self.tags / "BV1LINEAR002.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["items"]["1"]["source"], "linear-online")
        self.assertEqual(payload["items"]["1"]["dominant"], "spoiler")

    def test_global_model_is_used_for_unknown_video(self):
        train_model(self.models / "_global.json")
        out = self.post(
            {
                "bvid": "BV1LINEAR003",
                "dmids": ["1"],
                "texts": {"1": "什么垃圾东西"},
                "labels": True,
                "prefs": {"min_confidence": 0.0},
            }
        )
        self.assertEqual(out["labels"]["1"]["label"], "attack")

    def test_ephemeral_does_not_write_cache(self):
        train_model(self.models / "_global.json")
        out = self.post(
            {
                "bvid": "BV1LINEAR004",
                "dmids": ["1"],
                "texts": {"1": "什么垃圾东西"},
                "ephemeral": True,
                "labels": True,
                "prefs": {"min_confidence": 0.0},
            }
        )
        self.assertEqual(out["labels"]["1"]["label"], "attack")
        self.assertFalse((self.tags / "BV1LINEAR004.json").exists())


class LinearWithoutModelTest(ServerTestBase):
    model_files = {}

    def test_falls_back_when_no_model_exists(self):
        out = self.post(
            {
                "bvid": "BV1LINEAR009",
                "dmids": ["1"],
                "texts": {"1": "什么垃圾东西"},
                "labels": True,
                "prefs": {"min_confidence": 0.0},
            }
        )
        self.assertEqual(out["online"], 1)
        self.assertIn("1", out["labels"])
        self.assertNotEqual(self.server.online_backend, "linear")



class LowConfidenceUpgradeTest(ServerTestBase):
    """Linear answers fast; weak labels are re-checked by the LLM teacher."""

    model_files = {"_global.json": "centroid"}

    def test_weak_label_is_queued_for_upgrade(self):
        import time

        from danmaku_filter.server import _LLMWorker
        from danmaku_filter.types import ClassificationResult

        class Stub:
            def __init__(self):
                self.calls = []

            def classify_batch(self, chunk):
                self.calls.append(len(chunk))
                return [
                    ClassificationResult(
                        label="attack",
                        confidence=0.9,
                        rationale="",
                        source="llm",
                        tags={"attack": 0.9},
                    )
                    for _ in chunk
                ]

        stub = Stub()
        server = self.server
        server.llm_worker = _LLMWorker(server, stub, batch_size=4)
        server.llm_worker.start()
        try:
            out = self.post(
                {
                    "bvid": "BV1LINEAR010",
                    "dmids": ["1"],
                    "texts": {"1": "嗯嗯嗯"},
                    "labels": True,
                    "prefs": {"min_confidence": 0.0},
                }
            )
            self.assertIn("1", out["labels"])
            for _ in range(50):
                if server.llm_worker.done:
                    break
                time.sleep(0.05)
            out2 = self.post(
                {
                    "bvid": "BV1LINEAR010",
                    "dmids": ["1"],
                    "labels": True,
                    "prefs": {"min_confidence": 0.0},
                }
            )
            self.assertEqual(out2["labels"]["1"]["label"], "attack")
            self.assertTrue(stub.calls)
        finally:
            server.llm_worker.stop()

if __name__ == "__main__":
    unittest.main()
