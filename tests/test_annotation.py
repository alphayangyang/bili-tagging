"""Tests for danmaku_filter.annotation (pilot sampling and Cohen kappa)."""
import csv
import tempfile
import unittest
from pathlib import Path

from danmaku_filter import annotation


def _items() -> dict[str, dict]:
    items: dict[str, dict] = {}
    for label, count in [("other", 20), ("information", 10), ("humor", 6), ("spoiler", 2)]:
        for index in range(count):
            dmid = f"{label}-{index}"
            items[dmid] = {"text": f"{label} {index}", "time_ms": index * 1000, "dominant": label}
    return items


class StratifiedSampleTests(unittest.TestCase):
    def test_quotas_bring_in_rare_categories(self):
        quotas = {"other": 3, "information": 3, "humor": 2, "spoiler": 2}
        picked = annotation.stratified_sample(_items(), n=10, seed=1, quotas=quotas)
        self.assertEqual(len(picked), 10)
        labels = [item["dominant"] for _, item in picked]
        for label in quotas:
            self.assertIn(label, labels)

    def test_same_seed_is_reproducible(self):
        first = [dmid for dmid, _ in annotation.stratified_sample(_items(), n=12, seed=42)]
        second = [dmid for dmid, _ in annotation.stratified_sample(_items(), n=12, seed=42)]
        self.assertEqual(first, second)

    def test_n_larger_than_pool_returns_everything(self):
        items = _items()
        picked = annotation.stratified_sample(items, n=999, seed=7)
        self.assertEqual(len(picked), len(items))

    def test_bad_arguments_raise(self):
        with self.assertRaises(ValueError):
            annotation.stratified_sample(_items(), n=0)
        with self.assertRaises(ValueError):
            annotation.stratified_sample(_items(), n=5, quotas={"nope": 1})


class KappaTests(unittest.TestCase):
    def test_perfect_agreement(self):
        report = annotation.cohen_kappa([("info", "info"), ("humor", "humor")] * 5)
        self.assertEqual(report["n"], 10)
        self.assertEqual(report["observed"], 1.0)
        self.assertEqual(report["kappa"], 1.0)

    def test_known_textbook_value(self):
        pairs = [("x", "x")] * 4 + [("x", "y")] + [("y", "x")] + [("y", "y")] * 4
        report = annotation.cohen_kappa(pairs)
        self.assertEqual(report["observed"], 0.8)
        self.assertEqual(report["expected"], 0.5)
        self.assertEqual(report["kappa"], 0.6)

    def test_empty_pairs_report_none(self):
        report = annotation.cohen_kappa([])
        self.assertEqual(report["n"], 0)
        self.assertIsNone(report["kappa"])

    def test_confusion_and_disagreements(self):
        labels_a = {"1": "humor", "2": "other", "3": "information"}
        labels_b = {"1": "other", "2": "other", "3": "information"}
        pairs = annotation.coded_pairs(labels_a, labels_b)
        report = annotation.cohen_kappa(pairs)
        self.assertEqual(report["confusion"]["humor"], {"other": 1})
        rows = annotation.disagreements(labels_a, labels_b)
        self.assertEqual(rows, [{"item_id": "1", "coder_a": "humor", "coder_b": "other"}])

    def test_validate_labels_reports_typos(self):
        self.assertEqual(annotation.validate_labels(["humor", "humour", "other"]), ["humour"])


class SheetRoundTripTests(unittest.TestCase):
    def test_sheet_has_blank_labels_then_reads_them_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp) / "coder_a.csv"
            rows = [{"item_id": "1", "text": "a b"}, {"item_id": "2", "text": "c d"}]
            annotation.write_coder_sheet(sheet, rows)
            self.assertEqual(annotation.read_labels(sheet), {})
            with sheet.open(encoding="utf-8", newline="") as handle:
                written = list(csv.DictReader(handle))
            self.assertEqual([row["item_id"] for row in written], ["1", "2"])
            written[0]["label"] = "humor"
            with sheet.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["item_id", "text", "label", "note"])
                writer.writeheader()
                writer.writerows(written)
            self.assertEqual(annotation.read_labels(sheet), {"1": "humor"})


if __name__ == "__main__":
    unittest.main()
