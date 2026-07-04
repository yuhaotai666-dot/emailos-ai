"""Agent run record and the Daily Email Brief returned by a triage run."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .draft import Draft
from .triage import TriageResult


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FollowUp(BaseModel):
    email_id: str
    sender_name: str
    subject: str
    reason: str
    deadline_if_any: Optional[str] = None


class DailyBrief(BaseModel):
    id: str = Field(default_factory=_uuid)
    generated_at: datetime = Field(default_factory=_now)
    top_priority: list[TriageResult] = Field(default_factory=list)
    need_reply: list[TriageResult] = Field(default_factory=list)
    drafts_ready: list[str] = Field(default_factory=list, description="Draft ids")
    follow_ups: list[FollowUp] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    summary: str = ""


class AgentRun(BaseModel):
    id: str = Field(default_factory=_uuid)
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    emails_processed: int = 0
    drafts_created: int = 0
    errors: list[str] = Field(default_factory=list)
    total_cost_estimate: float = 0.0
    summary: str = ""


class TriageRunResponse(BaseModel):
    """Full payload returned by POST /api/agent/run-triage."""
    brief: DailyBrief
    triage_results: list[TriageResult]
    drafts: list[Draft]
    follow_ups: list[FollowUp]
    run: AgentRun
