"""Linear tagger: hashed character n-gram TF-IDF plus a tiny linear model.

This module is the L1 stage of the cascade described in
docs/design/ablation-v1.md: the LLM (L2) labels a few hundred comments, this
tagger is trained on them in milliseconds, and the rest of the video is scored
in microseconds. It is pure standard library (json, math, os, tempfile, zlib)
and therefore also exports to a JSON model a browser userscript can evaluate.

The feature pipeline is frozen and a port must reproduce it exactly:

* normalise: ' ' + text.strip().lower() + ' '
* features: every substring of length 1..3, spaces included, counted
* bucket: zlib.crc32(gram.encode('utf-8')) % dim, with dim = 1 << 18
* weight: tf = 1 + ln(count), idf = ln((1 + N) / (1 + df)) + 1, then
  w = tf * idf and the whole vector is L2-normalised

Two decision rules are available. 'centroid' treats the L2-normalised sum of
each class' training vectors as a prototype and scores by cosine; it is the
cheap, robust option for tiny label sets. 'softmax' is multinomial logistic
regression trained with Adam; it is the better option once a video has a few
hundred labels. Both return per-label probabilities.

Exported JSON is pruned (weights with |w| <= 0.005 are dropped) so a whole
video model stays well under three megabytes while keeping the accuracy
measured in the ablation (teacher 0.58, human gold 0.69 for the 1,407-comment
music video).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import zlib
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA_VERSION = 1
DEFAULT_DIM = 1 << 18
DEFAULT_NGRAM = (1, 3)
PRUNE_THRESHOLD = 0.005

CENTROID_TEMPERATURE = 10.0
SOFTMAX_EPOCHS = 120
SOFTMAX_LEARNING_RATE = 1.0
SOFTMAX_L2 = 1e-5
ADAM_BETA1 = 0.9
ADAM_BETA2 = 0.999
ADAM_EPSILON = 1e-8

SparseVector = dict[int, float]


def _softmax(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax over a non-empty sequence."""
    peak = max(logits)
    exps = [math.exp(value - peak) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


def _dot_left_sparse(vector: SparseVector, row: SparseVector) -> float:
    """Dot product iterating the (usually much shorter) document vector."""
    return sum(value * row.get(bucket, 0.0) for bucket, value in vector.items())


class HashingTfidf:
    """Hashed char n-gram TF-IDF vectoriser.

    Only buckets with df > 0 are stored in the idf table; unseen buckets use
    the df = 0 fallback ln(1 + N) + 1, so no vocabulary has to be
    materialised and the JSON model stays small.
    """

    def __init__(self, dim: int = DEFAULT_DIM, ngram: tuple[int, int] = DEFAULT_NGRAM) -> None:
        if len(tuple(ngram)) != 2:
            raise ValueError("ngram must be a (min, max) pair")
        low, high = (int(value) for value in ngram)
        if int(dim) <= 0:
            raise ValueError("dim must be positive")
        if low < 1 or high < low:
            raise ValueError("ngram must satisfy 1 <= min <= max")
        self.dim = int(dim)
        self.ngram = (low, high)
        self.n_docs = 0
        self.idf: dict[int, float] = {}

    def _grams(self, text: str) -> Iterable[str]:
        padded = " " + text.strip().lower() + " "
        low, high = self.ngram
        for size in range(low, high + 1):
            for start in range(len(padded) - size + 1):
                yield padded[start:start + size]

    def _bucket_counts(self, text: str) -> dict[int, int]:
        counts: dict[int, int] = {}
        dim = self.dim
        for gram in self._grams(text):
            bucket = zlib.crc32(gram.encode("utf-8")) % dim
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def fit(self, texts: Iterable[str]) -> "HashingTfidf":
        """Count document frequencies and cache the sparse idf table."""
        document_frequency: dict[int, int] = {}
        n_docs = 0
        for text in texts:
            for bucket in self._bucket_counts(text):
                document_frequency[bucket] = document_frequency.get(bucket, 0) + 1
            n_docs += 1
        self.n_docs = n_docs
        self.idf = {
            bucket: math.log((1.0 + n_docs) / (1.0 + df)) + 1.0
            for bucket, df in document_frequency.items()
        }
        return self

    def transform(self, texts: Iterable[str]) -> list[SparseVector]:
        """Return one L2-normalised sparse vector per text."""
        fallback_idf = math.log(1.0 + self.n_docs) + 1.0
        rows: list[SparseVector] = []
        for text in texts:
            weights: SparseVector = {}
            for bucket, count in self._bucket_counts(text).items():
                weights[bucket] = (1.0 + math.log(count)) * self.idf.get(bucket, fallback_idf)
            norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
            rows.append({bucket: value / norm for bucket, value in weights.items()})
        return rows

    def to_dict(self) -> dict:
        """Serialise the vectoriser; idf holds only training-seen buckets."""
        return {
            "dim": self.dim,
            "ngram": list(self.ngram),
            "n_docs": self.n_docs,
            "idf": {str(bucket): value for bucket, value in self.idf.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HashingTfidf":
        """Rebuild a vectoriser from to_dict output."""
        try:
            dim = int(data["dim"])
            ngram = tuple(int(value) for value in data["ngram"])
            n_docs = int(data.get("n_docs", 0))
            raw_idf = data.get("idf") or {}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid hashing tf-idf payload") from exc
        vectoriser = cls(dim=dim, ngram=ngram)
        vectoriser.n_docs = n_docs
        vectoriser.idf = {int(bucket): float(value) for bucket, value in raw_idf.items()}
        return vectoriser


class LinearTagger:
    """Per-video linear tagger over hashed TF-IDF vectors."""

    def __init__(self, kind: str = "centroid") -> None:
        if kind not in ("centroid", "softmax"):
            raise ValueError(f"unknown kind {kind!r}; choose from 'centroid' or 'softmax'")
        self.kind = kind
        self.labels: list[str] = []
        self.vectorizer: HashingTfidf | None = None
        self.weights: dict[str, SparseVector] = {}

    def train(self, texts: Iterable[str], labels: Iterable[str]) -> "LinearTagger":
        """Fit the vectoriser and the linear head; returns self for chaining."""
        text_list = [str(text) for text in texts]
        label_list = [str(label) for label in labels]
        if len(text_list) != len(label_list):
            raise ValueError(f"got {len(text_list)} texts but {len(label_list)} labels")
        if not text_list:
            raise ValueError("cannot train on an empty corpus")
        self.labels = sorted(set(label_list))
        self.vectorizer = HashingTfidf().fit(text_list)
        vectors = self.vectorizer.transform(text_list)
        if self.kind == "centroid":
            self.weights = self._fit_centroid(vectors, label_list)
        else:
            self.weights = self._fit_softmax(vectors, label_list)
        return self

    def _fit_centroid(
        self, vectors: Sequence[SparseVector], labels: Sequence[str]
    ) -> dict[str, SparseVector]:
        """L2-normalised class sums: each label becomes one prototype vector."""
        sums: dict[str, SparseVector] = {label: {} for label in self.labels}
        for vector, label in zip(vectors, labels):
            total = sums[label]
            for bucket, value in vector.items():
                total[bucket] = total.get(bucket, 0.0) + value
        centroids: dict[str, SparseVector] = {}
        for label, total in sums.items():
            norm = math.sqrt(sum(value * value for value in total.values())) or 1.0
            centroids[label] = {bucket: value / norm for bucket, value in total.items()}
        return centroids

    def _fit_softmax(
        self, vectors: Sequence[SparseVector], labels: Sequence[str]
    ) -> dict[str, SparseVector]:
        """Multinomial logistic regression trained with full-batch Adam.

        Gradients are averaged over the corpus (the / n in the update), there
        is no bias term and no class weighting: those are the settings that
        scored teacher 0.58 / human gold 0.69 in the ablation.
        """
        label_index = {label: position for position, label in enumerate(self.labels)}
        targets = [label_index[label] for label in labels]
        n_labels = len(self.labels)
        n_docs = len(vectors)
        documents = [list(vector.items()) for vector in vectors]
        rows: dict[int, list[float]] = {}
        first_moment: dict[int, list[float]] = {}
        second_moment: dict[int, list[float]] = {}
        for epoch in range(1, SOFTMAX_EPOCHS + 1):
            gradient: dict[int, list[float]] = {}
            for document, target in zip(documents, targets):
                logits = [0.0] * n_labels
                for bucket, value in document:
                    row = rows.get(bucket)
                    if row is None:
                        continue
                    for position in range(n_labels):
                        logits[position] += value * row[position]
                probabilities = _softmax(logits)
                probabilities[target] -= 1.0
                for bucket, value in document:
                    accumulated = gradient.get(bucket)
                    if accumulated is None:
                        gradient[bucket] = [value * probability for probability in probabilities]
                    else:
                        for position in range(n_labels):
                            accumulated[position] += value * probabilities[position]
            scale = 1.0 / n_docs
            beta1_correction = 1.0 - ADAM_BETA1 ** epoch
            beta2_correction = 1.0 - ADAM_BETA2 ** epoch
            for bucket, accumulated in gradient.items():
                row = rows.get(bucket)
                if row is None:
                    row = rows[bucket] = [0.0] * n_labels
                    first_moment[bucket] = [0.0] * n_labels
                    second_moment[bucket] = [0.0] * n_labels
                moment = first_moment[bucket]
                velocity = second_moment[bucket]
                for position in range(n_labels):
                    grad = accumulated[position] * scale + SOFTMAX_L2 * row[position]
                    moment[position] = ADAM_BETA1 * moment[position] + (1.0 - ADAM_BETA1) * grad
                    velocity[position] = (
                        ADAM_BETA2 * velocity[position] + (1.0 - ADAM_BETA2) * grad * grad
                    )
                    row[position] -= SOFTMAX_LEARNING_RATE * (moment[position] / beta1_correction) / (
                        math.sqrt(velocity[position] / beta2_correction) + ADAM_EPSILON
                    )
        return {
            label: {
                bucket: row[position]
                for bucket, row in rows.items()
                if row[position] != 0.0
            }
            for position, label in enumerate(self.labels)
        }

    def predict_scores(self, texts: Iterable[str]) -> list[dict[str, float]]:
        """Return {label: probability} for each text (each row sums to 1)."""
        self._require_trained()
        vectors = self._require_vectorizer().transform([str(text) for text in texts])
        results: list[dict[str, float]] = []
        for vector in vectors:
            raw = [
                _dot_left_sparse(vector, self.weights.get(label, {})) for label in self.labels
            ]
            if self.kind == "centroid":
                raw = [value * CENTROID_TEMPERATURE for value in raw]
            results.append(dict(zip(self.labels, _softmax(raw))))
        return results

    def predict(self, texts: Iterable[str]) -> list[str]:
        """Return the dominant label for each text."""
        scores = self.predict_scores(texts)
        return [max(row, key=row.get) for row in scores]

    def to_dict(self) -> dict:
        """Serialise a pruned model.

        Weights with |w| <= 0.005 are dropped. The idf table keeps every
        bucket seen during training (a superset of the buckets referenced by
        the pruned weights), so unseen text still falls back to the df = 0
        idf and nothing has to be re-fitted after loading.
        """
        self._require_trained()
        pruned = {
            label: {
                bucket: value
                for bucket, value in self.weights.get(label, {}).items()
                if abs(value) > PRUNE_THRESHOLD
            }
            for label in self.labels
        }
        return {
            "schema": SCHEMA_VERSION,
            "kind": self.kind,
            "labels": list(self.labels),
            "vectorizer": self._require_vectorizer().to_dict(),
            "weights": {
                label: {str(bucket): value for bucket, value in pruned[label].items()}
                for label in self.labels
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LinearTagger":
        """Rebuild a tagger from to_dict output."""
        try:
            kind = str(data["kind"])
            labels = [str(label) for label in data["labels"]]
            vectorizer = HashingTfidf.from_dict(data["vectorizer"])
            raw_weights = data.get("weights") or {}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid linear tagger payload") from exc
        if not labels:
            raise ValueError("invalid linear tagger payload: no labels")
        try:
            tagger = cls(kind=kind)
        except ValueError as exc:
            raise ValueError(f"invalid linear tagger payload: {exc}") from exc
        tagger.labels = labels
        tagger.vectorizer = vectorizer
        tagger.weights = {
            label: {
                int(bucket): float(value)
                for bucket, value in (raw_weights.get(label) or {}).items()
            }
            for label in labels
        }
        return tagger

    def save(self, path: str | Path) -> Path:
        """Atomically write the pruned JSON model and return its path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return target

    @classmethod
    def load(cls, path: str | Path) -> "LinearTagger":
        """Read a JSON model written by save."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def _require_vectorizer(self) -> HashingTfidf:
        if self.vectorizer is None:
            raise RuntimeError("tagger is not trained")
        return self.vectorizer

    def _require_trained(self) -> None:
        if self.vectorizer is None or not self.labels:
            raise RuntimeError("tagger is not trained")
