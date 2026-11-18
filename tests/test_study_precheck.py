"""Tests for scripts/study/precheck.py (pure stdlib, no network)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

STUDY_DIR = Path(__file__).resolve().parents[1] / "scripts" / "study"
if str(STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(STUDY_DIR))

import precheck  # noqa: E402

HEALTH_OK = {
    "ok": True,
    "online_backend": "linear",
    "models": 9,
    "tag_files": 12,
    "llm_pending": 0,
}


class _FakeResponse:
    """Minimal context manager standing in for urllib's response object."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _health(payload: dict) -> dict:
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        return precheck.check_health("http://127.0.0.1:8765")


def _repo(tmp: str) -> Path:
    repo = Path(tmp)
    (repo / "data" / "study" / "models").mkdir(parents=True)
    (repo / "extension" / "dist").mkdir(parents=True)
    return repo


def _write_stimuli(repo: Path, placeholder: bool = False) -> None:
    rows = [
        {"bvid": "BV1Ho4y1D7Wz", "genre": "information", "density": "high"},
        {"bvid": "BV18E1NByEYv", "genre": "entertainment", "density": "low"},
        {"bvid": "BV1VMRKBCEDp", "genre": "information", "density": "low"},
        {"bvid": "BV1X7zGY4E45", "genre": "entertainment", "density": "high"},
        {"bvid": "BV1Z4RHYKEcB", "genre": "information", "density": "high"},
        {"bvid": "BV1CzW26vEXz", "genre": "entertainment", "density": "low"},
    ]
    path = repo / "data" / "study" / "stimuli.json"
    path.write_text(json.dumps({"schema": 1, "placeholder": placeholder, "stimuli": rows}), encoding="utf-8")


class HealthCheckTest(unittest.TestCase):
    def test_healthy_linear_service_passes(self) -> None:
        result = _health(HEALTH_OK)
        self.assertTrue(result["ok"], result)

    def test_teacher_queue_is_flagged(self) -> None:
        result = _health(dict(HEALTH_OK, llm_pending=2500))
        self.assertFalse(result["ok"])
        self.assertIn("llm_pending", result["detail"])

    def test_wrong_online_backend_is_flagged(self) -> None:
        result = _health(dict(HEALTH_OK, online_backend="rules"))
        self.assertFalse(result["ok"])
        self.assertIn("online_backend", result["detail"])

    def test_unreachable_service_fails(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = precheck.check_health("http://127.0.0.1:8765")
        self.assertFalse(result["ok"])
        self.assertIn("cannot reach", result["detail"])


class StimuliCheckTest(unittest.TestCase):
    def test_real_six_stimuli_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _write_stimuli(repo)
            self.assertTrue(precheck.check_stimuli(repo)["ok"])

    def test_placeholder_manifest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _write_stimuli(repo, placeholder=True)
            result = precheck.check_stimuli(repo)
            self.assertFalse(result["ok"])
            self.assertIn("placeholder", result["detail"])

    def test_missing_manifest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = precheck.check_stimuli(Path(tmp))
            self.assertFalse(result["ok"])


class ModelsCheckTest(unittest.TestCase):
    def test_missing_model_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _write_stimuli(repo)
            models = repo / "data" / "study" / "models"
            for bvid in ("BV1Ho4y1D7Wz", "BV18E1NByEYv"):
                (models / (bvid + ".json")).write_text("x" * 40_000, encoding="utf-8")
            result = precheck.check_models(repo)
            self.assertFalse(result["ok"])
            self.assertIn("BV1VMRKBCEDp", result["detail"])

    def test_tiny_model_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            _write_stimuli(repo)
            models = repo / "data" / "study" / "models"
            for row in json.loads((repo / "data" / "study" / "stimuli.json").read_text())["stimuli"]:
                (models / (row["bvid"] + ".json")).write_text("x" * 100, encoding="utf-8")
            result = precheck.check_models(repo)
            self.assertFalse(result["ok"])
            self.assertIn("looks empty", result["detail"])


class RunSheetCheckTest(unittest.TestCase):
    def _write_sheet(self, repo: Path, rows: list[dict]) -> None:
        path = repo / "data" / "study" / "run-sheet.csv"
        columns = ["participant", "condition", "condition_order", "pair", "block", "bvid", "genre", "density", "study_id", "url"]
        lines = [",".join(columns)]
        for row in rows:
            lines.append(",".join(str(row.get(column, "")) for column in columns))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _rows(self) -> list[dict]:
        rows = []
        for participant in range(1, 31):
            for index, condition in enumerate(("A", "B", "C")):
                for block in (1, 2):
                    rows.append({
                        "participant": "P%02d" % participant,
                        "condition": condition,
                        "condition_order": index + 1,
                        "pair": index + 1,
                        "block": block,
                        "bvid": "BV1Ho4y1D7Wz" if block == 1 else "BV18E1NByEYv",
                        "genre": "information" if block == 1 else "entertainment",
                        "density": "high" if block == 1 else "low",
                        "study_id": "P%02d-%s" % (participant, condition),
                        "url": "https://www.bilibili.com/video/BV1Ho4y1D7Wz/?dmf_study=P%02d-%s&dmf_cond=%s&dmf_density=high" % (participant, condition, condition),
                    })
        return rows

    def test_complete_sheet_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            self._write_sheet(repo, self._rows())
            result = precheck.check_run_sheet(repo)
            self.assertTrue(result["ok"], result)

    def test_missing_url_parameter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            rows = self._rows()
            rows[0]["url"] = "https://www.bilibili.com/video/BV1Ho4y1D7Wz/?dmf_study=P01-A&dmf_cond=A"
            self._write_sheet(repo, rows)
            result = precheck.check_run_sheet(repo)
            self.assertFalse(result["ok"])
            self.assertIn("dmf_density", result["detail"])

    def test_missing_blocks_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            self._write_sheet(repo, self._rows()[:-1])
            result = precheck.check_run_sheet(repo)
            self.assertFalse(result["ok"])


class GoldCheckTest(unittest.TestCase):
    def test_gold_with_unknown_label_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            rows = [{"bvid": "BV1Ho4y1D7Wz", "text": "x", "label": "humor"}] * 120
            rows[0]["label"] = "spam"
            (repo / "data" / "study" / "gold.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            result = precheck.check_gold(repo)
            self.assertFalse(result["ok"])
            self.assertIn("unknown", result["detail"])

    def test_single_annotator_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            rows = [{"bvid": "BV1Ho4y1D7Wz", "text": "x", "label": "humor", "annotator": "lead-single"}] * 120
            (repo / "data" / "study" / "gold.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            result = precheck.check_gold(repo)
            self.assertTrue(result["ok"], result)
            self.assertIn("single annotator", result["detail"])


class VersionCheckTest(unittest.TestCase):
    def test_version_mismatch_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            (repo / "extension" / "package.json").write_text(json.dumps({"version": "0.4.2"}), encoding="utf-8")
            (repo / "extension" / "dist" / "bili-danmaku-filter.user.js").write_text(
                "// ==UserScript==\n// @version      0.4.1\n", encoding="utf-8"
            )
            result = precheck.check_version(repo)
            self.assertFalse(result["ok"])
            self.assertIn("rebuild", result["detail"])


class RunChecksTest(unittest.TestCase):
    def test_offline_skips_the_service_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = precheck.run_checks("http://127.0.0.1:8765", offline=True, repo=Path(tmp))
        self.assertEqual(results[0]["name"], "service")
        self.assertTrue(results[0]["ok"])
        self.assertIn("skipped", results[0]["detail"])
        self.assertEqual([result["name"] for result in results], ["service", "stimuli", "models", "report", "run-sheet", "gold", "script"])


if __name__ == "__main__":
    unittest.main()