"""Calendar event mapping + prep generation (offline, no network)."""
from __future__ import annotations

from app.services.calendar_provider import GoogleCalendarProvider, _to_meeting


def _event(**over):
    ev = {
        "id": "evt-123",
        "summary": "Creator sync — Aisha",
        "start": {"dateTime": "2026-07-08T10:30:00+01:00"},
        "attendees": [
            {"email": "aisha@example.com", "displayName": "Aisha Rahman"},
            {"email": "theo@superintern.ai"},
            {"email": "room-3@resource.calendar.google.com", "resource": True},
        ],
    }
    ev.update(over)
    return ev


def test_to_meeting_maps_event():
    m = _to_meeting(_event())
    assert m.id == "evt-123"
    assert m.title == "Creator sync — Aisha"
    assert m.starts_at.year == 2026
    assert m.attendees == ["Aisha Rahman", "theo"]  # resource room excluded
    assert m.source == "google"


def test_to_meeting_allday_and_missing():
    allday = _to_meeting(_event(start={"date": "2026-07-09"}))
    assert allday is not None and allday.starts_at.hour == 0
    assert _to_meeting(_event(start={})) is None


def test_mock_prep_generated_once(store, mock_llm, settings):
    provider = GoogleCalendarProvider(store=store, settings=settings, llm=mock_llm)
    meeting = _to_meeting(_event())
    provider._generate_prep(meeting)
    assert meeting.prep and meeting.questions and meeting.action_items


def test_meetings_seeded_into_runtime_file(store):
    # Seed/runtime split: real calendar writes must never touch the seed file.
    assert len(store.meetings.list()) == 3  # seeded from mock_meetings.json
    assert not (store.data_dir / "meetings.json").name == "mock_meetings.json"
