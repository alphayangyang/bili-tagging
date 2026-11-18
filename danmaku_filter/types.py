"""Core data types shared across the danmaku filtering pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .taxonomy import CATEGORY_IDS, FALLBACK_ID


@dataclass
class Danmaku:
    """One danmaku (bullet comment)."""

    text: str
    time_ms: int = 0      # playback position (ms) at which the comment appears
    mode: int = 1         # 1 = scroll, 4 = bottom, 5 = top
    dmid: str = ""        # platform comment id
    uid: str = ""         # hashed author id, if available
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClassificationResult:
    """A single classifier decision plus a short explanation for the UI."""

    label: str
    confidence: float = 1.0
    rationale: str = ""
    source: str = "rules"  # rules | llm | user
    tags: dict[str, float] = field(default_factory=dict)  # multi-label view: {tag: score}

    def __post_init__(self) -> None:
        # Single-label backends (rules / llm) fill the multi-label view implicitly.
        if not self.tags:
            self.tags = {self.label: self.confidence}

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_valid(self) -> bool:
        return self.label in CATEGORY_IDS or self.label == FALLBACK_ID
