"""Three memory types plus the learn-from-edit payloads."""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


class MemoryRule(BaseModel):
    id: str = Field(default_factory=_uuid)
    situation: str
    preference: str
    example_good: str = ""
    example_bad: str = ""
    created_from: str = "manual"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Optional grouping for the memory-profile view (e.g. "Payment rules").
    # Empty = inferred from the situation text by keyword.
    section: str = ""


class MemorySection(BaseModel):
    """One titled group of memory items for the frontend memory page."""

    title: str
    items: list[str] = Field(default_factory=list)


class MemoryProfile(BaseModel):
    """The grouped what-the-assistant-knows view shown on the memory page."""

    sections: list[MemorySection] = Field(default_factory=list)


class ErrorCase(BaseModel):
    id: str = Field(default_factory=_uuid)
    situation: str
    bad_output: str
    correction: str
    lesson: str
    error_type: str = "tone"


class SuccessPattern(BaseModel):
    id: str = Field(default_factory=_uuid)
    situation: str
    successful_reply: str
    why_it_worked: str
    reusable_phrases: list[str] = Field(default_factory=list)


class LearnFromEditRequest(BaseModel):
    original_draft: str
    edited_draft: str
    situation: str = ""
    email_id: Optional[str] = None
    auto_save: bool = False


class LearnFromEditResult(BaseModel):
    extracted_preference: str
    possible_memory_rule: Optional[MemoryRule] = None
    possible_error_case: Optional[ErrorCase] = None
    possible_success_pattern: Optional[SuccessPattern] = None
    saved: bool = False
