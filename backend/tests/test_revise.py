"""Conversational draft revision + standing-preference rule suggestion."""
from __future__ import annotations

import pytest

import app.repositories.local_store as local_store
from app.routes.drafts import ReviseRequest, revise_draft


@pytest.fixture
def with_drafts(engine, monkeypatch):
    """A triaged store (so drafts exist) wired as the app-global store."""
    engine.run_triage()
    monkeypatch.setattr(local_store, "_store", engine.store)
    return engine.store


def _a_draft(store):
    return store.drafts.list()[0]


def test_revise_updates_the_draft(with_drafts):
    d = _a_draft(with_drafts)
    resp = revise_draft(d.id, ReviseRequest(message="Make it shorter."))
    assert resp.draft.id == d.id
    assert resp.reply_text  # an ack for the chat


def test_one_off_feedback_suggests_no_rule(with_drafts):
    d = _a_draft(with_drafts)
    resp = revise_draft(d.id, ReviseRequest(message="Make THIS one a bit warmer."))
    assert resp.suggested_rule is None


def test_standing_feedback_suggests_a_rule(with_drafts):
    d = _a_draft(with_drafts)
    resp = revise_draft(
        d.id, ReviseRequest(message="From now on, always keep replies to three sentences.")
    )
    assert resp.suggested_rule is not None
    assert "three sentences" in resp.suggested_rule.preference.lower()


def test_chinese_standing_feedback_suggests_a_rule(with_drafts):
    d = _a_draft(with_drafts)
    resp = revise_draft(d.id, ReviseRequest(message="这类邮件以后都要更简短一点"))
    assert resp.suggested_rule is not None
