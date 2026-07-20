"""Derived contacts view for the frontend.

There is no standalone contact store yet, so we aggregate the people Theo emails
straight from the mock inbox: one contact per unique sender address, enriched
with their most common category, latest triage summary, and the tone of the
draft prepared for them.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends

from ..context import user_scope
from pydantic import BaseModel

from ..models import Email
from ..repositories import get_store

router = APIRouter(prefix="/api/contacts", tags=["contacts"], dependencies=[Depends(user_scope)])

_TLDS = {"com", "io", "co", "dev", "org", "net", "ai", "app", "example"}


class ContactView(BaseModel):
    id: str
    name: str
    email: str
    company: str
    relationship: str
    recent_summary: str
    preferred_tone: str
    last_contacted: datetime


def _company_from_email(email: str) -> str:
    domain = email.split("@")[-1]
    labels = domain.split(".")
    if len(labels) > 1 and labels[-1].lower() in _TLDS:
        labels = labels[:-1]
    return " ".join(w.capitalize() for w in labels) or domain


@router.get("", response_model=list[ContactView])
def list_contacts() -> list[ContactView]:
    store = get_store()
    triage_by_email = {t.email_id: t for t in store.triage.list()}
    draft_by_email = {d.email_id: d for d in store.drafts.list()}

    by_sender: dict[str, list[Email]] = defaultdict(list)
    for e in store.emails.list():
        by_sender[e.sender_email].append(e)

    contacts: list[ContactView] = []
    for sender_email, group in by_sender.items():
        group.sort(key=lambda e: e.received_at, reverse=True)
        latest = group[0]
        t = triage_by_email.get(latest.id)
        d = draft_by_email.get(latest.id)

        categories = [
            triage_by_email[e.id].category.value
            for e in group
            if e.id in triage_by_email
        ]
        relationship = Counter(categories).most_common(1)[0][0] if categories else "Contact"

        contacts.append(
            ContactView(
                id=sender_email,
                name=latest.sender_name,
                email=sender_email,
                company=_company_from_email(sender_email),
                relationship=relationship,
                recent_summary=t.summary if t else latest.body_preview,
                preferred_tone=d.tone if d else "professional",
                last_contacted=latest.received_at,
            )
        )

    contacts.sort(key=lambda c: c.last_contacted, reverse=True)
    return contacts
