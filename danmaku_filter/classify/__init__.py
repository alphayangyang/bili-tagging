"""Classifier backends: a rule baseline and an LLM backend."""
from __future__ import annotations

from .base import Classifier
from .llm import LLMClassifier
from .rules import RuleClassifier

_BACKENDS = {"rules": RuleClassifier, "llm": LLMClassifier}


def get_classifier(name: str = "rules", **kwargs) -> Classifier:
    try:
        backend = _BACKENDS[name]
    except KeyError as exc:
        raise ValueError(f"unknown backend {name!r}; choose from {sorted(_BACKENDS)}") from exc
    return backend(**kwargs)
