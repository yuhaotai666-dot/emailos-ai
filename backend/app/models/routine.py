"""Proactive scheduling: routines (what to run, when) and nudges (the results).

Nudges are in-app only — the proactive engine never sends anything anywhere;
it prepares information for Theo to see when he opens the app.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Routine(BaseModel):
    id: str = Field(default_factory=_uuid)
    title: str
    # What Ivy should do. For kind="triage_brief" this is informational;
    # for kind="ivy_task" it is the prompt handed to the supervisor.
    prompt: str = ""
    schedule: str = "daily"  # "daily" | "weekly"
    time: str = "08:30"  # local HH:MM
    enabled: bool = True
    kind: str = "ivy_task"  # "triage_brief" | "ivy_task"
    created_from: str = "user"  # "seed" | "user" | "chat"
    created_at: datetime = Field(default_factory=_now)
    last_run_at: Optional[datetime] = None


class Nudge(BaseModel):
    """One proactive output, surfaced on the Home page."""

    id: str = Field(default_factory=_uuid)
    routine_id: str = ""
    title: str
    body: str = ""
    items: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    read: bool = False
