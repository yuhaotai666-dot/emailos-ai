"""Persistent sub-agent (specialist) definitions for the Ivy supervisor.

Ivy creates a specialist the first time a kind of task shows up, stores it
here, and reuses it for similar tasks later. Specialists never gain email-
sending abilities — there is no such tool to grant.
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


class SubAgent(BaseModel):
    id: str = Field(default_factory=_uuid)
    # Short unique handle Ivy uses to address it, e.g. "rate-analyst".
    name: str
    # "system" = built-in domain agent (email/meeting/reminder), "custom" =
    # created by Ivy from a user request.
    kind: str = "custom"
    # What tasks this specialist handles — Ivy matches new tasks against this.
    description: str
    # The specialist's standing instructions (its system prompt).
    system_prompt: str
    # Names of tools from the shared toolbox this specialist may use.
    tools: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    runs: int = 0
    last_used_at: Optional[datetime] = None
    # Human-facing name for the Powers/"My Skills" UI, e.g. "Email Employee".
    # System agents without one stay listed under "Ivy's core team" instead.
    display_name: Optional[str] = None
    # Whether the underlying integration is actually linked. Only meaningful
    # for system agents with a display_name; recomputed per-request by the
    # route, never trusted from what's persisted on disk.
    connected: bool = True


class ChatRequest(BaseModel):
    message: str
    conversation_id: str = "default"


class ChatEvent(BaseModel):
    """One step of visible agent activity, e.g. tool use or delegation."""

    kind: str  # "tool" | "specialist_created" | "delegated" | "review"
    label: str


class ChatResponse(BaseModel):
    reply: str
    events: list[ChatEvent] = Field(default_factory=list)
    conversation_id: str = "default"
