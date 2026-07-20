"""Email classifier -> TriageResult.

Real path: an LCEL chain ``prompt | llm.structured(_ClassifyOut)`` — LangChain
drives Claude via tool-calling to return schema-valid JSON, primed with Theo's
context and the spec's Need Reply / Important / Low Priority criteria.
Mock path: transparent keyword heuristics so the loop and tests run offline.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from ..models import Category, Email, Priority, TriageResult
from ._chains import SYS_USER_PROMPT
from ._prompt_layers import as_data
from ._theo import THEO_CONTEXT
from .llm_client import LLMClient, get_llm_client


class _ClassifyOut(BaseModel):
    """Structured output schema for the classifier (excludes email_id)."""

    category: Category
    priority: Priority
    needs_reply: bool
    confidence: float = Field(ge=0, le=1)
    summary: str
    why_it_matters: str
    suggested_action: str
    deadline_if_any: Optional[str] = None
    risk_if_ignored: str


_SYSTEM = f"""{THEO_CONTEXT}

You are an email triage classifier. Classify one email and return structured JSON.

Category guidance:
- Payment: payment status, invoices, "when will I get paid", fees tied to money owed.
- Product Access: login/access problems, credits redeemed in the wrong place, codes.
- Creator Partnership: collaboration terms, rates/negotiation, tracking links, posting/channel questions.
- Meeting: scheduling a call or meeting time.
- Important: high-value business opportunity or time-sensitive update not covered above.
- Low Priority: newsletters, generic sales pitches, automated notifications, nothing to act on.
- Need Reply: someone is waiting on a response and no more specific category fits.

needs_reply is TRUE when: a direct question, a request for confirmation/timeline/payment
update, a scheduling ask, a product-access issue, or a collaboration detail is raised.
needs_reply is FALSE for newsletters, generic pitches, and automated notifications.
Priority: High for money/access/time-sensitive; Medium for actionable but not urgent; Low otherwise.
Keep summary/why_it_matters/suggested_action to one short sentence each."""


class Classifier:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or get_llm_client()

    def classify(self, email: Email) -> TriageResult:
        if self.llm.mock:
            return TriageResult(email_id=email.id, **_mock_classify(email))

        user = as_data(
            "EMAIL TO CLASSIFY",
            f"From: {email.sender_name} <{email.sender_email}>\n"
            f"Subject: {email.subject}\n\n{email.body_preview}",
        )
        chain = SYS_USER_PROMPT | self.llm.structured(_ClassifyOut)
        try:
            out: _ClassifyOut = chain.invoke({"system": _SYSTEM, "user": user})
            data = out.model_dump()
        except Exception:  # defensive: fall back rather than crash the run
            data = _mock_classify(email)
        return TriageResult(email_id=email.id, **data)


def _has(blob: str, *words: str) -> bool:
    return any(w in blob for w in words)


def _mock_classify(email: Email) -> dict:
    blob = f"{email.subject} {email.body_preview}".lower()
    deadline = _extract_deadline(blob)

    # Order matters: most specific first.
    if _has(blob, "newsletter", "unsubscribe", "weekly digest", "% off", "sale ends", "no-reply"):
        return _mk(Category.LOW_PRIORITY, Priority.LOW, False, 0.9,
                   "Marketing newsletter with no action needed.",
                   "No business impact.", "Skip or archive.", "None.", deadline)

    if _has(blob, "credits", "redeem", "redeemed", "can't log", "cannot log", "access", "log in", "code isn't", "wrong place"):
        return _mk(Category.PRODUCT_ACCESS, Priority.HIGH, True, 0.85,
                   "Product access / credits issue needs help.",
                   "Blocks the creator from using the product.",
                   "Ask for a screenshot or point them to the web dashboard.",
                   "Creator stays blocked and frustrated.", deadline)

    is_rate = bool(re.search(r"[$£€]\s?\d", blob)) or _has(blob, "rate is", "my rate", "per video", "flat fee")
    if is_rate:
        return _mk(Category.CREATOR_PARTNERSHIP, Priority.HIGH, True, 0.8,
                   "Creator shared a rate / pricing for collaboration.",
                   "Pricing negotiation affecting partnership viability.",
                   "Reply with pilot-budget framing and keep the door open.",
                   "Lose the creator or overcommit budget.", deadline)

    if _has(blob, "payment", "invoice", "get paid", "when will i be paid", "paid yet", "payout"):
        return _mk(Category.PAYMENT, Priority.HIGH, True, 0.85,
                   "Creator is asking about payment status/timing.",
                   "Payment questions affect trust and the relationship.",
                   "Reference the team review/approval process and reassure.",
                   "Creator loses trust if unanswered.", deadline)

    if _has(blob, "tracking link", "tracking", "utm", "affiliate link"):
        return _mk(Category.CREATOR_PARTNERSHIP, Priority.MEDIUM, True, 0.8,
                   "Creator needs a tracking link.",
                   "Needed to attribute the campaign correctly.",
                   "Confirm the campaign and share/prepare the tracking link.",
                   "Attribution is lost without it.", deadline)

    if _has(blob, "meeting", "schedule", "available", "calendar", "call on", "time to chat", "book a time"):
        return _mk(Category.MEETING, Priority.MEDIUM, True, 0.8,
                   "Request to schedule a meeting/call.",
                   "Coordinating a live conversation.",
                   "Share availability or propose a couple of slots.",
                   "Scheduling slips.", deadline)

    if _has(blob, "post", "publish", "channel", "upload", "go live"):
        return _mk(Category.CREATOR_PARTNERSHIP, Priority.MEDIUM, True, 0.75,
                   "Question about posting/publishing the video.",
                   "Timing/placement of the collaboration content.",
                   "Confirm status and make sure both sides are aligned.",
                   "Misaligned expectations on publishing.", deadline)

    if _has(blob, "collaboration", "collab", "partnership", "work together", "structure", "deal"):
        return _mk(Category.CREATOR_PARTNERSHIP, Priority.MEDIUM, True, 0.75,
                   "Influencer asking about collaboration structure.",
                   "Shapes the partnership terms.",
                   "Outline the pilot structure (base + revenue share) and keep it open.",
                   "Opportunity cools if unanswered.", deadline)

    if _has(blob, "review", "feedback", "footage", "the cut", "draft video", "thoughts on"):
        return _mk(Category.NEED_REPLY, Priority.MEDIUM, True, 0.7,
                   "Someone is asking for video review feedback.",
                   "They are blocked waiting on input.",
                   "Give concise feedback or say when it's coming.",
                   "Delays the content.", deadline)

    # Fallback.
    needs = "?" in email.body_preview or "?" in email.subject
    return _mk(Category.NEED_REPLY if needs else Category.FYI,
               Priority.MEDIUM if needs else Priority.LOW, needs, 0.55,
               "General message.",
               "Unclear business impact." if not needs else "Sender is waiting on a reply.",
               "Reply if relevant." if needs else "Read for awareness.",
               "Low." if not needs else "Sender may follow up.", deadline)


def _mk(category, priority, needs_reply, confidence, summary, why, action, risk, deadline):
    return {
        "category": category,
        "priority": priority,
        "needs_reply": needs_reply,
        "confidence": confidence,
        "summary": summary,
        "why_it_matters": why,
        "suggested_action": action,
        "deadline_if_any": deadline,
        "risk_if_ignored": risk,
    }


def _extract_deadline(blob: str) -> Optional[str]:
    m = re.search(
        r"(by|until|before)\s+([a-z0-9 ,]+?\d{1,2}(?:st|nd|rd|th)?(?:\s+\w+)?)",
        blob,
    )
    return m.group(0) if m else None
