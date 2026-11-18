"""Unit tests for danmaku_filter.pipeline (network-free, fetch is monkeypatched)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from danmaku_filter import cache, pipeline
from danmaku_filter.taxonomy import CATEGORY_IDS, FALLBACK_ID
from danmaku_filter.types import ClassificationResult

FIXTURE = Path(__file__).parent / "fixtures" / "seg_sample.bin"
BVID = "BV1GJ411x7h7"
INFO = {"bvid": BVID, "aid": 80433022, "cid": 137649199, "duration": 213, "title": "test"}
KNOWN_LABELS = set(CATEGORY_IDS) | {FALLBACK_ID}
TOTAL = 150
FIRST_DMID = "34576873522987015"
ITEM_FIELDS = {"text", "time_ms", "mode", "tags", "dominant", "score", "evidence", "source"}
SUMMARY_FIELDS = {
    "bvid",
    "aid",
    "cid",
    "segments",
    "total",
    "labeled",
    "backend",
    "model",
    "path",
}


class _FakeClassifier:
    """Two-batch fake: one weird multi-label item, plain items otherwise."""

    def __init__(self) -> None:
        self.batches = 0
        self.seen = 0

    def classify_batch(self, batch) -> list[ClassificationResult]:
        self.batches += 1
        self.seen += len(batch)
        results = []
        for danmaku in batch:
            if danmaku.dmid == FIRST_DMID:
                results.append(
                    ClassificationResult(
                        label="information",
                        confidence=0.6,
                        rationale="matched cue: 金曲",
                        source="llm",
                        tags={"information": 1.5, "bogus": 0.9, "humor": -0.2},
                    )
                )
            else:
                results.append(
                    ClassificationResult(
                        label="humor",
                        confidence=0.8,
                        rationale="matched cue: joke",
                        source="llm",
                        tags={"attack": 0.5},
                    )
                )
        return results


class _BrokenClassifier:
    def classify_batch(self, batch) -> list[ClassificationResult]:
        raise RuntimeError("backend crashed")


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data" / "tags"
        self.segment = FIXTURE.read_bytes()
        self._patches = [
            mock.patch.object(pipeline.fetch, "get_video_info", return_value=dict(INFO)),
            mock.patch.object(
                pipeline.fetch, "fetch_all_segments", return_value=[self.segment]
            ),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self._patches):
            patcher.stop()
        self._tmp.cleanup()

    def tags_path(self) -> Path:
        return cache.tags_path(BVID, self.root)

    def run_precompute(self, **kwargs):
        return pipeline.precompute(BVID, tags_dir=self.root, **kwargs)

    def test_rules_backend_end_to_end(self) -> None:
        summary = self.run_precompute()
        self.assertEqual(set(summary), SUMMARY_FIELDS)
        self.assertEqual(summary["bvid"], BVID)
        self.assertEqual(summary["aid"], 80433022)
        self.assertEqual(summary["cid"], 137649199)
        self.assertEqual(summary["segments"], 1)
        self.assertEqual(summary["total"], TOTAL)
        self.assertEqual(summary["labeled"], TOTAL)
        self.assertEqual(summary["backend"], "rules")
        self.assertIsNone(summary["model"])
        self.assertEqual(Path(summary["path"]), self.tags_path())

        cached = cache.load_tags(BVID, self.root)
        self.assertEqual(cached["schema"], 2)
        self.assertEqual(cached["bvid"], BVID)
        self.assertEqual(cached["segments"], 1)
        self.assertTrue(cached["created"].endswith("Z"))
        self.assertEqual(len(cached["items"]), TOTAL)
        item = cached["items"][FIRST_DMID]
        self.assertEqual(
            item["text"], "80年代迪厅必放金曲"
        )
        self.assertEqual(item["time_ms"], 95453)
        self.assertEqual(item["mode"], 1)

        from danmaku_filter.dmproto import parse_segment

        for danmaku in parse_segment(self.segment):
            item = cached["items"][danmaku.dmid]
            self.assertEqual(set(item), ITEM_FIELDS)
            self.assertEqual(item["text"], danmaku.text)
            self.assertEqual(item["time_ms"], danmaku.time_ms)
            self.assertEqual(item["mode"], danmaku.mode)
            self.assertIn(item["dominant"], KNOWN_LABELS)
            self.assertEqual(set(item["tags"]), {item["dominant"]})
            self.assertEqual(item["score"], item["tags"][item["dominant"]])
            self.assertGreaterEqual(item["score"], 0.0)
            self.assertLessEqual(item["score"], 1.0)
            self.assertIn(item["source"], {"rules", "llm"})

    def test_duplicate_segments_are_deduped_by_dmid(self) -> None:
        with mock.patch.object(
            pipeline.fetch, "fetch_all_segments", return_value=[self.segment, self.segment]
        ):
            summary = self.run_precompute()
        self.assertEqual(summary["segments"], 2)
        self.assertEqual(summary["total"], TOTAL)
        self.assertEqual(len(cache.load_tags(BVID, self.root)["items"]), TOTAL)

    def test_limit_caps_labeled_items_but_keeps_total(self) -> None:
        summary = self.run_precompute(limit=10)
        self.assertEqual(summary["total"], TOTAL)
        self.assertEqual(summary["labeled"], 10)
        self.assertEqual(len(cache.load_tags(BVID, self.root)["items"]), 10)

    def test_classifier_tags_are_clamped_and_sanitised(self) -> None:
        fake = _FakeClassifier()
        with mock.patch.object(pipeline, "get_classifier", return_value=fake):
            summary = self.run_precompute()
        self.assertEqual(fake.batches, 2)
        self.assertEqual(fake.seen, TOTAL)
        self.assertEqual(summary["labeled"], TOTAL)
        items = cache.load_tags(BVID, self.root)["items"]

        weird = items[FIRST_DMID]
        self.assertEqual(weird["dominant"], "information")
        self.assertEqual(weird["tags"], {"information": 1.0, "humor": 0.0})
        self.assertEqual(weird["score"], 1.0)
        self.assertEqual(weird["source"], "llm")
        self.assertEqual(weird["evidence"], "matched cue: 金曲")

        plain = items["34576873522987016"] if "34576873522987016" in items else next(
            item for dmid, item in items.items() if dmid != FIRST_DMID
        )
        self.assertEqual(plain["dominant"], "humor")
        self.assertEqual(plain["score"], 0.8)
        self.assertEqual(plain["tags"], {"attack": 0.5, "humor": 0.8})
        self.assertEqual(plain["evidence"], "matched cue: joke")

    def test_llm_backend_without_settings_fails_before_fetching(self) -> None:
        with mock.patch(
            "danmaku_filter.classify.llm.settings", side_effect=RuntimeError("no api key")
        ):
            with self.assertRaises(RuntimeError):
                self.run_precompute(backend="llm")
        self.assertFalse(self.tags_path().exists())
        pipeline.fetch.get_video_info.assert_not_called()

    def test_unknown_backend_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.run_precompute(backend="bogus")
        self.assertFalse(self.tags_path().exists())

    def test_classifier_failure_leaves_no_partial_cache(self) -> None:
        with mock.patch.object(pipeline, "get_classifier", return_value=_BrokenClassifier()):
            with self.assertRaises(RuntimeError):
                self.run_precompute()
        self.assertFalse(self.tags_path().exists())

    def test_parse_failure_leaves_no_partial_cache(self) -> None:
        with mock.patch.object(
            pipeline.dmproto, "parse_segment", side_effect=ValueError("bad segment")
        ):
            with self.assertRaises(ValueError):
                self.run_precompute()
        self.assertFalse(self.tags_path().exists())

    def test_negative_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.run_precompute(limit=-1)
        self.assertFalse(self.tags_path().exists())

    def test_fetch_arguments_match_contract(self) -> None:
        self.run_precompute(max_segments=3)
        pipeline.fetch.get_video_info.assert_called_once_with(BVID)
        pipeline.fetch.fetch_all_segments.assert_called_once_with(
            80433022, 137649199, 213, max_segments=3
        )


if __name__ == "__main__":
    unittest.main()
