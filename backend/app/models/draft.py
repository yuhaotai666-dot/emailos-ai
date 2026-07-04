"""Reply draft held in the review queue.

A draft is NEVER sent automatically. ``status`` tracks the human review lifecycle.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .enums import DraftStatus
from .evaluation import Critique, Evaluation


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConstraintCheck(BaseModel):
    passed: bool
    failed_constraints: list[str] = Field(default_factory=list)
    rewrite_instructions: str = ""


class Draft(BaseModel):
    id: str = Field(default_factory=_uuid)
    email_id: str
    original_email_summary: str

    subject_suggestion: Optional[str] = None
    draft_body: str
    tone: str = "professional"
    reasoning: str = ""

    status: DraftStatus = DraftStatus.PENDING_REVIEW
    version: int = 1
    created_at: datetime = Field(default_factory=_now)

    critique: Optional[Critique] = None
    evaluation: Optional[Evaluation] = None
    constraints_passed: bool = True
    constraint_detail: Optional[ConstraintCheck] = None
