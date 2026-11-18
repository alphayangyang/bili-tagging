"""Tests for scripts/study/prepare_stimuli.py (pure stdlib, no network)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

STUDY_DIR = Path(__file__).resolve().parents[1] / "scripts" / "study"
if str(STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(STUDY_DIR))

import prepare_stimuli  # noqa: E402


def _stimulus(**overrides):
    row = {"bvid": "BV1GJ411x7h7", "genre": "entertainment", "density": "tbd", "label": "music"}
    row.update(overrides)
    return row


class ManifestTest(unittest.TestCase):
    def test_placeholder_is_created_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stimuli.json"
            rows, placeholder = prepare_stimuli.read_manifest(path)
            self.assertTrue(placeholder)
            self.assertEqual(len(rows), 6)
            self.assertTrue(path.is_file())
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(on_disk["placeholder"])
            self.assertEqual(len(on_disk["stimuli"]), 6)

    def test_placeholder_has_three_per_genre_after_genre_fix(self) -> None:
        rows = prepare_stimuli.load_stimuli(Path(tempfile.mkdtemp()) / "stimuli.json", create=True)
        genres = [row["genre"] for row in rows]
        self.assertEqual(genres.count("information"), 2)
        self.assertEqual(genres.count("entertainment"), 4)
        self.assertEqual({row["bvid"] for row in rows}, {
            "BV1rCxe6wEN9", "BV1GJ411x7h7", "BV1bnxr6cEJ9",
            "BV1mV4F6mE5Z", "BV1w5xh6sEEz", "BV1W18Y61Ee5",
        })
        self.assertTrue(all(row["density"] == "tbd" for row in rows))

    def test_no_create_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                prepare_stimuli.read_manifest(Path(tmp) / "missing.json", create=False)

    def test_custom_manifest_is_validated_and_not_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stimuli.json"
            path.write_text(
                json.dumps({"schema": 1, "placeholder": False, "stimuli": [_stimulus(density="high")]}),
                encoding="utf-8",
            )
            rows, placeholder = prepare_stimuli.read_manifest(path)
            self.assertFalse(placeholder)
            self.assertEqual(rows[0]["density"], "high")
            self.assertEqual(rows[0]["genre"], "entertainment")


class ValidateStimulusTest(unittest.TestCase):
    def test_defaults_density_to_tbd(self) -> None:
        row = prepare_stimuli._validate_stimulus({"bvid": "BV1GJ411x7h7", "genre": "information"})
        self.assertEqual(row["density"], "tbd")
        self.assertEqual(row["label"], "")

    def test_rejects_bad_bvid(self) -> None:
        for bvid in ("", "../etc/passwd", "BV 12", "x" * 65):
            with self.assertRaises(ValueError):
                prepare_stimuli._validate_stimulus({"bvid": bvid, "genre": "information"})

    def test_rejects_bad_genre_and_density(self) -> None:
        with self.assertRaises(ValueError):
            prepare_stimuli._validate_stimulus(_stimulus(genre="education"))
        with self.assertRaises(ValueError):
            prepare_stimuli._validate_stimulus(_stimulus(density="medium"))
        with self.assertRaises(ValueError):
            prepare_stimuli._validate_stimulus("not-an-object")


class CacheRowsTest(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "schema": 2,
            "backend": "llm",
            "model": "deepseek-flash",
            "items": {
                "1": {"text": "好笑", "time_ms": 1000, "dominant": "humor", "source": "llm"},
                "2": {"text": "这里不对劲", "time_ms": 2000, "dominant": "spoiler", "source": "llm"},
                "3": {"text": "哦", "time_ms": 3000, "dominant": "humor", "source": "rules-online"},
                "4": {"time_ms": 4000, "dominant": "humor", "source": "llm"},
                "5": "garbage",
            },
        }

    def test_cache_items_skips_unusable(self) -> None:
        rows = prepare_stimuli.cache_items(self._payload())
        self.assertEqual([row["text"] for row in rows], ["好笑", "这里不对劲", "哦"])
        self.assertEqual(prepare_stimuli.cache_items(None), [])
        self.assertEqual(prepare_stimuli.cache_items({"items": []}), [])

    def test_teacher_rows_only_llm_source(self) -> None:
        rows = prepare_stimuli.teacher_rows_from_cache(self._payload())
        self.assertEqual([row["label"] for row in rows], ["humor", "spoiler"])
        self.assertEqual(len(prepare_stimuli.cache_label_rows(self._payload())), 3)

    def test_teacher_status_flags_partial_cache(self) -> None:
        status = prepare_stimuli.teacher_status(self._payload())
        self.assertEqual(status["items"], 3)
        self.assertEqual(status["teacher_rows"], 2)
        self.assertEqual(status["backend"], "llm")
        self.assertFalse(status["teacher"])
        full = prepare_stimuli.teacher_status(
            {"backend": "llm", "items": {"1": {"text": "a", "dominant": "humor", "source": "llm"}}}
        )
        self.assertTrue(full["teacher"])


class DatasetRowsTest(unittest.TestCase):
    def test_groups_only_llm_rows(self) -> None:
        lines = [
            {"bvid": "BV1", "text": "a", "time_ms": 10, "dominant": "humor", "label_source": "llm"},
            {"bvid": "BV1", "text": "b", "time_ms": 20, "dominant": "spoiler", "label_source": "llm-llm"},
            {"bvid": "BV1", "text": "c", "time_ms": 30, "dominant": "humor", "label_source": "rules"},
            {"bvid": "BV2", "text": "d", "time_ms": 40, "dominant": "information"},
            {"bvid": "BV2", "text": "", "time_ms": 50, "dominant": "humor", "label_source": "llm"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dataset.jsonl"
            path.write_text("\n".join(json.dumps(line) for line in lines) + "\nbad json\n", encoding="utf-8")
            grouped = prepare_stimuli.read_dataset_rows(path)
        self.assertEqual(len(grouped["BV1"]), 2)
        self.assertEqual(len(grouped["BV2"]), 1)
        self.assertEqual(grouped["BV2"][0]["label"], "information")

    def test_missing_dataset_is_empty(self) -> None:
        self.assertEqual(prepare_stimuli.read_dataset_rows("/nonexistent/dataset.jsonl"), {})


class StreamTest(unittest.TestCase):
    def setUp(self) -> None:
        prepare_stimuli._STREAM_CACHE.clear()

    def test_fetch_stream_keeps_offsets_and_dedupes(self) -> None:
        from unittest import mock

        from danmaku_filter import dmproto, fetch
        from danmaku_filter.types import Danmaku

        first = Danmaku("hello", 1000, 1, "d1")
        duplicate = Danmaku("hello again", 1000, 1, "d1")
        second = Danmaku("world", 61000, 1, "d2")
        with mock.patch.object(fetch, "fetch_all_segments", return_value=[b"a", b"b"]) as called:
            with mock.patch.object(dmproto, "parse_segment", side_effect=[[first, duplicate], [second]]):
                result = prepare_stimuli.fetch_stream(
                    "BV1test", {"aid": 11, "cid": 22, "duration_s": 400}
                )
        called.assert_called_once_with(11, 22, 400, max_segments=60)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["segments"], 2)
        self.assertEqual([row["time_ms"] for row in result["items"]], [1000, 61000])
        self.assertEqual([row["text"] for row in result["items"]], ["hello", "world"])
        # The result is cached so a second call does not hit the network again.
        self.assertIs(prepare_stimuli.fetch_stream("BV1test", {}), result)

    def test_fetch_stream_falls_back_to_legacy_endpoint(self) -> None:
        from unittest import mock

        from danmaku_filter import fetch
        from danmaku_filter.types import Danmaku

        legacy = [Danmaku("one", 500, 1, "x1"), Danmaku("two", 65000, 1, "x2")]
        with mock.patch.object(fetch, "fetch_all_segments", return_value=[]):
            with mock.patch.object(fetch, "fetch_danmaku", return_value=legacy):
                result = prepare_stimuli.fetch_stream(
                    "BV1legacy", {"aid": 3, "cid": 4, "duration_s": 120}
                )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["source"], "legacy")
        self.assertEqual([row["time_ms"] for row in result["items"]], [500, 65000])

    def test_fetch_stream_reports_failures(self) -> None:
        result = prepare_stimuli.fetch_stream("BV1miss", {"aid": 0, "cid": 0})
        self.assertIn("missing aid/cid", result["error"])
        self.assertNotIn("items", result)

        from unittest import mock

        from danmaku_filter import fetch

        with mock.patch.object(fetch, "fetch_all_segments", side_effect=RuntimeError("net down")):
            result = prepare_stimuli.fetch_stream("BV1err", {"aid": 1, "cid": 2, "duration_s": 60})
        self.assertIn("net down", result["error"])


class DensityStatsTest(unittest.TestCase):
    def _items(self, times: list[int]) -> list[dict]:
        return [{"text": "t%d" % index, "time_ms": value} for index, value in enumerate(times)]

    def test_peak_per_minute(self) -> None:
        self.assertEqual(prepare_stimuli._peak_per_minute([]), 0.0)
        self.assertEqual(prepare_stimuli._peak_per_minute([0, 1000, 2000, 70000]), 2.0)
        self.assertEqual(prepare_stimuli._peak_per_minute(list(range(1000, 60001, 1000))), 60.0)
        self.assertEqual(prepare_stimuli._peak_per_minute([0, 60000]), 1.0)

    def test_density_stats_uses_duration_and_span(self) -> None:
        items = self._items(list(range(0, 10000, 1000)))
        stats = prepare_stimuli.density_stats(items, items[:3], 180)
        self.assertEqual(stats["items"], 10)
        self.assertEqual(stats["duration_s"], 180)
        self.assertAlmostEqual(stats["comments_per_min"], 3.3, delta=0.1)
        # t=0 is dropped by the >0 filter used for span and peak.
        self.assertAlmostEqual(stats["span_s"], 8.0, delta=0.1)
        self.assertAlmostEqual(stats["comments_per_min_span"], 75.0, delta=0.2)
        self.assertAlmostEqual(stats["peak_per_min"], 9.0, delta=0.1)
        self.assertEqual(stats["teacher_rows"], 3)

    def test_density_stats_without_duration_or_items(self) -> None:
        stats = prepare_stimuli.density_stats([], [], 0)
        self.assertIsNone(stats["comments_per_min"])
        self.assertEqual(stats["peak_per_min"], 0.0)

    def test_social_counts_and_hide_share(self) -> None:
        counts = prepare_stimuli.social_counts(
            {"humor": 10, "attack": 5, "conflict": 0, "spoiler": 0, "inflammatory": 1}
        )
        self.assertEqual(counts["attack"], 5)
        self.assertEqual(counts["conflict"], 0)
        self.assertEqual(counts["inflammatory"], 1)
        self.assertEqual(counts["hide_total"], 6)
        self.assertEqual(counts["labelled"], 16)
        self.assertAlmostEqual(counts["hide_share"], 0.375)
        self.assertEqual(prepare_stimuli.social_counts({})["hide_share"], 0.0)

    def test_density_feasibility(self) -> None:
        high = prepare_stimuli.density_feasibility({"items": 300, "peak_per_min": 150})
        self.assertEqual(high, {"can_high": True, "can_low": True, "suggested": "high"})
        low = prepare_stimuli.density_feasibility({"items": 300, "peak_per_min": 40})
        self.assertEqual(low, {"can_high": False, "can_low": True, "suggested": "low"})
        none = prepare_stimuli.density_feasibility({"items": 100, "peak_per_min": 500})
        self.assertEqual(none, {"can_high": False, "can_low": False, "suggested": None})


class ChunkLatencyTest(unittest.TestCase):
    def test_chunks_are_contiguous_and_never_empty(self) -> None:
        self.assertEqual(prepare_stimuli._chunks([], 3), [])
        self.assertEqual(prepare_stimuli._chunks([1, 2], 0), [])
        self.assertEqual(prepare_stimuli._chunks([1, 2], 5), [[1], [2]])
        self.assertEqual(prepare_stimuli._chunks(list(range(10)), 4), [[0, 1], [2, 3, 4], [5, 6], [7, 8, 9]])

    def test_percentile(self) -> None:
        self.assertEqual(prepare_stimuli.percentile([], 0.5), 0.0)
        self.assertEqual(prepare_stimuli.percentile([1, 2, 3, 4], 0.5), 3.0)
        self.assertEqual(prepare_stimuli.percentile([10, 20], 0.95), 20)

    def test_latency_stats(self) -> None:
        stats = prepare_stimuli.latency_stats([0.01, 0.02, 0.03, 0.04, 0.05])
        self.assertEqual(stats["n"], 5)
        self.assertAlmostEqual(stats["p50_ms"], 30.0, delta=0.01)
        self.assertAlmostEqual(stats["p95_ms"], 50.0, delta=0.01)
        self.assertAlmostEqual(stats["mean_ms"], 30.0, delta=0.01)
        self.assertEqual(prepare_stimuli.latency_stats([])["p50_ms"], None)


class TrainingTest(unittest.TestCase):
    def _rows(self, labels: list[str]) -> list[dict]:
        return [{"text": "评论 %d 号" % index, "label": labels[index % len(labels)], "time_ms": index * 1000}
                for index in range(24)]

    def test_sampling_is_deterministic_and_bounded(self) -> None:
        rows = [{"text": "字" * (index % 20 + 2) + "x%02d" % index, "label": "humor"} for index in range(100)]
        picked = prepare_stimuli.sample_rows(rows, 20, 7)
        self.assertEqual(len(picked), 20)
        self.assertEqual(picked, prepare_stimuli.sample_rows(rows, 20, 7))
        self.assertEqual(len(prepare_stimuli.sample_rows(rows[:5], 20, 7)), 5)

    def test_train_video_model_saves_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.json"
            meta = prepare_stimuli.train_video_model(self._rows(["humor", "information"]), target)
            self.assertIsNotNone(meta)
            self.assertTrue(target.is_file())
            self.assertGreater(meta["size_kb"], 0)
            self.assertEqual(meta["train_rows"], 24)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(sorted(payload["labels"]), ["humor", "information"])

    def test_train_video_model_needs_rows_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.json"
            self.assertIsNone(prepare_stimuli.train_video_model(self._rows(["humor"])[:5], target))
            self.assertIsNone(prepare_stimuli.train_video_model(self._rows(["humor"])[:24], target))
            self.assertFalse(target.exists())


class AssessTest(unittest.TestCase):
    def _good_entry(self) -> dict:
        return {
            "bvid": "BV1GJ411x7h7",
            "genre": "entertainment",
            "density": "high",
            "duration_s": 200,
            "density": {"duration_s": 200, "items": 500, "peak_per_min": 200.0},
            "density_feasibility": {"can_high": True, "can_low": True, "suggested": "high"},
            "teacher": {"rows": 120, "coverage": 0.5, "backend": "llm"},
            "social_counts": {"attack": 6, "conflict": 6, "spoiler": 6, "inflammatory": 2},
            "model": {"path": "x.json", "size_kb": 10.0},
        }

    def test_good_entry_is_ok(self) -> None:
        entry = prepare_stimuli.assess_stimulus(self._good_entry())
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["reasons"], [])
        self.assertEqual(entry["warnings"], [])

    def test_bad_entry_lists_reasons(self) -> None:
        entry = self._good_entry()
        entry["duration_s"] = 600
        entry["density"] = {"duration_s": 600, "items": 100, "peak_per_min": 10.0}
        entry["density_feasibility"] = {"can_high": False, "can_low": False, "suggested": None}
        entry["teacher"] = {"rows": 10, "coverage": 0.1, "backend": "rules-online"}
        entry["social_counts"] = {"attack": 1, "conflict": 0, "spoiler": 0}
        entry["model"] = None
        entry["density_declared"] = "high"
        entry["density_declared"] = "high"
        annotated = prepare_stimuli.assess_stimulus(entry)
        reasons = " ".join(annotated["reasons"])
        self.assertEqual(annotated["status"], "insufficient")
        self.assertIn("duration 600s", reasons)
        self.assertIn("only 100 danmaku", reasons)
        self.assertIn("only 10 teacher-labelled", reasons)
        warnings = " ".join(annotated["warnings"])
        # Per-video social coverage is only a warning; the C contrast is judged
        # on the 3-video entertainment set in summarise().
        self.assertIn("attack=1, conflict=0, spoiler=0", warnings)
        self.assertIn("high-density", warnings)
        self.assertIn("no per-video model", warnings)
        self.assertIn("rules-online", warnings)

    def test_high_density_unreachable_is_reported(self) -> None:
        entry = self._good_entry()
        entry["density"] = {"duration_s": 200, "items": 400, "peak_per_min": 10.0}
        entry["density_declared"] = "high"
        annotated = prepare_stimuli.assess_stimulus(entry)
        self.assertIn("peak density 10.0/min", " ".join(annotated["reasons"]))

    def test_cache_density_without_timestamps_warns(self) -> None:
        entry = self._good_entry()
        entry["duration_s"] = 213
        entry["density_source"] = "cache"
        entry["density"] = {
            "items": 1407, "span_s": 0.0, "peak_per_min": 0.0, "comments_per_min": 396.3,
        }
        entry["density_feasibility"] = {"can_high": False, "can_low": False, "suggested": None}
        entry["teacher"] = {"rows": 1407, "coverage": 1.0, "backend": "llm"}
        entry["social_counts"] = {"attack": 5, "conflict": 5, "spoiler": 5}
        entry["model"] = {"path": "x", "size_kb": 1.0}
        annotated = prepare_stimuli.assess_stimulus(entry)
        self.assertTrue(any("no playback timestamps" in note for note in annotated["warnings"]))
        self.assertEqual(annotated["status"], "insufficient")

    def test_declared_low_cell_is_not_a_warning(self) -> None:
        # A "low" cell is produced by deterministic thinning on a dense stream,
        # so a dense measurement is expected and must not warn.
        entry = self._good_entry()
        entry["density_declared"] = "low"
        annotated = prepare_stimuli.assess_stimulus(entry)
        self.assertFalse(any("declared density" in warning for warning in annotated["warnings"]))

    def test_unreachable_high_cell_warns(self) -> None:
        entry = self._good_entry()
        entry["density_declared"] = "high"
        entry["density_feasibility"] = {"can_high": False, "can_low": True, "suggested": "low"}
        annotated = prepare_stimuli.assess_stimulus(entry)
        self.assertTrue(any("declared density 'high'" in warning for warning in annotated["warnings"]))


class SummariseTest(unittest.TestCase):
    def _entry(self, bvid: str, genre: str, ok: bool = True) -> dict:
        return {
            "bvid": bvid,
            "genre": genre,
            "status": "ok" if ok else "insufficient",
            "reasons": [] if ok else ["broken"],
            "density_feasibility": {"can_high": True, "can_low": True, "suggested": "high"},
        }

    def test_ready_set_needs_three_per_genre_and_even_pairs(self) -> None:
        entries = [
            self._entry("BV1", "information"), self._entry("BV2", "entertainment"),
            self._entry("BV3", "information"), self._entry("BV4", "entertainment"),
            self._entry("BV5", "information"), self._entry("BV6", "entertainment"),
        ]
        summary = prepare_stimuli.summarise(entries, placeholder=False)
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["genres"], {"information": 3, "entertainment": 3})
        self.assertEqual(len(summary["pairs"]), 3)

    def _entry_with_social(self, bvid: str, genre: str, counts: dict) -> dict:
        entry = self._entry(bvid, genre)
        entry["social_counts"] = counts
        return entry

    def test_material_social_coverage_is_checked_across_entertainment_set(self) -> None:
        entries = [
            self._entry_with_social("BV1", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV2", "entertainment", {"attack": 6, "conflict": 1, "spoiler": 0}),
            self._entry_with_social("BV3", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV4", "entertainment", {"attack": 1, "conflict": 7, "spoiler": 0}),
            self._entry_with_social("BV5", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV6", "entertainment", {"attack": 0, "conflict": 0, "spoiler": 2}),
        ]
        summary = prepare_stimuli.summarise(entries, placeholder=False)
        # attack and conflict pass, spoilers are scarce: readiness holds but the
        # material notes that spoiler collapse will barely be exercised.
        self.assertTrue(summary["ready"])
        self.assertTrue(any("only 2 spoiler comments" in warning for warning in summary["warnings"]))
        self.assertEqual(summary["social"], {"attack": 7, "conflict": 8, "spoiler": 2})

    def test_material_social_coverage_fails_when_attack_or_conflict_is_low(self) -> None:
        entries = [
            self._entry_with_social("BV1", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV2", "entertainment", {"attack": 2, "conflict": 1, "spoiler": 0}),
            self._entry_with_social("BV3", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV4", "entertainment", {"attack": 1, "conflict": 3, "spoiler": 0}),
            self._entry_with_social("BV5", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV6", "entertainment", {"attack": 0, "conflict": 1, "spoiler": 9}),
        ]
        summary = prepare_stimuli.summarise(entries, placeholder=False)
        self.assertFalse(summary["ready"])
        self.assertTrue(any("material: entertainment set lacks hide-class" in reason for reason in summary["reasons"]))

    def test_material_social_coverage_passes_when_totals_are_enough(self) -> None:
        entries = [
            self._entry_with_social("BV1", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV2", "entertainment", {"attack": 6, "conflict": 2, "spoiler": 1}),
            self._entry_with_social("BV3", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV4", "entertainment", {"attack": 0, "conflict": 6, "spoiler": 2}),
            self._entry_with_social("BV5", "information", {"attack": 0, "conflict": 0, "spoiler": 0}),
            self._entry_with_social("BV6", "entertainment", {"attack": 0, "conflict": 0, "spoiler": 3}),
        ]
        summary = prepare_stimuli.summarise(entries, placeholder=False)
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["social"]["spoiler"], 6)

    def test_wrong_pairing_and_insufficient_block_readiness(self) -> None:
        entries = [
            self._entry("BV1", "information"), self._entry("BV2", "information"),
            self._entry("BV3", "information"), self._entry("BV4", "entertainment"),
            self._entry("BV5", "entertainment"), self._entry("BV6", "entertainment", ok=False),
        ]
        summary = prepare_stimuli.summarise(entries, placeholder=True)
        self.assertFalse(summary["ready"])
        self.assertTrue(any("pair P1" in reason for reason in summary["reasons"]))
        self.assertEqual(summary["ok"], 5)
        self.assertEqual(summary["insufficient"][0]["bvid"], "BV6")


class MainOfflineTest(unittest.TestCase):
    def test_main_runs_offline_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stimuli = root / "stimuli.json"
            stimuli.write_text(
                json.dumps({"schema": 1, "placeholder": False, "stimuli": [
                    {"bvid": "BV1GJ411x7h7", "genre": "entertainment", "density": "high"},
                    {"bvid": "BV1rCxe6wEN9", "genre": "information", "density": "high"},
                ]}),
                encoding="utf-8",
            )
            dataset = root / "dataset.jsonl"
            lines = []
            for bvid in ("BV1GJ411x7h7", "BV1rCxe6wEN9"):
                for index in range(60):
                    lines.append(json.dumps({
                        "bvid": bvid,
                        "text": "评论 %d 号 内容" % index,
                        "time_ms": index * 1000,
                        "dominant": "humor" if index % 2 else "information",
                        "label_source": "llm",
                    }))
            dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
            report = root / "report.json"
            code = prepare_stimuli.main([
                "--stimuli", str(stimuli),
                "--report", str(report),
                "--tags-dir", str(root / "tags"),
                "--models-dir", str(root / "models"),
                "--dataset", str(dataset),
                "--skip-metadata",
                "--skip-latency",
                "--no-refresh-tags",
                "--quiet",
            ])
            self.assertEqual(code, 0)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(len(payload["stimuli"]), 2)
            first = payload["stimuli"][0]
            self.assertEqual(first["teacher"]["source"], "dataset")
            self.assertEqual(first["items"], 0)
            self.assertEqual(first["status"], "insufficient")
            self.assertTrue(any("duration unknown" in reason for reason in first["reasons"]))
            self.assertTrue((root / "models" / "BV1GJ411x7h7.json").is_file())
            self.assertIn("summary", payload)
            self.assertFalse(payload["summary"]["ready"])


if __name__ == "__main__":
    unittest.main()
