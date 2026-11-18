"""Tests for scripts/study/freeze.py (materials manifest, stdlib only)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

STUDY_DIR = Path(__file__).resolve().parents[1] / "scripts" / "study"
if str(STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(STUDY_DIR))

import freeze  # noqa: E402


class FreezeTest(unittest.TestCase):
    def _layout(self, root: Path) -> dict:
        data = root / "data" / "study"
        models = data / "models"
        models.mkdir(parents=True)
        (data / "stimuli.json").write_text('{"stimuli": [{"id": 1}, {"id": 2}]}', encoding="utf-8")
        (data / "teacher_sample.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
        (data / "gold.jsonl").write_text('{"label": "humor"}\n', encoding="utf-8")
        (models / "BV1.json").write_text("{}", encoding="utf-8")
        (data / "run-sheet.csv").write_text("participant,url\nP01,x\nP02,y\n", encoding="utf-8")
        userscript = root / "userscript.js"
        userscript.write_text("// build", encoding="utf-8")
        return {"repo": root, "study": data, "manifest": data / "manifest.json", "userscript": userscript}

    def _patch(self, layout: dict):
        return mock.patch.multiple(
            freeze,
            REPO_ROOT=layout["repo"],
            STUDY_DIR=layout["study"],
            MANIFEST=layout["manifest"],
            USCRIPT=layout["userscript"],
        )

    def test_write_and_verify_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(Path(tmp))
            with self._patch(layout):
                payload = freeze.write_manifest()
                self.assertEqual(payload["schema"], "dmf-study-manifest/1")
                self.assertGreaterEqual(len(payload["files"]), 5)
                self.assertEqual(payload["counts"]["stimuli"], 2)
                self.assertEqual(payload["counts"]["teacher_rows"], 2)
                self.assertEqual(payload["counts"]["run_sheet_participants"], 2)
                result = freeze.verify_manifest()
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["missing"], [])
                self.assertEqual(result["changed"], [])

    def test_verify_detects_changed_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(Path(tmp))
            with self._patch(layout):
                freeze.write_manifest()
                (layout["study"] / "gold.jsonl").write_text('{"label": "spoiler"}\n', encoding="utf-8")
                (layout["study"] / "models" / "BV1.json").unlink()
                result = freeze.verify_manifest()
                self.assertFalse(result["ok"])
                self.assertEqual(result["changed"], ["data/study/gold.jsonl"])
                self.assertEqual(result["missing"], ["data/study/models/BV1.json"])

    def test_verify_reports_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(Path(tmp))
            with self._patch(layout):
                result = freeze.verify_manifest()
                self.assertFalse(result["ok"])
                self.assertIn("manifest missing", result["reason"])
                self.assertEqual(freeze.main(["--verify"]), 1)

    def test_main_write_then_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(Path(tmp))
            with self._patch(layout):
                self.assertEqual(freeze.main(["--write"]), 0)
                self.assertEqual(freeze.main(["--verify"]), 0)


if __name__ == "__main__":
    unittest.main()
