"""Classifier interface shared by the rules and LLM backends."""
from __future__ import annotations

from typing import Sequence

from ..types import ClassificationResult, Danmaku


class Classifier:
    name = "base"

    def classify(self, dm: Danmaku) -> ClassificationResult:  # pragma: no cover - interface
        raise NotImplementedError

    def classify_batch(self, batch: Sequence[Danmaku]) -> list[ClassificationResult]:
        return [self.classify(dm) for dm in batch]
