"""Read-only email views for the frontend.

The frontend inbox needs sender metadata (name, address, subject) joined with
the triage analysis and any prepared draft. ``TriageResult`` alone doesn't carry
sender/subject, so this endpoint stitches Email + TriageResult + Draft into one
flat view keyed by email id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends

from ..context import user_scope
from pydantic import BaseModel

from ..models import Category, Priority
from ..repositories import get_store

router = APIRouter(prefix="/api/emails", tags=["emails"], dependencies=[Depends(user_scope)])


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

    # True when the shown message postdates the thread's last triage — i.e. a
    # newer reply arrived and this card's analysis/draft (if any) belongs to an
    # earlier message. The UI should invite a re-triage rather than trust it.
    needs_retriage: bool = False


def _preview(body: str, limit: int = 200) -> str:
    flat = " ".join(body.split())
    return flat[:limit] + "…" if len(flat) > limit else flat


@router.get("", response_model=list[EmailView])
def list_emails() -> list[EmailView]:
    """One row per thread: the latest message *from the other party*, joined
    with the thread's triage result and draft (if any).

    The store holds full thread histories (older messages and the user's own
    sent replies exist as drafting context) — those must not show up as
    separate inbox rows, and the row that does appear must carry the
    counterpart's newest reply, not the first message of the conversation.
    """
    store = get_store()
    triage_by_email = {t.email_id: t for t in store.triage.list()}
    draft_by_email = {d.email_id: d for d in store.drafts.list()}

    profile = store.profile.get("profile")
    own_email = (profile.email if profile else "").lower()

    def _is_own(e) -> bool:
        # Gmail SENT label is authoritative; fall back to address match for
        # seed/mock data that carries no labels.
        return e.from_me or (bool(own_email) and e.sender_email.lower() == own_email)

    by_thread: dict[str, list] = {}
    for e in store.emails.list():
        by_thread.setdefault(e.thread_id, []).append(e)

    representatives = []
    for thread in by_thread.values():
        inbound = [e for e in thread if not _is_own(e)]
        if not inbound:
            continue  # thread contains only our own sent mail — nothing to review
        rep = max(inbound, key=lambda e: e.received_at)
        # Analysis is bound to the *specific message* it was produced for.
        # Only attach triage/draft that belongs to the representative itself —
        # never staple an older message's analysis onto a newer reply (that
        # made the card's body and its Suggested Action disagree). If the
        # newest reply hasn't been triaged, the card shows no analysis and
        # flags needs_retriage.
        t = triage_by_email.get(rep.id)
        d = draft_by_email.get(rep.id)
        thread_has_prior_analysis = any(
            (e.id in triage_by_email or e.id in draft_by_email) for e in thread
        )
        needs_retriage = t is None and thread_has_prior_analysis
        representatives.append((rep, t, d, needs_retriage))

    representatives.sort(key=lambda item: item[0].received_at, reverse=True)

    views: list[EmailView] = []
    for e, t, d, needs_retriage in representatives:
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
                needs_retriage=needs_retriage,
            )
        )
    return views
