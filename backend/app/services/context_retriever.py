"""Retrieve memory relevant to an email before drafting.

Gathers the most relevant MemoryRules, ErrorCases, and SuccessPatterns via a
simple keyword-overlap match. Returned bundle is injected into the draft prompt
and mock generator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Email, ErrorCase, MemoryRule, SuccessPattern, TriageResult
from ..repositories import LocalStore, get_store
from ._match import score_overlap
from .error_library import ErrorLibrary


@dataclass
class RetrievedContext:
    rules: list[MemoryRule] = field(default_factory=list)
    errors: list[ErrorCase] = field(default_factory=list)
    successes: list[SuccessPattern] = field(default_factory=list)


class ContextRetriever:
    def __init__(self, store: LocalStore | None = None):
        self.store = store or get_store()
        self.errors = ErrorLibrary(self.store)

    def retrieve(self, email: Email, triage: TriageResult, k: int = 3) -> RetrievedContext:
        query = f"{email.subject} {email.body_preview} {triage.category.value} {triage.summary}"

        rules = sorted(
            self.store.memory_rules.list(),
            key=lambda r: score_overlap(query, f"{r.situation} {r.preference}"),
            reverse=True,
        )
        rules = [r for r in rules if score_overlap(query, f"{r.situation} {r.preference}") > 0][:k]

        successes = sorted(
            self.store.success_patterns.list(),
            key=lambda s: score_overlap(query, f"{s.situation} {s.why_it_worked}"),
            reverse=True,
        )
        successes = [s for s in successes if score_overlap(query, s.situation) > 0][:k]

        return RetrievedContext(
            rules=rules,
            errors=self.errors.relevant(query, triage, k),
            successes=successes,
        )
