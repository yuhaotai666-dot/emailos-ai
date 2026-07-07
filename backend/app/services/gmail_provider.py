"""Gmail email source (READ-ONLY).

Implements the ``EmailProvider`` seam against the real Gmail API.

Privacy & safety, by construction:
* OAuth scope is ``gmail.readonly`` — this token *cannot* send, modify, or
  delete mail even if the code tried.
* We store only headers + Gmail's short snippet as ``body_preview`` — full
  bodies are never fetched, stored, or logged.

Auth: run ``python -m app.gmail_auth`` once; it opens the browser, you approve,
and a refresh token is saved to ``secrets/gmail_token.json`` (gitignored).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parseaddr

from ..config import Settings, get_settings
from ..models import Email
from ..repositories import LocalStore, get_store
from .google_auth import SCOPES, load_credentials as _load_credentials  # shared Google OAuth
from .mock_email_provider import EmailProvider

__all__ = ["GmailProvider", "SCOPES"]


class GmailProvider(EmailProvider):
    """Fetches recent inbox messages and mirrors them into the local store so
    the rest of the system (triage, /api/emails, people, todos) is unchanged."""

    def __init__(self, store: LocalStore | None = None, settings: Settings | None = None):
        self.store = store or get_store()
        self.settings = settings or get_settings()
        self._service = None

    def _svc(self):
        if self._service is None:
            from googleapiclient.discovery import build

            creds = _load_credentials(self.settings)
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def fetch_recent(self, limit: int = 50) -> list[Email]:
        svc = self._svc()
        listing = (
            svc.users()
            .messages()
            .list(userId="me", labelIds=["INBOX"], q=self.settings.gmail_query, maxResults=limit)
            .execute()
        )
        ids = [m["id"] for m in listing.get("messages", [])]

        # Per-message fetches are independent HTTP calls — do them concurrently.
        # Each thread builds its own service client (httplib2 is not thread-safe).
        creds = _load_credentials(self.settings)

        def _fetch(msg_id: str) -> Email:
            from googleapiclient.discovery import build

            svc_local = build("gmail", "v1", credentials=creds, cache_discovery=False)
            # format=metadata returns headers + snippet only — never the body.
            msg = (
                svc_local.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            return _to_email(msg)

        if len(ids) <= 1:
            emails = [_fetch(i) for i in ids]
        else:
            with ThreadPoolExecutor(max_workers=8) as pool:
                emails = list(pool.map(_fetch, ids))

        # Mirror into the store so joins (triage/drafts/people) keep working.
        self.store.emails.replace_all(emails)
        return emails


def _to_email(msg: dict) -> Email:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    if not sender_name:
        sender_name = sender_email.split("@")[0] if sender_email else "Unknown"

    # internalDate is epoch millis as a string.
    try:
        received = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        received = datetime.now(timezone.utc)

    return Email(
        id=msg["id"],
        thread_id=msg.get("threadId", msg["id"]),
        sender_name=sender_name,
        sender_email=sender_email or "unknown@unknown",
        subject=headers.get("subject", "(no subject)"),
        body_preview=_clean_snippet(msg.get("snippet", "")),
        received_at=received,
        source="gmail",
    )


def _clean_snippet(snippet: str) -> str:
    # Gmail HTML-escapes snippets (&amp; etc.) — undo that, collapse whitespace.
    import html

    return re.sub(r"\s+", " ", html.unescape(snippet)).strip()[:300]
