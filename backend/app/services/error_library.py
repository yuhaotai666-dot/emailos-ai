"""Error library: retrieval + recording of past bad-output cases.

Thin domain helper over the ``error_cases`` collection. Kept separate (per the
spec's file list) so the "avoid repeating mistakes" concern has one home.
"""
from __future__ import annotations

from ..models import ErrorCase, TriageResult
from ..repositories import LocalStore, get_store
from ._match import score_overlap


class ErrorLibrary:
    def __init__(self, store: LocalStore | None = None):
        self.store = store or get_store()

    def all(self) -> list[ErrorCase]:
        return self.store.error_cases.list()

    def add(self, case: ErrorCase) -> ErrorCase:
        return self.store.error_cases.add(case)

    def relevant(self, query: str, triage: TriageResult | None = None, k: int = 3) -> list[ErrorCase]:
        cases = self.all()
        ctx = query
        if triage:
            ctx = f"{query} {triage.category.value} {triage.summary}"
        ranked = sorted(
            cases,
            key=lambda c: score_overlap(ctx, f"{c.situation} {c.lesson} {c.bad_output}"),
            reverse=True,
        )
        return [c for c in ranked if score_overlap(ctx, f"{c.situation} {c.lesson}") > 0][:k]
