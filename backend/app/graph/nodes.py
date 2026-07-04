"""Node functions for the per-email LangGraph.

Each node is a small pure-ish function over :class:`EmailState`. The heavy
lifting still lives in the services (classifier, retriever, generator, critic,
scorer) and the deterministic constraint checker — the graph just wires them
into a loop with an explicit rewrite decision.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..models import ConstraintCheck, Critique, Draft, DraftStatus, Evaluation
from ..services import constraint_checker
from .state import EmailState

if TYPE_CHECKING:  # avoid a circular import at runtime
    from .build import EmailGraphDeps


class EmailNodes:
    """Graph nodes bound to a set of service dependencies."""

    def __init__(self, deps: "EmailGraphDeps"):
        self.deps = deps

    # -- classify -------------------------------------------------------
    def classify(self, state: EmailState) -> dict:
        triage = self.deps.classifier.classify(state["email"])
        return {"triage": triage}

    def route_after_classify(self, state: EmailState) -> str:
        """Only drafts a reply when the email needs one."""
        return "retrieve" if state["triage"].needs_reply else "__end__"

    # -- retrieve memory ------------------------------------------------
    def retrieve(self, state: EmailState) -> dict:
        context = self.deps.retriever.retrieve(state["email"], state["triage"])
        return {"context": context}

    # -- initial draft --------------------------------------------------
    def generate(self, state: EmailState) -> dict:
        draft = self.deps.generator.generate(state["email"], state["triage"], state["context"])
        return {"draft": draft, "attempt": 0}

    # -- critique + constraints + score --------------------------------
    def evaluate(self, state: EmailState) -> dict:
        email, triage, draft = state["email"], state["triage"], state["draft"]
        critique = self.deps.critic.critique(email, triage, draft)
        constraint = constraint_checker.check(
            draft.draft_body, draft.subject_suggestion or "", email, triage
        )
        evaluation = self.deps.scorer.score(email, triage, draft, constraint)
        _attach(draft, critique, evaluation, constraint)
        best = _better(state.get("best"), draft)
        return {
            "critique": critique,
            "constraint": constraint,
            "evaluation": evaluation,
            "draft": draft,
            "best": best,
        }

    def route_after_evaluate(self, state: EmailState) -> str:
        """Loop back to rewrite until good enough or retries are exhausted."""
        evaluation = state["evaluation"]
        attempt = state.get("attempt", 0)
        if evaluation.should_rewrite and attempt < state.get("max_retries", 0):
            return "rewrite"
        return "finalize"

    # -- rewrite --------------------------------------------------------
    def rewrite(self, state: EmailState) -> dict:
        draft = self.deps.generator.rewrite(
            state["email"],
            state["triage"],
            state["context"],
            state["draft"],
            state.get("critique"),
            state.get("constraint"),
        )
        return {"draft": draft, "attempt": state.get("attempt", 0) + 1}

    # -- finalize -------------------------------------------------------
    def finalize(self, state: EmailState) -> dict:
        best = state["best"]
        best.status = DraftStatus.PENDING_REVIEW
        return {"best": best}


def _attach(draft: Draft, critique: Critique, evaluation: Evaluation, constraint: ConstraintCheck) -> None:
    draft.critique = critique
    draft.evaluation = evaluation
    draft.constraint_detail = constraint
    draft.constraints_passed = constraint.passed


def _better(current: Optional[Draft], candidate: Draft) -> Draft:
    """Prefer a constraint-passing draft with the higher overall score."""
    if current is None:
        return candidate.model_copy(deep=True)
    c_ev = current.evaluation.overall_score if current.evaluation else 0
    n_ev = candidate.evaluation.overall_score if candidate.evaluation else 0
    c_key = (current.constraints_passed, c_ev)
    n_key = (candidate.constraints_passed, n_ev)
    return candidate.model_copy(deep=True) if n_key >= c_key else current
