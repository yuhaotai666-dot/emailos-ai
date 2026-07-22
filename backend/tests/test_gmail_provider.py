"""Gmail message mapping — pure functions, no network."""
from __future__ import annotations

import base64

from app.services.gmail_provider import SCOPES, _clean_snippet, _to_email


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


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
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("Hi Theo, when will the payment for my last video be sent?")},
        },
    }
    msg.update(over)
    return msg


def test_to_email_maps_headers_and_body():
    e = _to_email(_gmail_msg())
    assert e.id == "18c1a2b3"
    assert e.thread_id == "18c1a2b0"
    assert e.sender_name == "Maya Chen"
    assert e.sender_email == "maya@creators.example"
    assert e.subject == "Payment for last video?"
    # Body now comes from the actual message payload, not the truncated snippet.
    assert "when will the payment" in e.body_preview
    assert e.source == "gmail"
    assert e.received_at.year >= 2025


def test_body_prefers_plain_text_part_and_caps_length():
    long_body = "word " * 1000  # 5000 chars
    msg = _gmail_msg(
        payload={
            "headers": [{"name": "From", "value": "a@b.com"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>ignored html</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64(long_body)}},
            ],
        }
    )
    e = _to_email(msg)
    assert e.body_preview.startswith("word word")
    assert "ignored html" not in e.body_preview  # plain-text part wins
    assert len(e.body_preview) <= 2000  # capped


def test_body_strips_quoted_reply_chain_keeps_paragraphs():
    raw = (
        "Hi Theo,\n\n"
        "Here is my new reply with an actual point to make.\n\n"
        "Best,\nDavid\n"
        "On Wed, Jul 15, 2026 at 8:11 PM Theo Tai <theo@superintern.ai> wrote:\n"
        "> Hi David,\n> Thanks for explaining.\n> Would you consider performance-based?\n"
    )
    msg = _gmail_msg(
        payload={
            "headers": [{"name": "From", "value": "David <david@x.com>"}],
            "mimeType": "text/plain",
            "body": {"data": _b64(raw)},
        }
    )
    e = _to_email(msg)
    assert "new reply with an actual point" in e.body_preview
    assert "Thanks for explaining" not in e.body_preview  # quoted history dropped
    assert "wrote:" not in e.body_preview  # attribution line dropped
    assert "David" in e.body_preview  # sender's own signature kept
    assert "\n" in e.body_preview  # paragraph breaks preserved


def test_body_strips_leading_quote_and_wrapped_attribution():
    # Attribution wrapped across two lines, quote block right after.
    raw = (
        "Quick yes from me.\n\n"
        "On Fri, Jul 18, 2026 at 9:00 AM Maya Chen <maya@creators.example>\n"
        "wrote:\n"
        "> Are we still on for Tuesday?\n"
    )
    msg = _gmail_msg(
        payload={
            "headers": [{"name": "From", "value": "Maya <maya@creators.example>"}],
            "mimeType": "text/plain",
            "body": {"data": _b64(raw)},
        }
    )
    e = _to_email(msg)
    assert e.body_preview.startswith("Quick yes from me")
    assert "Tuesday" not in e.body_preview
    assert "On Fri" not in e.body_preview  # wrapped attribution head removed


def test_body_falls_back_to_snippet_when_no_body_data():
    # Metadata-only resource (no decodable body) -> snippet, HTML-unescaped.
    e = _to_email({"id": "x1", "payload": {"headers": []}, "snippet": "Hi Theo &amp; team"})
    assert e.body_preview == "Hi Theo & team"


def test_from_me_set_from_sent_label():
    # A message the user sent carries the Gmail SENT label — flagged regardless
    # of which address it was sent from.
    sent = _to_email(_gmail_msg(labelIds=["SENT", "INBOX"]))
    assert sent.from_me is True
    received = _to_email(_gmail_msg(labelIds=["INBOX"]))
    assert received.from_me is False
    assert _to_email(_gmail_msg()).from_me is False  # no labels -> not ours


def test_to_email_handles_missing_fields():
    e = _to_email({"id": "x1", "payload": {"headers": []}, "snippet": ""})
    assert e.sender_email == "unknown@unknown"
    assert e.subject == "(no subject)"


def test_scopes_are_drafts_only_no_send():
    # Calendar stays read-only; Gmail is limited to compose (drafts) — NOT the
    # broader gmail.modify, and NOT a send-only scope. The no-send guarantee is
    # enforced in code (see test_no_send.py), since compose can technically send.
    assert SCOPES
    assert "https://www.googleapis.com/auth/calendar.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/gmail.compose" in SCOPES
    assert not any(s.endswith("gmail.modify") or s.endswith("gmail.send") for s in SCOPES)


def test_clean_snippet_truncates():
    assert len(_clean_snippet("x" * 1000)) == 300
