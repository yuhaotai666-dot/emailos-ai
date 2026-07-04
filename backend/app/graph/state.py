"""Shared state for the per-email LangGraph.

One graph run processes exactly one email. The graph is linear with a single
rewrite loop, so no channel reducers are needed — each node returns a partial
dict that is merged into the state.
"""
from __future__ import annotations

from typing import Optional, TypedDict

from ..models import ConstraintCheck, Critique, Draft, Email, Evaluation, TriageResult
from ..services.context_retriever import RetrievedContext


class EmailState(TypedDict, total=False):
    # Inputs
    email: Email
    max_retries: int

    # Produced along the way
    triage: TriageResult
    context: RetrievedContext
    draft: Draft            # the current working draft
    critique: Critique
    evaluation: Evaluation
    constraint: ConstraintCheck
    best: Draft             # best draft seen so far (constraint-pass preferred, higher score)
    attempt: int
