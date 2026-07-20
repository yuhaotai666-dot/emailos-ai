"""User-defined event tags for grouping emails.

Purely manual: the user creates tags and assigns them to emails by hand.
No LLM ever assigns these — that is a product decision, not a limitation
(the AI triage category on an email is a separate, parallel field).

Assignments live in their own collection (not on ``Email``) because the
email collection is wiped and rebuilt on every provider fetch — tags must
survive that.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventTag(BaseModel):
    id: str = Field(default_factory=_uuid)
    name: str
    color: str = "slate"  # frontend palette key: amber|blue|rose|green|violet|slate
    created_at: datetime = Field(default_factory=_now)


class EmailEventLink(BaseModel):
    """One email's manual tag. Keyed by email_id — an email has at most one."""

    email_id: str
    event_id: str
