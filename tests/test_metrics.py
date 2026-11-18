import unittest

from danmaku_filter.metrics import prf


GOLD = [
    {"text": "a", "label": "attack"},
    {"text": "b", "label": "humor"},
    {"text": "c", "label": "other"},
]
PRED = [
    {"text": "a", "label": "attack"},
    {"text": "b", "label": "attack"},
    {"text": "c", "label": "other"},
]


class MetricsTest(unittest.TestCase):
    def test_counts(self) -> None:
        report = prf(GOLD, PRED, ["attack", "humor", "other"])
        self.assertEqual(report["n"], 3)
        self.assertAlmostEqual(report["accuracy"], 2 / 3, places=3)
        attack = report["per_label"]["attack"]
        self.assertAlmostEqual(attack["precision"], 0.5, places=3)
        self.assertAlmostEqual(attack["recall"], 1.0, places=3)
        self.assertEqual(attack["support"], 1)

    def test_unmatched_gold_rows_are_reported(self) -> None:
        report = prf(GOLD, PRED[:2], ["attack", "humor", "other"])
        self.assertEqual(report["unmatched"], 1)


if __name__ == "__main__":
    unittest.main()
