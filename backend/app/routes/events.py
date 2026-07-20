"""Manual event tags: user-created groups for emails.

Deliberately no LLM anywhere in this file — tags are created, assigned, and
removed only by explicit user action. The AI triage category on an email is a
separate field and is not touched here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import user_scope
from ..models import EmailEventLink, EventTag
from ..repositories import get_store

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(user_scope)])


class EventsSnapshot(BaseModel):
    """Everything the frontend needs in one fetch."""

    events: list[EventTag]
    assignments: dict[str, str]  # email_id -> event_id


class EventCreate(BaseModel):
    # The client may supply its own id so optimistic UI updates keep working.
    id: Optional[str] = None
    name: str
    color: str = "slate"


class EventPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


class AssignBody(BaseModel):
    event_id: Optional[str] = None  # null clears the email's tag


@router.get("", response_model=EventsSnapshot)
def snapshot() -> EventsSnapshot:
    store = get_store()
    return EventsSnapshot(
        events=store.events.list(),
        assignments={l.email_id: l.event_id for l in store.email_events.list()},
    )


@router.post("", response_model=EventTag)
def create_event(body: EventCreate) -> EventTag:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Event name cannot be empty")
    tag = EventTag(name=name, color=body.color, **({"id": body.id} if body.id else {}))
    return get_store().events.add(tag)


@router.patch("/{event_id}", response_model=EventTag)
def update_event(event_id: str, body: EventPatch) -> EventTag:
    store = get_store()
    tag = store.events.get(event_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if body.name is not None and body.name.strip():
        tag.name = body.name.strip()
    if body.color is not None:
        tag.color = body.color
    return store.events.update(tag)


@router.delete("/{event_id}")
def delete_event(event_id: str) -> dict:
    store = get_store()
    if not store.events.delete(event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    # Cascade: clear the tag from every email that carried it.
    for link in store.email_events.list():
        if link.event_id == event_id:
            store.email_events.delete(link.email_id)
    return {"deleted": event_id}


@router.put("/assignments/{email_id}")
def assign(email_id: str, body: AssignBody) -> dict:
    store = get_store()
    if body.event_id is None:
        store.email_events.delete(email_id)
        return {"email_id": email_id, "event_id": None}
    if store.events.get(body.event_id) is None:
        raise HTTPException(status_code=404, detail="Event not found")
    store.email_events.update(EmailEventLink(email_id=email_id, event_id=body.event_id))
    return {"email_id": email_id, "event_id": body.event_id}
