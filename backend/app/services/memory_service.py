"""Memory service: CRUD over the three memory stores + learn-from-edit.

learn-from-edit compares the human-edited draft against the AI draft and proposes
memory updates. It is semi-automatic: suggestions are only persisted when
``auto_save=True`` (rule #: never silently mutate memory).
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from collections import Counter, defaultdict

from ..models import (
    ErrorCase,
    LearnFromEditRequest,
    LearnFromEditResult,
    MemoryProfile,
    MemoryRule,
    MemorySection,
    SuccessPattern,
)
from ..repositories import LocalStore, get_store
from ._chains import SYS_USER_PROMPT
from ._theo import FORBIDDEN_PHRASES, PREFERRED_PHRASES
from .llm_client import LLMClient, get_llm_client

# ---- memory-profile grouping ------------------------------------------------
# Static sections describe Theo himself; dynamic sections come from MemoryRules.
_STYLE_ITEMS = [
    "Concise, polite, direct, professional",
    "No emojis, no exaggerated sales language",
    "Keeps replies under ~180 words",
    "When declining, keeps the door open for future collaboration",
]

_BUSINESS_ITEMS = [
    "SuperIntern — growth and partnerships",
    "Creator partnerships, influencer outreach, and collaboration negotiations",
    "Owns payments questions, tracking links, credits, and product access",
    "Early pilot stage with a limited budget",
]

# situation-keyword -> section title (checked in order; first hit wins)
_SECTION_RULES: list[tuple[tuple[str, ...], str]] = [
    (("payment", "invoice", "payout", "paid"), "Payment rules"),
    (("access", "credit", "login", "log in", "dashboard", "code"), "Product access rules"),
    (("rate", "pricing", "price", "partnership", "collab", "creator", "influencer"),
     "Partnership preferences"),
]
_FALLBACK_SECTION = "Reply preferences"


class _LearnOut(BaseModel):
    extracted_preference: str
    situation: str = ""
    preference: str = ""
    removed_bad_phrase: str = ""
    better_phrase: str = ""


class _FeedbackOut(BaseModel):
    """Was the user's revise feedback a one-off tweak or a standing rule?"""

    is_standing_preference: bool
    situation: str = ""  # what kind of email this applies to
    preference: str = ""  # the standing guidance to remember
    ack: str = ""  # one-line note of what changed


def _mock_feedback(message: str, situation_hint: str) -> tuple[Optional["MemoryRule"], str]:
    low = message.lower()
    standing = any(
        w in low
        for w in ("always", "from now on", "in future", "all ", "every", "这类", "以后", "都")
    )
    ack = "Updated the draft with your feedback."
    if standing:
        rule = MemoryRule(
            situation=situation_hint or "emails like this",
            preference=message.strip(),
            created_from="revise_feedback",
            confidence=0.5,
        )
        return rule, ack
    return None, ack


class MemoryService:
    def __init__(self, store: Optional[LocalStore] = None, llm: Optional[LLMClient] = None):
        self.store = store or get_store()
        self.llm = llm or get_llm_client()

    # ---- rules ----
    def list_rules(self) -> list[MemoryRule]:
        return self.store.memory_rules.list()

    def add_rule(self, rule: MemoryRule) -> MemoryRule:
        return self.store.memory_rules.add(rule)

    # ---- errors ----
    def list_errors(self) -> list[ErrorCase]:
        return self.store.error_cases.list()

    def add_error(self, case: ErrorCase) -> ErrorCase:
        return self.store.error_cases.add(case)

    # ---- success patterns ----
    def list_success(self) -> list[SuccessPattern]:
        return self.store.success_patterns.list()

    def add_success(self, pattern: SuccessPattern) -> SuccessPattern:
        return self.store.success_patterns.add(pattern)

    # ---- grouped profile view ----
    def memory_profile(self) -> MemoryProfile:
        """Group everything the assistant knows into the memory-page sections."""
        # Common phrases: the static voice list + phrases proven in successes.
        phrases = list(PREFERRED_PHRASES)
        for s in self.list_success():
            for p in s.reusable_phrases:
                if p not in phrases:
                    phrases.append(p)

        # Dynamic rule sections (explicit rule.section wins over keyword inference).
        grouped: dict[str, list[str]] = defaultdict(list)
        for rule in self.list_rules():
            grouped[rule.section or _infer_section(rule.situation)].append(rule.preference)
        for err in self.list_errors():
            grouped[_infer_section(err.situation)].append(f"Avoid: {err.lesson}")

        # Important contacts, derived from who Theo actually emails.
        contacts = self._important_contacts()

        sections = [
            MemorySection(title="Communication style", items=_STYLE_ITEMS),
            MemorySection(title="Common phrases", items=phrases),
            MemorySection(title="Business context", items=_BUSINESS_ITEMS),
        ]
        if contacts:
            sections.append(MemorySection(title="Important contacts", items=contacts))
        for title in ("Partnership preferences", "Payment rules", "Product access rules",
                      _FALLBACK_SECTION):
            if grouped.get(title):
                sections.append(MemorySection(title=title, items=grouped[title]))
        return MemoryProfile(sections=sections)

    def _important_contacts(self, k: int = 4) -> list[str]:
        triage_by_email = {t.email_id: t for t in self.store.triage.list()}
        by_sender: dict[str, list] = defaultdict(list)
        for e in self.store.emails.list():
            by_sender[e.sender_email].append(e)

        rows = []
        for sender_email, group in by_sender.items():
            group.sort(key=lambda e: e.received_at, reverse=True)
            latest = group[0]
            cats = Counter(
                triage_by_email[e.id].category.value for e in group if e.id in triage_by_email
            )
            label = cats.most_common(1)[0][0] if cats else "Contact"
            rows.append((latest.received_at, f"{latest.sender_name} — {label}"))
        rows.sort(reverse=True)
        return [line for _, line in rows[:k]]

    # ---- learning ----
    def learn_from_edit(self, req: LearnFromEditRequest) -> LearnFromEditResult:
        if self.llm.mock:
            result = _mock_learn(req)
        else:
            result = self._llm_learn(req)
            if result is None:
                result = _mock_learn(req)

        if req.auto_save:
            if result.possible_memory_rule:
                self.add_rule(result.possible_memory_rule)
            if result.possible_error_case:
                self.add_error(result.possible_error_case)
            if result.possible_success_pattern:
                self.add_success(result.possible_success_pattern)
            result.saved = True
        return result

    def analyze_feedback(
        self, message: str, situation_hint: str = ""
    ) -> tuple[Optional[MemoryRule], str]:
        """Given revise feedback, return (suggested standing rule | None, ack).

        A rule is only *suggested* — the caller confirms with the user before
        saving (never silently mutate the knowledge base)."""
        if self.llm.mock:
            return _mock_feedback(message, situation_hint)
        system = (
            "The user gave feedback on an AI-drafted email reply. Decide whether it "
            "is a ONE-OFF tweak to just this draft, or a STANDING preference to apply "
            "to all similar emails in future. If standing, extract `situation` (what "
            "kind of email it applies to) and `preference` (the guidance to remember). "
            "Give a one-line `ack` of what you changed. Return JSON."
        )
        user = f"Email context: {situation_hint}\n\nUser feedback: {message}"
        chain = SYS_USER_PROMPT | self.llm.structured(_FeedbackOut)
        try:
            out: _FeedbackOut = chain.invoke({"system": system, "user": user})
        except Exception:
            return None, "Updated the draft with your feedback."
        ack = out.ack or "Updated the draft with your feedback."
        if out.is_standing_preference and out.preference:
            rule = MemoryRule(
                situation=out.situation or situation_hint or "email reply",
                preference=out.preference,
                created_from="revise_feedback",
                confidence=0.6,
            )
            return rule, ack
        return None, ack

    def _llm_learn(self, req: LearnFromEditRequest) -> Optional[LearnFromEditResult]:
        system = (
            "Theo edited an AI-drafted email reply. Infer the reusable preference behind the "
            "edit and, if applicable, the bad phrasing that was removed and the better "
            "replacement. Return JSON."
        )
        user = (
            f"Situation: {req.situation}\n\nAI draft:\n{req.original_draft}\n\n"
            f"Theo's edited version:\n{req.edited_draft}"
        )
        chain = SYS_USER_PROMPT | self.llm.structured(_LearnOut)
        try:
            out: _LearnOut = chain.invoke({"system": system, "user": user})
        except Exception:  # defensive: fall back to the deterministic path
            return None
        situation = out.situation or req.situation or "email reply"
        rule = MemoryRule(
            situation=situation,
            preference=out.preference or out.extracted_preference,
            example_good=out.better_phrase,
            example_bad=out.removed_bad_phrase,
            created_from="learn_from_edit",
            confidence=0.6,
        )
        error = None
        if out.removed_bad_phrase:
            error = ErrorCase(
                situation=situation,
                bad_output=out.removed_bad_phrase,
                correction=out.better_phrase,
                lesson=out.extracted_preference,
                error_type="tone",
            )
        return LearnFromEditResult(
            extracted_preference=out.extracted_preference,
            possible_memory_rule=rule,
            possible_error_case=error,
            possible_success_pattern=None,
        )


def _infer_section(situation: str) -> str:
    low = situation.lower()
    for keywords, title in _SECTION_RULES:
        if any(k in low for k in keywords):
            return title
    return _FALLBACK_SECTION


def _mock_learn(req: LearnFromEditRequest) -> LearnFromEditResult:
    situation = req.situation or "email reply"
    orig_low = req.original_draft.lower()

    removed_bad = next((p for p in FORBIDDEN_PHRASES if p in orig_low and p not in req.edited_draft.lower()), "")
    got_shorter = len(req.edited_draft) < len(req.original_draft) * 0.8

    if removed_bad:
        preference = f"Avoid phrasing like '{removed_bad}'; keep it professional and budget-aware."
        error = ErrorCase(
            situation=situation,
            bad_output=removed_bad,
            correction="Use pilot-stage / budget framing instead.",
            lesson=preference,
            error_type="tone",
        )
    elif got_shorter:
        preference = "Prefer shorter, more direct replies for this kind of email."
        error = None
    else:
        preference = "Match the wording and structure Theo chose in the edited version."
        error = None

    rule = MemoryRule(
        situation=situation,
        preference=preference,
        example_good=_first_sentence(req.edited_draft),
        example_bad=removed_bad,
        created_from="learn_from_edit",
        confidence=0.55,
    )
    return LearnFromEditResult(
        extracted_preference=preference,
        possible_memory_rule=rule,
        possible_error_case=error,
        possible_success_pattern=None,
    )


def _first_sentence(text: str) -> str:
    m = re.split(r"(?<=[.!?])\s", text.strip())
    return m[0] if m else text.strip()[:160]
