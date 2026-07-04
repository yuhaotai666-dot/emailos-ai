"""Memory service: CRUD over the three memory stores + learn-from-edit.

learn-from-edit compares the human-edited draft against the AI draft and proposes
memory updates. It is semi-automatic: suggestions are only persisted when
``auto_save=True`` (rule #: never silently mutate memory).
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from ..models import (
    ErrorCase,
    LearnFromEditRequest,
    LearnFromEditResult,
    MemoryRule,
    SuccessPattern,
)
from ..repositories import LocalStore, get_store
from ._chains import SYS_USER_PROMPT
from ._theo import FORBIDDEN_PHRASES
from .llm_client import LLMClient, get_llm_client


class _LearnOut(BaseModel):
    extracted_preference: str
    situation: str = ""
    preference: str = ""
    removed_bad_phrase: str = ""
    better_phrase: str = ""


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
