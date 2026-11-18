"""Tests for scripts/study/power_sim.py (simulation helper, stdlib only)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

STUDY_DIR = Path(__file__).resolve().parents[1] / "scripts" / "study"
if str(STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(STUDY_DIR))

import power_sim  # noqa: E402


class PowerSimTest(unittest.TestCase):
    def test_power_grows_with_effect_and_holm_is_conservative(self) -> None:
        rows = power_sim.simulate(n=24, reps=300, deltas=(0.2, 1.0))
        small, large = rows
        self.assertEqual(small["delta"], 0.2)
        self.assertGreater(large["power"], small["power"])
        self.assertGreaterEqual(large["power"], 0.9)
        for row in rows:
            self.assertLessEqual(row["power_holm"], row["power"])

    def test_minimal_detectable(self) -> None:
        rows = [{"delta": 0.5, "power": 0.4, "power_holm": 0.2}, {"delta": 0.7, "power": 0.85, "power_holm": 0.6}]
        self.assertEqual(power_sim.minimal_detectable(rows), 0.7)
        self.assertIsNone(power_sim.minimal_detectable(rows, target=0.99))
        self.assertIsNone(power_sim.minimal_detectable([]))


if __name__ == "__main__":
    unittest.main()