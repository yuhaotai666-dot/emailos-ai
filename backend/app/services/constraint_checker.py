"""Deterministic hard-constraint checker.

Intentionally rule-based (regex/keyword), NOT an LLM call, so safety checks are
reliable, fast, and unit-testable. Returns a :class:`ConstraintCheck` with the
list of failed constraints and concrete rewrite instructions.

Enforced rules (from the product spec):
* Never promise payment unless the thread explicitly confirms it.
* Never promise exact publishing results / use "guarantee" without support.
* Never fabricate a prior conversation ("as discussed"/"as agreed").
* Rate rejections must use pilot-budget framing, never harsh affordability wording.
* If the sender asks about payment, mention the team review / approval process.
* If it's a product-access issue, ask for a screenshot or point to the web dashboard.
* Keep replies <= ~180 words unless context clearly requires more.
* Keep tone professional/respectful (no harsh wording).

("Never send automatically" is enforced architecturally — there is no send path —
so it is asserted here as always-true rather than a text check.)
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import ConstraintCheck, Category, Email, TriageResult
from ._theo import FORBIDDEN_PHRASES

WORD_LIMIT = 180

_HARSH = [
    "cannot afford",
    "can't afford",
    "too expensive",
    "way too much",
    "no way we can",
    "that's ridiculous",
]
_GUARANTEE = ["guarantee", "guaranteed"]
_RESULT_PROMISE = [
    "we will definitely",
    "will definitely get",
    "guarantee results",
    "guaranteed results",
    "i promise",
    "we promise",
]
_PAYMENT_PROMISE = [
    "we will pay you",
    "you will be paid",
    "you'll be paid",
    "payment will be sent on",
    "i will send the payment",
    "we'll send the payment",
    "we will transfer",
]
_FABRICATED_PRIOR = [
    "as discussed",
    "as we discussed",
    "as agreed",
    "as we agreed",
    "per our previous conversation",
    "per our last conversation",
    "as promised",
    "last time we spoke",
]
_REVIEW_TERMS = ["review", "approval", "approve", "internal", "team is", "the team"]
_ACCESS_TERMS = ["screenshot", "dashboard"]


def _contains_any(text: str, needles: list[str]) -> Optional[str]:
    low = text.lower()
    for n in needles:
        if n in low:
            return n
    return None


def _is_payment_question(email: Optional[Email], triage: Optional[TriageResult]) -> bool:
    """True only for payment-status/timing questions, not rate negotiations."""
    if triage and triage.category == Category.PAYMENT:
        return True
    blob = ""
    if email:
        blob = f"{email.subject} {email.body_preview}".lower()
    return any(w in blob for w in ["payment", "invoice", "get paid", "paid yet", "payout"])


def check(
    draft_body: str,
    subject_suggestion: str = "",
    email: Optional[Email] = None,
    triage: Optional[TriageResult] = None,
) -> ConstraintCheck:
    text = f"{subject_suggestion}\n{draft_body}"
    low = text.lower()
    email_blob = ""
    if email:
        email_blob = f"{email.subject} {email.body_preview}".lower()

    failed: list[str] = []
    instructions: list[str] = []

    # 1. Explicit forbidden phrases (mirror of the tone blocklist).
    hit = _contains_any(low, [p for p in FORBIDDEN_PHRASES])
    if hit:
        failed.append(f"Contains forbidden phrase: '{hit}'.")
        instructions.append(f"Remove the phrase '{hit}'.")

    # 2. Harsh / affordability wording.
    hit = _contains_any(low, _HARSH)
    if hit:
        failed.append(f"Tone too harsh / blunt affordability wording: '{hit}'.")
        instructions.append(
            "Reframe using pilot-stage budget language, e.g. 'at this stage this is "
            "above what we can commit to for the pilot', and keep future collaboration open."
        )

    # 3. Guarantee without support from the original email.
    hit = _contains_any(low, _GUARANTEE)
    if hit and not _contains_any(email_blob, _GUARANTEE):
        failed.append("Uses 'guarantee' without support from the thread.")
        instructions.append("Do not use 'guarantee'; describe expected effort, not guaranteed outcomes.")

    # 4. Exact result / delivery promises.
    hit = _contains_any(low, _RESULT_PROMISE)
    if hit:
        failed.append(f"Makes an unsupported promise: '{hit}'.")
        instructions.append("Avoid promising exact results; use measured language.")

    # 5. Payment promise unless the sender's mail confirms it.
    hit = _contains_any(low, _PAYMENT_PROMISE)
    if hit and "confirmed" not in email_blob:
        failed.append(f"Promises payment without confirmation: '{hit}'.")
        instructions.append(
            "Do not commit a payment date/amount; reference the internal review/approval process."
        )

    # 6. Fabricated prior conversation.
    hit = _contains_any(low, _FABRICATED_PRIOR)
    if hit and not _contains_any(email_blob, _FABRICATED_PRIOR):
        failed.append(f"Implies a prior conversation that isn't in the thread: '{hit}'.")
        instructions.append("Remove references to previous discussions unless the thread supports them.")

    # 7. Payment-status questions must mention the review/approval process.
    #    Gated on the Payment category so rate/fee negotiations (Creator
    #    Partnership) are not forced into review/approval framing.
    if _is_payment_question(email, triage) and not _contains_any(low, _REVIEW_TERMS):
        failed.append("Payment-related reply does not mention the team review/approval process.")
        instructions.append("Mention that the team is reviewing / the internal approval process.")

    # 8. Product-access issues must ask for a screenshot or point to the dashboard.
    if triage and triage.category == Category.PRODUCT_ACCESS:
        if not _contains_any(low, _ACCESS_TERMS):
            failed.append("Product-access reply does not request a screenshot or mention the dashboard.")
            instructions.append("Ask for a screenshot or suggest checking the web dashboard.")

    # 9. Word count.
    words = len(re.findall(r"\b\w+\b", draft_body))
    if words > WORD_LIMIT:
        failed.append(f"Reply is {words} words (> {WORD_LIMIT}).")
        instructions.append(f"Tighten the reply to under {WORD_LIMIT} words.")

    return ConstraintCheck(
        passed=len(failed) == 0,
        failed_constraints=failed,
        rewrite_instructions=" ".join(instructions),
    )
