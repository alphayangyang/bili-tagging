"""Unit tests for danmaku_filter.pipeline.reclassify (offline, rules backend)."""
import tempfile
import unittest

from danmaku_filter import cache, pipeline
from danmaku_filter.taxonomy import CATEGORY_IDS, FALLBACK_ID

BVID = "BV1GJ411x7h7"
KNOWN_LABELS = set(CATEGORY_IDS) | {FALLBACK_ID}
TEXTS = ["2333333", "哈哈哈哈哈", "这里第三秒的转场好绝"]


def _item(text: str, label: str = FALLBACK_ID) -> dict:
    return {
        "text": text,
        "time_ms": 1000,
        "mode": 1,
        "tags": {label: 1.0},
        "dominant": label,
        "score": 1.0,
        "evidence": "cached",
        "source": "rules",
    }


def _payload(items: dict) -> dict:
    return {
        "schema": 2,
        "bvid": BVID,
        "aid": 1,
        "cid": 2,
        "duration": 213,
        "segments": 1,
        "backend": "rules",
        "model": None,
        "created": "2026-10-01T00:00:00Z",
        "items": items,
    }


class ReclassifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tags_dir = self.tmp.name
        self.items = {str(1000 + i): _item(text) for i, text in enumerate(TEXTS)}
        cache.save_tags(BVID, _payload(self.items), self.tags_dir)

    def test_relabels_every_item_and_updates_metadata(self):
        summary = pipeline.reclassify(BVID, backend="rules", tags_dir=self.tags_dir)
        self.assertEqual(summary["total"], len(TEXTS))
        self.assertEqual(summary["labeled"], len(TEXTS))
        self.assertFalse(summary["dry_run"])
        payload = cache.load_tags(BVID, self.tags_dir)
        self.assertEqual(payload["backend"], "rules")
        self.assertIsNone(payload["model"])
        self.assertEqual(payload["schema"], 2)
        self.assertEqual(payload["relabeled_from"], {"backend": "rules", "model": None})
        self.assertEqual(list(payload["items"].keys()), list(self.items.keys()))
        for dmid, item in payload["items"].items():
            self.assertEqual(item["text"], self.items[dmid]["text"])
            self.assertIn(item["dominant"], KNOWN_LABELS)
            self.assertGreaterEqual(item["score"], 0.0)
            self.assertLessEqual(item["score"], 1.0)
            self.assertIn(item["source"], {"rules", "llm", "user"})
        self.assertEqual(sum(summary["distribution"].values()), len(TEXTS))

    def test_dry_run_leaves_the_file_untouched(self):
        path = cache.tags_path(BVID, self.tags_dir)
        before = path.read_bytes()
        summary = pipeline.reclassify(BVID, dry_run=True, tags_dir=self.tags_dir)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(path.read_bytes(), before)

    def test_limit_keeps_remaining_cached_labels(self):
        summary = pipeline.reclassify(BVID, limit=1, tags_dir=self.tags_dir)
        self.assertEqual(summary["labeled"], 1)
        payload = cache.load_tags(BVID, self.tags_dir)
        for dmid, item in list(payload["items"].items())[1:]:
            self.assertEqual(item, self.items[dmid])

    def test_missing_cache_raises(self):
        with self.assertRaises(FileNotFoundError):
            pipeline.reclassify("BV1missing", tags_dir=self.tags_dir)

    def test_empty_items_raise(self):
        cache.save_tags(BVID, _payload({}), self.tags_dir)
        with self.assertRaises(ValueError):
            pipeline.reclassify(BVID, tags_dir=self.tags_dir)

    def test_negative_limit_raises(self):
        with self.assertRaises(ValueError):
            pipeline.reclassify(BVID, limit=-1, tags_dir=self.tags_dir)


if __name__ == "__main__":
    unittest.main()
