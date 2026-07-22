"""Structural guarantee: the app never sends email — only writes drafts.

Even with the gmail.compose scope (which technically permits send), the code
must contain no Gmail send call. This test fails loudly if one is ever added.
"""
from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Gmail API send call shapes we must never contain.
_SEND_PATTERNS = [
    re.compile(r"messages\(\)\s*\.\s*send\("),
    re.compile(r"\.send\(\s*userId"),
]


def test_no_gmail_send_call_anywhere():
    offenders = []
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in _SEND_PATTERNS:
            if pat.search(text):
                offenders.append(f"{path.name}: {pat.pattern}")
    assert not offenders, f"Gmail send call found (app must never send): {offenders}"


def test_push_to_gmail_noop_without_gmail(store, monkeypatch, engine):
    """With email_provider != gmail, push-to-gmail is a safe no-op."""
    import app.config as config
    import app.repositories.local_store as local_store
    from app.config import Settings
    from app.routes import drafts as droute

    engine.run_triage()
    monkeypatch.setattr(local_store, "_store", store)
    monkeypatch.setattr(config, "get_settings", lambda: Settings(email_provider="mock"))
    d = store.drafts.list()[0]
    resp = droute.push_to_gmail(d.id, droute.PushToGmailRequest(body="hello"))
    assert resp.synced is False
