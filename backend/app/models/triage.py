"""Triage result produced by the classifier."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .enums import Category, Priority


class TriageResult(BaseModel):
    email_id: str
    category: Category
    priority: Priority
    needs_reply: bool
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    why_it_matters: str
    suggested_action: str
    deadline_if_any: Optional[str] = None
    risk_if_ignored: str
