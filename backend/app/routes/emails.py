"""Read-only email views for the frontend.

The frontend inbox needs sender metadata (name, address, subject) joined with
the triage analysis and any prepared draft. ``TriageResult`` alone doesn't carry
sender/subject, so this endpoint stitches Email + TriageResult + Draft into one
flat view keyed by email id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..models import Category, Priority
from ..repositories import get_store

router = APIRouter(prefix="/api/emails", tags=["emails"])


class EmailView(BaseModel):
    # Email metadata
    id: str
    thread_id: str
    sender_name: str
    sender_email: str
    subject: str
    body_preview: str
    received_at: datetime
    source: str

    # Triage analysis (present once classified)
    category: Optional[Category] = None
    priority: Optional[Priority] = None
    needs_reply: Optional[bool] = None
    summary: Optional[str] = None
    why_it_matters: Optional[str] = None
    suggested_action: Optional[str] = None
    deadline_if_any: Optional[str] = None
    risk_if_ignored: Optional[str] = None
    confidence: Optional[float] = None

    # Linked draft (present once drafted)
    draft_id: Optional[str] = None
    draft_preview: Optional[str] = None


def _preview(body: str, limit: int = 200) -> str:
    flat = " ".join(body.split())
    return flat[:limit] + "…" if len(flat) > limit else flat


@router.get("", response_model=list[EmailView])
def list_emails() -> list[EmailView]:
    """Return every email joined with its triage result and draft (if any)."""
    store = get_store()
    triage_by_email = {t.email_id: t for t in store.triage.list()}
    draft_by_email = {d.email_id: d for d in store.drafts.list()}

    views: list[EmailView] = []
    for e in store.emails.list():
        t = triage_by_email.get(e.id)
        d = draft_by_email.get(e.id)
        views.append(
            EmailView(
                id=e.id,
                thread_id=e.thread_id,
                sender_name=e.sender_name,
                sender_email=e.sender_email,
                subject=e.subject,
                body_preview=e.body_preview,
                received_at=e.received_at,
                source=e.source,
                category=t.category if t else e.category,
                priority=t.priority if t else e.priority,
                needs_reply=t.needs_reply if t else e.needs_reply,
                summary=t.summary if t else None,
                why_it_matters=t.why_it_matters if t else None,
                suggested_action=t.suggested_action if t else None,
                deadline_if_any=t.deadline_if_any if t else None,
                risk_if_ignored=t.risk_if_ignored if t else None,
                confidence=t.confidence if t else None,
                draft_id=d.id if d else None,
                draft_preview=_preview(d.draft_body) if d else None,
            )
        )
    return views
