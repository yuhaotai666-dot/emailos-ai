"""Draft generator: initial draft + critique-driven rewrite.

Real path: an LCEL chain ``prompt | llm.text()`` primed with Theo's voice, the
preferred/forbidden vocabulary, and retrieved memory. Mock path: category-aware
templates that already satisfy the hard constraints, so the offline loop is
deterministic and rewrite-stable.
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import (
    Category,
    ConstraintCheck,
    Critique,
    Draft,
    Email,
    TriageResult,
)
from ._chains import SYS_USER_PROMPT
from ._prompt_layers import as_data, user_rules_block
from ._theo import PREFERRED_PHRASES, THEO_CONTEXT, TONE_GUIDE
from .context_retriever import RetrievedContext
from .llm_client import LLMClient, get_llm_client


def _first_name(sender_name: str) -> str:
    return (sender_name or "there").strip().split(" ")[0]


class DraftGenerator:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or get_llm_client()

    # ---- public API ----
    def generate(self, email: Email, triage: TriageResult, context: RetrievedContext) -> Draft:
        if self.llm.mock:
            subject, body, tone, reasoning = _mock_draft(email, triage)
        else:
            subject, body, tone, reasoning = self._llm_draft(email, triage, context)
        return Draft(
            email_id=email.id,
            original_email_summary=triage.summary,
            subject_suggestion=subject,
            draft_body=body,
            tone=tone,
            reasoning=reasoning,
            version=1,
        )

    def rewrite(
        self,
        email: Email,
        triage: TriageResult,
        context: RetrievedContext,
        previous: Draft,
        critique: Optional[Critique],
        constraint: Optional[ConstraintCheck],
    ) -> Draft:
        if self.llm.mock:
            subject, body, tone, reasoning = _mock_draft(email, triage)
            reasoning = "Rewritten to satisfy critique/constraints. " + reasoning
        else:
            subject, body, tone, reasoning = self._llm_rewrite(
                email, triage, context, previous, critique, constraint
            )
        return Draft(
            email_id=email.id,
            original_email_summary=triage.summary,
            subject_suggestion=subject,
            draft_body=body,
            tone=tone,
            reasoning=reasoning,
            version=previous.version + 1,
        )

    # ---- real LLM path ----
    def _system(self, context: RetrievedContext) -> str:
        # Assembled in instruction-hierarchy order (top overrides bottom):
        # safety → app/product persona → user rules → knowledge → reasoning.
        mem = _format_memory(context)
        thread = _format_thread(context)
        tiers = [
            # Tier 1 — safety (also enforced structurally + by constraint_checker)
            "Hard rules: never fabricate prior conversations or facts. Never use "
            "'we cannot afford this', 'too expensive', 'guarantee results', "
            "'I promise', 'we will definitely'. No fake urgency. Keep replies under "
            "~180 words.",
            # Tier 2 — app/product persona (per-user at Stage 4)
            f"{THEO_CONTEXT}\n\n{TONE_GUIDE}\n"
            f"Preferred phrases you may use naturally: {', '.join(PREFERRED_PHRASES)}.\n"
            "When declining a rate, frame it as a current pilot budget limitation and "
            "keep future collaboration open. For payment questions, reference the team "
            "review / approval process. For product-access issues, ask for a screenshot "
            "or point to the web dashboard.",
        ]
        # Tier 3 — the user's standing rules (highest user-authored precedence)
        rules_block = user_rules_block(context.agent_rules)
        if rules_block:
            tiers.append(rules_block)
        # Tier 4 — knowledge (reference) + thread context
        tiers.append(
            "Prior messages in this thread (oldest first — do not repeat questions "
            f"already answered here, stay consistent with what was said):\n{thread}"
        )
        tiers.append(f"What you know about this user and how they work:\n{mem}")
        tiers.append(
            "Write only the reply body (greeting to sign-off as the user). Do not add "
            "commentary."
        )
        return "\n\n".join(tiers)

    def _llm_draft(self, email, triage, context):
        user = (
            f"Triage: {triage.category.value}, {triage.priority.value}. {triage.summary}\n\n"
            + as_data(
                "EMAIL TO REPLY TO",
                f"From: {email.sender_name} <{email.sender_email}>\n"
                f"Subject: {email.subject}\n\n{email.body_preview}",
            )
            + "\n\nDraft the user's reply."
        )
        chain = SYS_USER_PROMPT | self.llm.text()
        body = _run_text(chain, self._system(context), user) or _mock_draft(email, triage)[1]
        return f"Re: {email.subject}", body.strip(), "professional", "Generated by Claude."

    def _llm_rewrite(self, email, triage, context, previous, critique, constraint):
        fixes = []
        if critique and critique.rewrite_instructions:
            fixes.append(critique.rewrite_instructions)
        if constraint and constraint.rewrite_instructions:
            fixes.append(constraint.rewrite_instructions)
        user = (
            as_data(
                "EMAIL TO REPLY TO",
                f"Subject: {email.subject}\n\n{email.body_preview}",
            )
            + f"\n\nYour previous draft:\n{previous.draft_body}\n\n"
            "Fix these issues, keep the user's voice, stay under 180 words:\n- "
            + "\n- ".join(fixes or ["Improve clarity and tone."])
            + "\n\nReturn only the improved reply body."
        )
        chain = SYS_USER_PROMPT | self.llm.text()
        body = _run_text(chain, self._system(context), user) or _mock_draft(email, triage)[1]
        return f"Re: {email.subject}", body.strip(), "professional", "Rewritten by Claude."


def _run_text(chain, system: str, user: str) -> str:
    try:
        return chain.invoke({"system": system, "user": user})
    except Exception:  # defensive: let the caller fall back to a template
        return ""


def _format_thread(context: RetrievedContext) -> str:
    return "\n".join(context.thread_history) if context.thread_history else "(no prior messages)"


def _format_memory(context: RetrievedContext) -> str:
    lines: list[str] = []
    for r in context.rules:
        lines.append(f"- Rule ({r.situation}): {r.preference}")
    for s in context.successes:
        phrases = "; ".join(s.reusable_phrases)
        lines.append(f"- Worked before ({s.situation}): {s.why_it_worked}. Phrases: {phrases}")
    for e in context.errors:
        lines.append(f"- Avoid ({e.situation}): {e.lesson} (bad: '{e.bad_output}')")
    return "\n".join(lines) if lines else "(none)"


# --------------------------------------------------------------------------
# Deterministic mock templates (offline). All are < 180 words and already pass
# the hard constraints for their scenario.
# --------------------------------------------------------------------------
def _mock_draft(email: Email, triage: TriageResult) -> tuple[str, str, str, str]:
    name = _first_name(email.sender_name)
    blob = f"{email.subject} {email.body_preview}".lower()
    cat = triage.category
    subject = f"Re: {email.subject}"

    if cat == Category.PAYMENT:
        body = (
            f"Hi {name},\n\nThanks for following up on this. The team is reviewing it now and "
            "the payment is going through our internal approval process. I want to make sure we "
            "get you the right details, so I'll confirm the exact timing as soon as the review "
            "is complete.\n\nIf there's anything you need from my side in the meantime, such as an "
            "updated invoice, happy to sort that out.\n\nBest,\nTheo"
        )
        return subject, body, "reassuring-professional", "Payment question: routed to review/approval framing."

    if cat == Category.PRODUCT_ACCESS:
        body = (
            f"Hi {name},\n\nThanks for flagging this, and sorry for the hassle. So I can pinpoint "
            "what happened, could you send a quick screenshot of what you're seeing? In the "
            "meantime it's worth checking the web dashboard directly, since credits sometimes "
            "appear under a different section there.\n\nOnce I see the screenshot I'll get this "
            "sorted quickly.\n\nBest,\nTheo"
        )
        return subject, body, "helpful-professional", "Access issue: requested screenshot + dashboard check."

    is_rate = bool(re.search(r"[$£€]\s?\d", blob)) or "rate" in blob or "fee" in blob
    if cat == Category.CREATOR_PARTNERSHIP and is_rate:
        body = (
            f"Hi {name},\n\nThanks for sharing your rate, and for being upfront about it. To be "
            "transparent, at this stage we're running an early pilot with a limited budget, so a "
            "fixed fee at that level is above what we can commit to right now.\n\nWe'd love to still "
            "work together. One option we're happy to explore is a smaller base fee plus a revenue "
            "share, which keeps things workable for the pilot, and we'd be glad to reconnect on a "
            "larger budget as things grow.\n\nWould that structure work for you?\n\nBest,\nTheo"
        )
        return subject, body, "warm-direct", "Rate negotiation: pilot-budget framing, door left open."

    if "tracking" in blob:
        body = (
            f"Hi {name},\n\nHappy to get you set up with a tracking link. Could you confirm which "
            "campaign or video this is for so I generate the right one? I'll send it straight over "
            "once I have that.\n\nBest,\nTheo"
        )
        return subject, body, "helpful-professional", "Tracking link: confirm campaign then provide."

    if cat == Category.MEETING:
        body = (
            f"Hi {name},\n\nHappy to find a time. I'm generally free Tuesday or Thursday afternoon "
            "this week. Let me know what works on your side and I'll send an invite, or feel free "
            "to drop a couple of slots and I'll confirm.\n\nBest,\nTheo"
        )
        return subject, body, "friendly-professional", "Meeting: offered availability, asked for slots."

    if cat == Category.CREATOR_PARTNERSHIP and any(w in blob for w in ("post", "publish", "channel", "upload")):
        body = (
            f"Hi {name},\n\nThanks for checking. The team is reviewing the final cut now, and we "
            "want to make sure we're aligned before it goes live. I'll confirm the posting plan and "
            "channel shortly so we're on the same page.\n\nBest,\nTheo"
        )
        return subject, body, "aligned-professional", "Posting question: confirmed review + alignment."

    if cat == Category.CREATOR_PARTNERSHIP:
        body = (
            f"Hi {name},\n\nThanks for your interest in working together. At this stage we usually "
            "run a pilot: a smaller base fee plus a revenue share, with room to grow the budget as "
            "results come in. Happy to walk through the details on a quick call if that's useful.\n\n"
            "Would that work as a starting point?\n\nBest,\nTheo"
        )
        return subject, body, "warm-direct", "Collaboration: outlined pilot structure, kept open."

    body = (
        f"Hi {name},\n\nThanks for reaching out. The team is reviewing this now and I'll follow up "
        "shortly with the details. If anything is time-sensitive on your side, let me know and I'll "
        "prioritise it.\n\nBest,\nTheo"
    )
    return subject, body, "professional", "General reply acknowledging and setting a next step."
