"""Persist user corrections and replay them as overrides.

A correction is keyed by the normalized comment text. That is deliberately
simple: it works for the first study and keeps the prototype dependency-free.
A later iteration should key by (text, video, timestamp) or use embedding
similarity once enough feedback has accumulated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .types import ClassificationResult, Danmaku


def text_key(text: str) -> str:
    normalized = "".join(text.split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


class FeedbackStore:
    def __init__(self, path: str | Path = "data/feedback.jsonl") -> None:
        self.path = Path(path)
        self._labels: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self._labels = {}
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            self._labels[text_key(row["text"])] = str(row["label"])

    def record(self, dm: Danmaku, label: str, note: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"text": dm.text, "time_ms": dm.time_ms, "label": label, "note": note}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._labels[text_key(dm.text)] = label

    def apply(self, dm: Danmaku, prediction: ClassificationResult) -> ClassificationResult:
        label = self._labels.get(text_key(dm.text))
        if label is None:
            return prediction
        return ClassificationResult(
            label=label, confidence=1.0, rationale="user correction", source="user"
        )
