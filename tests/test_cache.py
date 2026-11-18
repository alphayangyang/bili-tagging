"""Unit tests for danmaku_filter.cache: paths, atomic writes, tolerant reads."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from danmaku_filter import cache


class PathTest(unittest.TestCase):
    def test_tags_dir_and_path(self) -> None:
        self.assertEqual(cache.tags_dir("tmp/tags"), Path("tmp/tags"))
        self.assertEqual(
            cache.tags_path("BV1GJ411x7h7", "tmp/tags"), Path("tmp/tags/BV1GJ411x7h7.json")
        )

    def test_invalid_bvid_is_rejected(self) -> None:
        for bvid in ("", "../escape", "a/b", "a" * 65, "BV 1"):
            with self.assertRaises(ValueError):
                cache.tags_path(bvid, "tmp/tags")
        with self.assertRaises(ValueError):
            cache.add_pending("../escape", ["1"], "tmp/pending")


class TagsCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data" / "tags"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roundtrip_creates_directory_and_keeps_unicode(self) -> None:
        payload = {"schema": 2, "bvid": "BV1test", "items": {"345": {"text": "中文弹幕"}}}
        cache.save_tags("BV1test", payload, self.root)
        self.assertEqual(cache.load_tags("BV1test", self.root), payload)
        self.assertEqual([path.name for path in self.root.iterdir()], ["BV1test.json"])
        self.assertIn("中文弹幕", (self.root / "BV1test.json").read_text(encoding="utf-8"))

    def test_missing_and_corrupt_files_load_as_none(self) -> None:
        self.assertIsNone(cache.load_tags("BVmissing", self.root))
        self.root.mkdir(parents=True)
        (self.root / "BVbroken.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(cache.load_tags("BVbroken", self.root))
        (self.root / "BVlist.json").write_text("[1, 2]", encoding="utf-8")
        self.assertIsNone(cache.load_tags("BVlist", self.root))

    def test_failed_write_keeps_previous_file_and_cleans_tmp(self) -> None:
        cache.save_tags("BV1test", {"version": 1}, self.root)
        with mock.patch("danmaku_filter.cache.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                cache.save_tags("BV1test", {"version": 2}, self.root)
        self.assertEqual(cache.load_tags("BV1test", self.root), {"version": 1})
        self.assertEqual([path.name for path in self.root.iterdir()], ["BV1test.json"])

    def test_save_rejects_non_dict_payload(self) -> None:
        with self.assertRaises(ValueError):
            cache.save_tags("BV1test", ["not", "an", "object"], self.root)


class OverridesCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data" / "overrides"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roundtrip_uses_items_envelope(self) -> None:
        overrides = {"345": {"label": "humor", "at": "2026-10-21T12:00:00Z"}}
        cache.save_overrides("BV1test", overrides, self.root)
        self.assertEqual(cache.load_overrides("BV1test", self.root), overrides)
        on_disk = json.loads((self.root / "BV1test.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, {"items": overrides})

    def test_missing_or_corrupt_file_returns_empty_mapping(self) -> None:
        self.assertEqual(cache.load_overrides("BVmissing", self.root), {})
        self.root.mkdir(parents=True)
        (self.root / "BVbroken.json").write_text("not json", encoding="utf-8")
        self.assertEqual(cache.load_overrides("BVbroken", self.root), {})

    def test_flat_legacy_file_is_tolerated(self) -> None:
        self.root.mkdir(parents=True)
        legacy = {"345": {"label": "humor", "at": "2026-10-21T12:00:00Z"}}
        (self.root / "BVlegacy.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(cache.load_overrides("BVlegacy", self.root), legacy)


class PendingCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data" / "pending"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_pending_dedupes_within_and_across_calls(self) -> None:
        self.assertEqual(cache.add_pending("BV1test", ["111", "222", "111"], self.root), 2)
        self.assertEqual(cache.add_pending("BV1test", ["222", "333"], self.root), 1)
        self.assertEqual(cache.load_pending("BV1test", self.root), ["111", "222", "333"])
        self.assertEqual(cache.add_pending("BV1test", ["333"], self.root), 0)
        payload = json.loads((self.root / "BV1test.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["bvid"], "BV1test")
        self.assertEqual(payload["dmids"], ["111", "222", "333"])
        self.assertTrue(payload["updated"].endswith("Z"))

    def test_missing_pending_file_is_empty(self) -> None:
        self.assertEqual(cache.load_pending("BVnothing", self.root), [])


if __name__ == "__main__":
    unittest.main()
