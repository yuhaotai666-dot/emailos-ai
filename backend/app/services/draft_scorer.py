"""Draft scorer -> Evaluation (1-10 per dimension) + should_rewrite decision.

Real path: an LCEL chain ``prompt | llm.structured(_ScoreOut)``.
Mock path: deterministic heuristics. The ``should_rewrite`` decision is applied
in code (not by the model) so the rewrite loop is reliable and testable.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..models import Category, ConstraintCheck, Draft, Email, Evaluation, TriageResult
from ._chains import SYS_USER_PROMPT
from ._theo import THEO_CONTEXT
from .llm_client import LLMClient, get_llm_client


class _ScoreOut(BaseModel):
    tone_score: int = Field(ge=1, le=10)
    clarity_score: int = Field(ge=1, le=10)
    completeness_score: int = Field(ge=1, le=10)
    context_score: int = Field(ge=1, le=10)
    risk_score: int = Field(ge=1, le=10)
    overall_score: int = Field(ge=1, le=10)
    feedback: str = ""


_SYSTEM = f"""{THEO_CONTEXT}

Score a reply draft from 1-10 on: tone (match to Theo), clarity, completeness
(does it answer the ask), context (uses the right situational framing), and risk
(HIGHER = more risky: unsupported promises, payment/legal exposure, fabricated
history). Also give an overall_score and one line of feedback. Return JSON only."""


class DraftScorer:
    def __init__(self, llm: Optional[LLMClient] = None, settings: Optional[Settings] = None):
        self.llm = llm or get_llm_client()
        self.settings = settings or get_settings()

    def score(
        self,
        email: Email,
        triage: TriageResult,
        draft: Draft,
        constraint: Optional[ConstraintCheck] = None,
    ) -> Evaluation:
        if self.llm.mock:
            ev = _mock_score(email, triage, draft)
        else:
            ev = self._llm_score(email, triage, draft)
        ev.should_rewrite = self._should_rewrite(ev, constraint)
        return ev

    def _llm_score(self, email: Email, triage: TriageResult, draft: Draft) -> Evaluation:
        user = f"Category: {triage.category.value}\n\nDraft:\n{draft.draft_body}"
        chain = SYS_USER_PROMPT | self.llm.structured(_ScoreOut)
        try:
            out: _ScoreOut = chain.invoke({"system": _SYSTEM, "user": user})
        except Exception:  # defensive: fall back rather than crash
            return _mock_score(email, triage, draft)
        return Evaluation(
            tone_score=out.tone_score,
            clarity_score=out.clarity_score,
            completeness_score=out.completeness_score,
            context_score=out.context_score,
            risk_score=out.risk_score,
            overall_score=out.overall_score,
            feedback=out.feedback,
        )

    def _should_rewrite(self, ev: Evaluation, constraint: Optional[ConstraintCheck]) -> bool:
        if constraint and not constraint.passed:
            return True
        return (
            ev.overall_score < self.settings.min_draft_score
            or ev.tone_score < 8
            or ev.clarity_score < 8
            or ev.risk_score > 3
        )


def _clamp(v: int) -> int:
    return max(1, min(10, v))


def _mock_score(email: Email, triage: TriageResult, draft: Draft) -> Evaluation:
    low = draft.draft_body.lower()
    tone, clarity, completeness, context, risk = 9, 9, 9, 8, 2
    notes: list[str] = []

    if any(w in low for w in ("cannot afford", "too expensive", "that's ridiculous")):
        tone, risk = 4, 7
        notes.append("Blunt affordability wording.")
    if any(w in low for w in ("guarantee", "guaranteed", "i promise", "we will definitely")):
        risk = max(risk, 8)
        tone = min(tone, 6)
        notes.append("Unsupported promise/guarantee.")
    if any(w in low for w in ("we will pay", "you will be paid", "payment will be sent on")):
        risk = max(risk, 7)
        notes.append("Payment commitment without confirmation.")

    words = len(re.findall(r"\b\w+\b", draft.draft_body))
    if words > 180:
        clarity = min(clarity, 6)
        notes.append("Too long.")

    if triage.category == Category.PRODUCT_ACCESS and "screenshot" not in low and "dashboard" not in low:
        completeness = min(completeness, 5)
        notes.append("Missing screenshot/dashboard step.")
    if triage.category == Category.PAYMENT and not any(w in low for w in ("review", "approval", "team")):
        completeness = min(completeness, 5)
        context = min(context, 6)
        notes.append("Payment reply omits review/approval framing.")

    overall = _clamp(round((tone + clarity + completeness + context + (10 - risk)) / 5))
    return Evaluation(
        tone_score=_clamp(tone),
        clarity_score=_clamp(clarity),
        completeness_score=_clamp(completeness),
        context_score=_clamp(context),
        risk_score=_clamp(risk),
        overall_score=overall,
        feedback=" ".join(notes) if notes else "Clean, on-voice draft.",
    )
