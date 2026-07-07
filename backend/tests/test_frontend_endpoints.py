"""Frontend-facing derived endpoints: memory profile, people, profile, meetings, todos.

Route functions read the global store, so these tests point the module-level
``_store`` at the seeded fixture store (all on the mock LLM path).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.repositories.local_store as local_store
from app.models import UserProfile
from app.routes.meetings import list_meetings
from app.routes.memory import get_memory_profile
from app.routes.people import get_person, list_people
from app.routes.profile import get_profile, put_profile
from app.routes.todos import list_todos
from app.services.memory_service import MemoryService


@pytest.fixture
def live_store(store, engine, settings, monkeypatch):
    """Run triage on the fixture store, then make it the app-global store.
    Route-level settings are pinned to the offline fixture so a real .env
    (gmail/google providers) can never leak network calls into tests."""
    engine.run_triage()
    monkeypatch.setattr(local_store, "_store", store)
    import app.routes.meetings as meetings_route

    monkeypatch.setattr(meetings_route, "get_settings", lambda: settings)
    return store


def test_memory_profile_groups_sections(store, mock_llm):
    profile = MemoryService(store=store, llm=mock_llm).memory_profile()
    titles = [s.title for s in profile.sections]
    for expected in ("Communication style", "Common phrases", "Business context",
                     "Partnership preferences", "Payment rules", "Product access rules"):
        assert expected in titles
    payment = next(s for s in profile.sections if s.title == "Payment rules")
    assert any("review" in item.lower() or "approval" in item.lower() for item in payment.items)


def test_people_derived_with_provenance(live_store):
    people = list_people()
    assert len(people) == 10  # one per unique mock sender
    maya = next(p for p in people if p.email == "maya@creators.example")
    assert maya.status == "Waiting for payment"
    assert maya.open_threads >= 1
    assert maya.claims and maya.claims[0].source_type == "email"
    assert maya.claims[0].confidence in ("high", "medium", "low")
    # Detail lookup by slug id round-trips.
    assert get_person(maya.id).email == maya.email


def test_person_404(live_store):
    with pytest.raises(HTTPException) as exc:
        get_person("nobody-at-nowhere")
    assert exc.value.status_code == 404


def test_profile_defaults_and_roundtrip(live_store):
    assert get_profile().name == "Theo"  # defaults before onboarding
    saved = put_profile(UserProfile(name="Theo", assistant_name="Ivy", routines=["Morning triage"]))
    assert saved.id == "profile"
    assert get_profile().routines == ["Morning triage"]


def test_meetings_sorted_with_recap(live_store):
    meetings = list_meetings()
    assert len(meetings) == 3
    starts = [m.starts_at for m in meetings]
    assert starts == sorted(starts)
    assert any(m.follow_up is not None for m in meetings)  # at least one recap draft


def test_todos_aggregate_emails_and_meetings(live_store):
    todos = list_todos()
    sources = {t.source for t in todos}
    assert sources == {"email", "meeting"}
    email_todos = [t for t in todos if t.source == "email"]
    assert len(email_todos) == 9  # one per needs-reply email
    assert all(t.text for t in todos)
