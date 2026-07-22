"""search_email / read_thread tools (offline / local-store path)."""
from __future__ import annotations

import pytest

import app.config as config
import app.repositories.local_store as local_store
from app.config import Settings
from app.services.agent_tools import read_thread, search_email


@pytest.fixture
def live(store, monkeypatch):
    monkeypatch.setattr(local_store, "_store", store)
    # Force the offline local-search path (real .env may point at Gmail).
    monkeypatch.setattr(config, "get_settings", lambda: Settings(email_provider="mock"))
    return store


def test_search_finds_rate_negotiation(live):
    # em-002 (Liam) is the "$600 per video" rate email in the seed.
    out = search_email.invoke({"query": "rate video"})
    assert "Liam" in out or "rate" in out.lower()


def test_search_reports_nothing_for_absent_terms(live):
    out = search_email.invoke({"query": "zzznonexistentqqq"})
    assert "No emails found" in out


def test_read_thread_returns_all_thread_messages_oldest_first(live):
    # th-001 has em-001 (Jun 30) + em-012 (Jul 5) in the seed.
    out = read_thread.invoke({"thread_ref": "thread:th-001"})
    assert "payment for my last video" in out.lower()  # em-001
    assert "following up again" in out.lower()  # em-012
    assert out.index("Jun 30") < out.index("Jul 05")  # oldest first


def test_read_thread_resolves_sender_name(live):
    out = read_thread.invoke({"thread_ref": "maya"})
    assert "payment" in out.lower()
