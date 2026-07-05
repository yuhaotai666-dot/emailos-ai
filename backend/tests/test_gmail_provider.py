"""Gmail message mapping — pure functions, no network."""
from __future__ import annotations

from app.services.gmail_provider import SCOPES, _clean_snippet, _to_email


def _gmail_msg(**over):
    msg = {
        "id": "18c1a2b3",
        "threadId": "18c1a2b0",
        "internalDate": "1751700000000",  # 2025-07-05-ish, epoch millis
        "snippet": "Hi Theo &amp; team — quick question about the   payment timing…",
        "payload": {
            "headers": [
                {"name": "From", "value": "Maya Chen <maya@creators.example>"},
                {"name": "Subject", "value": "Payment for last video?"},
            ]
        },
    }
    msg.update(over)
    return msg


def test_to_email_maps_headers_and_snippet():
    e = _to_email(_gmail_msg())
    assert e.id == "18c1a2b3"
    assert e.thread_id == "18c1a2b0"
    assert e.sender_name == "Maya Chen"
    assert e.sender_email == "maya@creators.example"
    assert e.subject == "Payment for last video?"
    assert "&amp;" not in e.body_preview  # HTML unescaped
    assert "  " not in e.body_preview  # whitespace collapsed
    assert e.source == "gmail"
    assert e.received_at.year >= 2025


def test_to_email_handles_missing_fields():
    e = _to_email({"id": "x1", "payload": {"headers": []}, "snippet": ""})
    assert e.sender_email == "unknown@unknown"
    assert e.subject == "(no subject)"


def test_scope_is_readonly_only():
    # The whole integration is read-only by construction.
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_clean_snippet_truncates():
    assert len(_clean_snippet("x" * 1000)) == 300
