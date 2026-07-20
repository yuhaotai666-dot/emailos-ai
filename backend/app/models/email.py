"""Email model.

We deliberately store only a ``body_preview`` (a short snippet), never the full
body, to honour the privacy rule "do not log/store full email bodies".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import Category, Priority


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Email(BaseModel):
    id: str = Field(default_factory=_uuid)
    thread_id: str = Field(default_factory=_uuid)
    sender_name: str
    sender_email: str
    subject: str
    body_preview: str
    received_at: datetime = Field(default_factory=_now)

    # Filled in by the classifier; optional at ingest time.
    category: Optional[Category] = None
    priority: Optional[Priority] = None
    needs_reply: Optional[bool] = None

    source: str = "mock"
    # True when the user themselves sent this message (Gmail SENT label). More
    # reliable than matching sender address, since the connected Gmail account
    # may send from an address that differs from the login/profile email.
    from_me: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
