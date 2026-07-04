"""Draft critic. Answers the spec's 9 review questions and emits rewrite guidance.

Real path: an LCEL chain ``prompt | llm.structured(_CritiqueOut)``.
Mock path: deterministic checks reusing the constraint checker.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..models import Category, Critique, Draft, Email, TriageResult
from . import constraint_checker
from ._chains import SYS_USER_PROMPT
from ._theo import THEO_CONTEXT
from .llm_client import LLMClient, get_llm_client


class _CritiqueOut(BaseModel):
    issues: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    tone_feedback: str = ""
    risk_feedback: str = ""
    rewrite_instructions: str = ""


_SYSTEM = f"""{THEO_CONTEXT}

You review a reply draft before Theo sees it. Check: (1) does it answer the sender's
actual question, (2) does the tone match Theo (concise, polite, direct, no hype),
(3) is it concise, (4) is it too cold/harsh/salesy, (5) any unsupported promises,
(6) any payment/legal/expectation risk, (7) does it preserve the relationship,
(8) is context missing, (9) is there a clear next step. Return structured JSON with
issues, missing_context, tone_feedback, risk_feedback, and concrete rewrite_instructions
(empty string if the draft is good)."""


class DraftCritic:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or get_llm_client()

    def critique(self, email: Email, triage: TriageResult, draft: Draft) -> Critique:
        if self.llm.mock:
            return _mock_critique(email, triage, draft)

        user = (
            f"Sender question (preview): {email.body_preview}\n"
            f"Category: {triage.category.value}\n\n"
            f"Draft:\n{draft.draft_body}"
        )
        chain = SYS_USER_PROMPT | self.llm.structured(_CritiqueOut)
        try:
            out: _CritiqueOut = chain.invoke({"system": _SYSTEM, "user": user})
        except Exception:  # defensive: fall back rather than crash
            return _mock_critique(email, triage, draft)
        return Critique(
            issues=out.issues,
            missing_context=out.missing_context,
            tone_feedback=out.tone_feedback,
            risk_feedback=out.risk_feedback,
            rewrite_instructions=out.rewrite_instructions,
        )


def _mock_critique(email: Email, triage: TriageResult, draft: Draft) -> Critique:
    cc = constraint_checker.check(draft.draft_body, draft.subject_suggestion or "", email, triage)
    issues = list(cc.failed_constraints)
    missing: list[str] = []
    low = draft.draft_body.lower()

    if triage.category == Category.PRODUCT_ACCESS and "screenshot" not in low and "dashboard" not in low:
        missing.append("No screenshot request or dashboard pointer for an access issue.")
    if triage.needs_reply and "?" in email.body_preview and "?" not in draft.draft_body and len(draft.draft_body) < 120:
        missing.append("Draft may not fully address the sender's question.")

    harsh = any(w in low for w in ("cannot afford", "too expensive", "guarantee"))
    tone_feedback = (
        "Tone is too blunt; soften with pilot-stage framing." if harsh
        else "Tone matches Theo's concise, polite, direct style."
    )
    risky = any(w in low for w in ("guarantee", "i promise", "we will definitely", "we will pay"))
    risk_feedback = (
        "Contains promises that create expectation/payment risk." if risky
        else "No obvious payment/legal/expectation risk."
    )
    return Critique(
        issues=issues,
        missing_context=missing,
        tone_feedback=tone_feedback,
        risk_feedback=risk_feedback,
        rewrite_instructions=cc.rewrite_instructions,
    )
