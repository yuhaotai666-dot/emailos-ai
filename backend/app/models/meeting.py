"""Meeting domain: schedule, prep, and post-meeting follow-up.

The follow-up recap contains a *draft* email (subject/summary/todos) that the
user can review and copy — consistent with the global rule, nothing is sent.
Meetings come from a ``MeetingProvider`` seam (mock now, Google Calendar later).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


class MeetingTodo(BaseModel):
    owner: str
    task: str
    due: Optional[str] = None


class MeetingFollowUp(BaseModel):
    """AI-drafted recap email content. Review-only; never sent."""

    recipients: list[str] = Field(default_factory=list)
    subject: str = ""
    summary: str = ""
    todos: list[MeetingTodo] = Field(default_factory=list)


class Meeting(BaseModel):
    id: str = Field(default_factory=_uuid)
    title: str
    starts_at: datetime
    attendees: list[str] = Field(default_factory=list)
    prep: str = ""
    questions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    # Present once the meeting has happened (mock data ships with examples).
    follow_up: Optional[MeetingFollowUp] = None
    source: str = "mock"
