"""Rich per-person profiles for the People pages.

Everything is *derived* from what the agent already knows — emails, triage
results, and drafts — so profiles stay in sync with each run and work fully
offline. Claims carry provenance (source type, observed date, confidence)
so the UI can show where each fact came from.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..context import user_scope
from pydantic import BaseModel, Field

from ..models import Category, Draft, Email, TriageResult
from ..repositories import get_store

router = APIRouter(prefix="/api/people", tags=["people"], dependencies=[Depends(user_scope)])

_TLDS = {"com", "io", "co", "dev", "org", "net", "ai", "app", "tv", "example"}


# ---- view models ------------------------------------------------------------
class PersonThread(BaseModel):
    subject: str
    snippet: str
    needs_reply_from: str  # "you" | "them"
    suggested_next: str


class PersonClaim(BaseModel):
    text: str
    source_type: str = "email"  # "email" | "meeting" | "manual note"
    observed_date: datetime
    confidence: str  # "high" | "medium" | "low"


class ImportantContext(BaseModel):
    label: str
    value: str


class PersonView(BaseModel):
    id: str
    name: str
    email: str
    company: str
    role: str = ""
    channel: str = "Email"
    relationship: str
    status: str
    active: str  # "active" | "paused" | "needs follow-up"
    stage: str
    ai_description: str
    who_they_are: str
    relationship_context: str
    last_contacted: datetime
    open_threads: int
    threads: list[PersonThread] = Field(default_factory=list)
    suggested_next_action: str = ""
    communication_tone: list[str] = Field(default_factory=list)
    communication_notes: str = ""
    important_context: list[ImportantContext] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    claims: list[PersonClaim] = Field(default_factory=list)


# ---- derivation helpers -----------------------------------------------------
def _slug(email: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-")


def _company_from_email(email: str) -> str:
    domain = email.split("@")[-1]
    labels = domain.split(".")
    if len(labels) > 1 and labels[-1].lower() in _TLDS:
        labels = labels[:-1]
    return " ".join(w.capitalize() for w in labels) or domain


_RELATIONSHIP = {
    Category.CREATOR_PARTNERSHIP: "Creator Partner",
    Category.PAYMENT: "Creator Partner",
    Category.PRODUCT_ACCESS: "Product Access",
    Category.MEETING: "Prospective Partner",
    Category.SALES: "Sales Lead",
    Category.IMPORTANT: "Partner",
}


def _status(t: Optional[TriageResult]) -> tuple[str, str]:
    """-> (status, active) for the latest triage of this person."""
    if t is None:
        return "Active collaboration", "active"
    if t.category == Category.PAYMENT:
        return "Waiting for payment", "needs follow-up"
    if t.category == Category.PRODUCT_ACCESS:
        return "Access issue", "needs follow-up"
    if t.category == Category.MEETING:
        return "Waiting for them", "active"
    if t.needs_reply:
        return "Needs reply", "needs follow-up"
    return "Active collaboration", "active"


def _confidence_label(c: float) -> str:
    return "high" if c >= 0.8 else "medium" if c >= 0.6 else "low"


def _build_person(
    sender_email: str,
    group: list[Email],
    triage_by_email: dict[str, TriageResult],
    draft_by_email: dict[str, Draft],
) -> PersonView:
    group = sorted(group, key=lambda e: e.received_at, reverse=True)
    latest = group[0]
    latest_triage = triage_by_email.get(latest.id)

    cats = Counter(
        triage_by_email[e.id].category for e in group if e.id in triage_by_email
    )
    top_cat = cats.most_common(1)[0][0] if cats else None
    relationship = _RELATIONSHIP.get(top_cat, "Contact") if top_cat else "Contact"
    status, active = _status(latest_triage)

    threads: list[PersonThread] = []
    for e in group:
        t = triage_by_email.get(e.id)
        if t and t.needs_reply:
            threads.append(
                PersonThread(
                    subject=e.subject,
                    snippet=t.summary or e.body_preview,
                    needs_reply_from="you",
                    suggested_next=t.suggested_action,
                )
            )

    claims: list[PersonClaim] = []
    for e in group:
        t = triage_by_email.get(e.id)
        if not t:
            continue
        claims.append(
            PersonClaim(
                text=t.summary,
                source_type="email",
                observed_date=e.received_at,
                confidence=_confidence_label(t.confidence),
            )
        )

    context: list[ImportantContext] = []
    if latest_triage:
        context.append(ImportantContext(label="Latest ask", value=latest_triage.suggested_action))
        if latest_triage.deadline_if_any:
            context.append(ImportantContext(label="Deadline", value=latest_triage.deadline_if_any))
        if latest_triage.risk_if_ignored:
            context.append(ImportantContext(label="Risk if ignored", value=latest_triage.risk_if_ignored))
    draft = draft_by_email.get(latest.id)
    if draft:
        context.append(ImportantContext(label="Draft status", value=draft.status.value))

    uncertainties = [
        f"Low-confidence read on: {triage_by_email[e.id].summary}"
        for e in group
        if e.id in triage_by_email and triage_by_email[e.id].confidence < 0.6
    ]

    company = _company_from_email(sender_email)
    summary = latest_triage.summary if latest_triage else latest.body_preview
    why = latest_triage.why_it_matters if latest_triage else ""

    return PersonView(
        id=_slug(sender_email),
        name=latest.sender_name,
        email=sender_email,
        company=company,
        relationship=relationship,
        status=status,
        active=active,
        stage=status,
        ai_description=f"{summary} {why}".strip(),
        who_they_are=f"{latest.sender_name} ({company}) — {relationship.lower()} in Theo's inbox.",
        relationship_context=(
            f"{len(group)} email(s) on record; most frequent topic: "
            f"{top_cat.value if top_cat else 'n/a'}."
        ),
        last_contacted=latest.received_at,
        open_threads=len(threads),
        threads=threads,
        suggested_next_action=latest_triage.suggested_action if latest_triage else "",
        communication_tone=[draft.tone] if draft else ["professional"],
        communication_notes="Derived from the tone of drafts prepared for this person.",
        important_context=context,
        uncertainties=uncertainties,
        claims=claims,
    )


def _all_people() -> list[PersonView]:
    store = get_store()
    triage_by_email = {t.email_id: t for t in store.triage.list()}
    draft_by_email = {d.email_id: d for d in store.drafts.list()}

    by_sender: dict[str, list[Email]] = defaultdict(list)
    for e in store.emails.list():
        by_sender[e.sender_email].append(e)

    people = [
        _build_person(sender, group, triage_by_email, draft_by_email)
        for sender, group in by_sender.items()
    ]
    people.sort(key=lambda p: p.last_contacted, reverse=True)
    return people


# ---- endpoints ----------------------------------------------------------------
@router.get("", response_model=list[PersonView])
def list_people() -> list[PersonView]:
    return _all_people()


@router.get("/{person_id}", response_model=PersonView)
def get_person(person_id: str) -> PersonView:
    for p in _all_people():
        if p.id == person_id:
            return p
    raise HTTPException(status_code=404, detail="Person not found")
