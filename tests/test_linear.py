"""Tests for the pure-stdlib linear tagger (hashed TF-IDF + linear head)."""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from danmaku_filter.classify.linear import (
    DEFAULT_DIM,
    PRUNE_THRESHOLD,
    HashingTfidf,
    LinearTagger,
)

TEXTS = [
    "好吃 好吃",
    "好吃 美味",
    "好吃 喜欢",
    "美味 好吃",
    "难看 难看",
    "难看 讨厌",
    "难看 垃圾",
    "难看 差评",
]
LABELS = ["good"] * 4 + ["bad"] * 4


def _assert_rows_almost_equal(test: unittest.TestCase, left: dict, right: dict, delta: float) -> None:
    test.assertEqual(sorted(left), sorted(right))
    for key, value in right.items():
        test.assertAlmostEqual(left[key], value, delta=delta)


class HashingTfidfTest(unittest.TestCase):
    def test_default_shape(self) -> None:
        vectorizer = HashingTfidf()
        self.assertEqual(vectorizer.dim, 1 << 18)
        self.assertEqual(DEFAULT_DIM, 262144)
        self.assertEqual(vectorizer.ngram, (1, 3))
        self.assertEqual(vectorizer.n_docs, 0)

    def test_grams_are_lowercased_and_padded(self) -> None:
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2))
        grams = list(vectorizer._grams("Ab"))
        self.assertEqual(grams, [" ", "a", "b", " ", " a", "ab", "b "])

    def test_buckets_match_frozen_crc32_formula(self) -> None:
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2))
        counts = vectorizer._bucket_counts("ab")
        expected = {3: 1, 5: 2, 18: 1, 19: 1, 45: 1, 57: 1}
        self.assertEqual(counts, expected)

    def test_hand_computed_idf(self) -> None:
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2)).fit(["ab", "ab"])
        self.assertEqual(vectorizer.n_docs, 2)
        self.assertEqual(sorted(vectorizer.idf), [3, 5, 18, 19, 45, 57])
        for bucket, value in vectorizer.idf.items():
            self.assertAlmostEqual(value, math.log(3.0 / 3.0) + 1.0, delta=1e-12)

    def test_hand_computed_transform(self) -> None:
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2)).fit(["ab", "ab"])
        vector = vectorizer.transform(["ab"])[0]
        space = (1.0 + math.log(2.0)) / math.sqrt((1.0 + math.log(2.0)) ** 2 + 5.0)
        other = 1.0 / math.sqrt((1.0 + math.log(2.0)) ** 2 + 5.0)
        expected = {3: other, 5: space, 18: other, 19: other, 45: other, 57: other}
        _assert_rows_almost_equal(self, vector, expected, delta=1e-9)
        norm = math.sqrt(sum(value * value for value in vector.values()))
        self.assertAlmostEqual(norm, 1.0, delta=1e-12)

    def test_empty_text_keeps_only_pad_buckets(self) -> None:
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2)).fit([""])
        vector = vectorizer.transform([""])[0]
        space = (1.0 + math.log(2.0)) / math.sqrt((1.0 + math.log(2.0)) ** 2 + 1.0)
        double_space = 1.0 / math.sqrt((1.0 + math.log(2.0)) ** 2 + 1.0)
        _assert_rows_almost_equal(self, vector, {5: space, 21: double_space}, delta=1e-9)

    def test_unseen_buckets_use_smoothed_idf(self) -> None:
        # "zz" pads to " zz ": bucket 5 (the space) was seen while fitting
        # "ab" and keeps idf 1.0; the other four buckets fall back to df=0.
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2)).fit(["ab", "ab"])
        vector = vectorizer.transform(["zz"])[0]
        fallback = math.log(1.0 + 2.0) + 1.0
        seen = (1.0 + math.log(2.0)) * 1.0
        unseen_frequent = (1.0 + math.log(2.0)) * fallback
        norm = math.sqrt(seen ** 2 + unseen_frequent ** 2 + 3.0 * fallback ** 2)
        expected = {
            5: seen / norm,
            47: unseen_frequent / norm,
            11: fallback / norm,
            33: fallback / norm,
            63: fallback / norm,
        }
        _assert_rows_almost_equal(self, vector, expected, delta=1e-9)

    def test_to_dict_roundtrip_preserves_transform(self) -> None:
        vectorizer = HashingTfidf(dim=64, ngram=(1, 2)).fit(["ab", "ba"])
        restored = HashingTfidf.from_dict(vectorizer.to_dict())
        self.assertEqual(restored.dim, 64)
        self.assertEqual(restored.ngram, (1, 2))
        self.assertEqual(restored.n_docs, 2)
        self.assertEqual(restored.idf, vectorizer.idf)
        self.assertEqual(restored.transform(["ab"]), vectorizer.transform(["ab"]))

    def test_from_dict_rejects_invalid_payloads(self) -> None:
        for payload in ({}, {"dim": 8}, {"dim": "x", "ngram": [1, 2]}):
            with self.assertRaises(ValueError):
                HashingTfidf.from_dict(payload)


class LinearTaggerCentroidTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tagger = LinearTagger(kind="centroid").train(TEXTS, LABELS)

    def test_labels_are_sorted_and_train_labels_recovered(self) -> None:
        self.assertEqual(self.tagger.labels, ["bad", "good"])
        self.assertEqual(self.tagger.predict(TEXTS), LABELS)

    def test_known_scores(self) -> None:
        rows = self.tagger.predict_scores(["好吃", "难看"])
        _assert_rows_almost_equal(self, rows[0], {"bad": 0.000804, "good": 0.999196}, delta=1e-5)
        _assert_rows_almost_equal(self, rows[1], {"bad": 0.999425, "good": 0.000575}, delta=1e-5)

    def test_scores_are_probabilities(self) -> None:
        for row in self.tagger.predict_scores(TEXTS + ["", "   "]):
            self.assertAlmostEqual(sum(row.values()), 1.0, delta=1e-12)
            for value in row.values():
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_predict_returns_the_argmax(self) -> None:
        rows = self.tagger.predict_scores(["好吃 美味"])
        self.assertEqual(self.tagger.predict(["好吃 美味"]), [max(rows[0], key=rows[0].get)])

    def test_predict_is_reproducible(self) -> None:
        self.assertEqual(self.tagger.predict(["好吃"]), self.tagger.predict(["好吃"]))


class LinearTaggerSoftmaxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tagger = LinearTagger(kind="softmax").train(TEXTS, LABELS)

    def test_training_separates_the_tiny_corpus(self) -> None:
        self.assertEqual(self.tagger.labels, ["bad", "good"])
        self.assertEqual(self.tagger.predict(TEXTS), LABELS)

    def test_known_scores(self) -> None:
        rows = self.tagger.predict_scores(["好吃", "难看"])
        _assert_rows_almost_equal(self, rows[0], {"bad": 0.0, "good": 1.0}, delta=1e-6)
        _assert_rows_almost_equal(self, rows[1], {"bad": 1.0, "good": 0.0}, delta=1e-6)

    def test_scores_are_probabilities(self) -> None:
        for row in self.tagger.predict_scores(TEXTS + ["", "   "]):
            self.assertAlmostEqual(sum(row.values()), 1.0, delta=1e-12)


class LinearTaggerPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def _path(self, name: str) -> Path:
        return Path(self.directory.name) / "models" / name

    def test_save_load_roundtrip(self) -> None:
        for kind in ("centroid", "softmax"):
            with self.subTest(kind=kind):
                tagger = LinearTagger(kind=kind).train(TEXTS, LABELS)
                path = tagger.save(self._path(f"{kind}.json"))
                self.assertTrue(path.is_file())
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], 1)
                self.assertEqual(payload["kind"], kind)
                self.assertEqual(payload["labels"], ["bad", "good"])
                loaded = LinearTagger.load(path)
                self.assertEqual(loaded.kind, kind)
                self.assertEqual(loaded.labels, tagger.labels)
                self.assertEqual(loaded.predict(TEXTS), tagger.predict(TEXTS))
                for left, right in zip(loaded.predict_scores(TEXTS), tagger.predict_scores(TEXTS)):
                    _assert_rows_almost_equal(self, left, right, delta=1e-9)

    def test_save_is_atomic_and_leaves_no_temporary_files(self) -> None:
        tagger = LinearTagger().train(TEXTS, LABELS)
        target = self._path("atomic.json")
        tagger.save(target)
        tagger.save(target)
        leftovers = [name for name in os.listdir(target.parent) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["kind"], "centroid")

    def test_pruning_keeps_a_working_model(self) -> None:
        tagger = LinearTagger().train(TEXTS, LABELS)
        with tempfile.TemporaryDirectory() as directory:
            path = tagger.save(Path(directory) / "model.json")
            loaded = LinearTagger.load(path)
        self.assertEqual(loaded.predict(TEXTS), tagger.predict(TEXTS))
        for label, weights in loaded.weights.items():
            self.assertTrue(weights)
            for value in weights.values():
                self.assertGreater(abs(value), PRUNE_THRESHOLD)

    def test_exported_idf_covers_every_kept_weight(self) -> None:
        tagger = LinearTagger("softmax").train(TEXTS, LABELS)
        payload = tagger.to_dict()
        idf = {int(bucket) for bucket in payload["vectorizer"]["idf"]}
        for weights in payload["weights"].values():
            self.assertTrue({int(bucket) for bucket in weights} <= idf)

    def test_load_rejects_invalid_payload(self) -> None:
        path = self._path("broken.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        for payload in ({}, {"kind": "centroid", "labels": []}, {"kind": "nope", "labels": ["a"]}):
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                LinearTagger.load(path)


class LinearTaggerErrorsTest(unittest.TestCase):
    def test_training_requires_matching_non_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            LinearTagger().train([], [])
        with self.assertRaises(ValueError):
            LinearTagger().train(["a"], ["a", "b"])

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LinearTagger(kind="magic")

    def test_untrained_tagger_raises(self) -> None:
        tagger = LinearTagger()
        with self.assertRaises(RuntimeError):
            tagger.predict(["text"])
        with self.assertRaises(RuntimeError):
            tagger.predict_scores(["text"])
        with self.assertRaises(RuntimeError):
            tagger.to_dict()

    def test_train_returns_self(self) -> None:
        tagger = LinearTagger()
        self.assertIs(tagger.train(TEXTS, LABELS), tagger)


if __name__ == "__main__":
    unittest.main()
