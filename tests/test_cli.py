"""CLI contract: classify must batch instead of calling the model per item."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from danmaku_filter import cli
from danmaku_filter.classify.base import Classifier
from danmaku_filter.types import ClassificationResult


class BatchOnlyClassifier(Classifier):
    name = "fake"

    def __init__(self) -> None:
        self.batch_calls = 0
        self.single_calls = 0

    def classify(self, dm):
        self.single_calls += 1
        raise AssertionError("classify() must not be called item by item")

    def classify_batch(self, batch):
        self.batch_calls += 1
        return [
            ClassificationResult(label="other", confidence=0.0, tags={"other": 0.0})
            for _ in batch
        ]


class ClassifyCliTest(unittest.TestCase):
    def test_classify_sends_one_batch_for_the_whole_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.jsonl"
            target = Path(tmp) / "out.jsonl"
            rows = [{"text": text, "time_ms": index * 1000} for index, text in enumerate(["a", "b", "c"])]
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            classifier = BatchOnlyClassifier()
            with mock.patch.object(cli, "get_classifier", return_value=classifier):
                code = cli.main(["classify", "--input", str(source), "--out", str(target)])

            self.assertEqual(code, 0)
            self.assertEqual(classifier.single_calls, 0)
            self.assertEqual(classifier.batch_calls, 1)
            written = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["label"] for row in written], ["other", "other", "other"])


if __name__ == "__main__":
    unittest.main()
