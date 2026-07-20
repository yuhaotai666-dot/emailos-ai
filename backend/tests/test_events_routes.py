"""Manual event tags: CRUD, assignment, and cascade behaviour."""
from __future__ import annotations

import pytest

import app.repositories.local_store as local_store
from app.routes.events import (
    AssignBody,
    EventCreate,
    EventPatch,
    assign,
    create_event,
    delete_event,
    snapshot,
    update_event,
)


@pytest.fixture
def events_store(store, monkeypatch):
    monkeypatch.setattr(local_store, "_store", store)
    return store


def test_create_list_and_assign(events_store):
    tag = create_event(EventCreate(name="Youtube Partnership", color="amber"))
    assert tag.id and tag.color == "amber"

    assign("em-001", AssignBody(event_id=tag.id))
    snap = snapshot()
    assert any(e.id == tag.id for e in snap.events)
    assert snap.assignments["em-001"] == tag.id


def test_client_supplied_id_is_kept(events_store):
    tag = create_event(EventCreate(id="evt-client-1", name="X Partnership"))
    assert tag.id == "evt-client-1"


def test_rename_and_recolor(events_store):
    tag = create_event(EventCreate(name="tmp"))
    update_event(tag.id, EventPatch(name="Renamed", color="rose"))
    stored = events_store.events.get(tag.id)
    assert stored.name == "Renamed" and stored.color == "rose"


def test_clearing_an_assignment(events_store):
    tag = create_event(EventCreate(name="t"))
    assign("em-002", AssignBody(event_id=tag.id))
    assign("em-002", AssignBody(event_id=None))
    assert "em-002" not in snapshot().assignments


def test_delete_cascades_assignments(events_store):
    tag = create_event(EventCreate(name="doomed"))
    assign("em-001", AssignBody(event_id=tag.id))
    assign("em-003", AssignBody(event_id=tag.id))
    delete_event(tag.id)
    snap = snapshot()
    assert all(e.id != tag.id for e in snap.events)
    assert "em-001" not in snap.assignments and "em-003" not in snap.assignments


def test_assign_unknown_event_404s(events_store):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        assign("em-001", AssignBody(event_id="no-such-event"))


def test_assignments_survive_email_replace_all(events_store):
    """Tags must outlive the provider wiping/rebuilding the email collection."""
    tag = create_event(EventCreate(name="sticky"))
    assign("em-001", AssignBody(event_id=tag.id))
    events_store.emails.replace_all(events_store.emails.list())  # simulated refetch
    assert snapshot().assignments["em-001"] == tag.id
