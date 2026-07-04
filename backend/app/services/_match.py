"""Tiny keyword-overlap scorer used for memory retrieval (no embeddings needed)."""
from __future__ import annotations

import re

_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "is", "are",
    "we", "you", "i", "it", "this", "that", "with", "at", "be", "as", "our",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 2}


def score_overlap(a: str, b: str) -> int:
    return len(_tokens(a) & _tokens(b))
