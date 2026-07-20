"""Gmail email source (READ-ONLY).

Implements the ``EmailProvider`` seam against the real Gmail API.

Privacy & safety, by construction:
* OAuth scope is ``gmail.readonly`` — this token *cannot* send, modify, or
  delete mail even if the code tried.
* ``body_preview`` holds the message body *truncated to 2000 characters*
  (plain text, HTML stripped). Bodies beyond the cap, and attachments, are
  never stored. (Theo relaxed the original snippet-only rule on 2026-07-19:
  half-sentence snippets crippled both the inbox display and the quality of
  triage/drafting.)

Thread context: ``fetch_recent`` also pulls each touched thread's full history
(via ``threads().get``, still metadata+snippet only) so drafting has visibility
into prior messages — including ones Theo already sent. That backstory is
stashed in the store for ``ContextRetriever`` to read, but is never part of
this method's *return value*, so old or self-sent messages are never
independently re-classified or re-drafted by ``run_triage``.

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
        # messages().list() already returns {id, threadId} per row — no extra
        # call needed just to learn which thread a message belongs to.
        rows = listing.get("messages", [])
        ids = [r["id"] for r in rows]
        thread_ids = sorted({r["threadId"] for r in rows if r.get("threadId")})

        # Per-message / per-thread fetches are independent HTTP calls — do them
        # concurrently. Each worker builds its own service client (httplib2 is
        # not thread-safe).
        creds = _load_credentials(self.settings)

        def _fetch(msg_id: str) -> Email:
            from googleapiclient.discovery import build

            svc_local = build("gmail", "v1", credentials=creds, cache_discovery=False)
            # format=full so the body text is available; _to_email truncates
            # it to the 2000-char cap before anything is stored.
            msg = (
                svc_local.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
            return _to_email(msg)

        def _fetch_thread(thread_id: str) -> list[Email]:
            from googleapiclient.discovery import build

            svc_local = build("gmail", "v1", credentials=creds, cache_discovery=False)
            # Returns every message in the thread regardless of gmail_query —
            # this is what gives drafting access to the full conversation
            # backstory. Bodies are capped by _to_email like everything else.
            thread = (
                svc_local.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            return [_to_email(m) for m in thread.get("messages", [])]

        if len(ids) <= 1:
            primary = [_fetch(i) for i in ids]
        else:
            with ThreadPoolExecutor(max_workers=8) as pool:
                primary = list(pool.map(_fetch, ids))

        if len(thread_ids) <= 1:
            thread_batches = [_fetch_thread(t) for t in thread_ids]
        else:
            with ThreadPoolExecutor(max_workers=8) as pool:
                thread_batches = list(pool.map(_fetch_thread, thread_ids))
        siblings = [e for batch in thread_batches for e in batch]

        # Store the full superset (primary + thread backstory, deduped by id)
        # so ContextRetriever can see prior messages in a thread. run_triage
        # only ever processes the RETURN VALUE below (`primary`), never
        # everything in the store — this keeps old/self-sent thread messages
        # from being re-classified or re-drafted on every run.
        by_id = {e.id: e for e in siblings}
        by_id.update({e.id: e for e in primary})
        self.store.emails.replace_all(list(by_id.values()))
        return primary


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

    # Prefer the actual body (format=full); fall back to Gmail's snippet when
    # the body can't be decoded (e.g. a metadata-only message resource).
    body = _extract_body(msg.get("payload", {})) or _clean_snippet(msg.get("snippet", ""))

    return Email(
        id=msg["id"],
        thread_id=msg.get("threadId", msg["id"]),
        sender_name=sender_name,
        sender_email=sender_email or "unknown@unknown",
        subject=headers.get("subject", "(no subject)"),
        body_preview=body,
        received_at=received,
        source="gmail",
        from_me="SENT" in (msg.get("labelIds") or []),
    )


# Body text is capped at 2000 chars before storage — enough for display and
# for triage/drafting context, without persisting entire threads verbatim.
_BODY_CAP = 2000


def _clean_snippet(snippet: str) -> str:
    # Gmail HTML-escapes snippets (&amp; etc.) — undo that, collapse whitespace.
    import html

    return re.sub(r"\s+", " ", html.unescape(snippet)).strip()[:300]


def _extract_body(payload: dict) -> str:
    """Best plain-text body from a Gmail message payload, capped and cleaned.

    Walks the MIME tree preferring ``text/plain``; falls back to ``text/html``.
    Returns "" if nothing decodable is found (caller then falls back to the
    snippet)."""
    plain = _find_part(payload, "text/plain")
    if plain:
        return _clean_body(plain)
    html_part = _find_part(payload, "text/html")
    if html_part:
        # Turn block-level tags into line breaks before dropping the rest, so
        # paragraph structure survives for _clean_body / quote-stripping.
        t = re.sub(r"(?i)<br\s*/?>", "\n", html_part)
        t = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        return _clean_body(t)
    return ""


def _find_part(part: dict, mime: str) -> str:
    """Depth-first search of the MIME tree for the first part of ``mime`` type
    that carries decodable body data."""
    if part.get("mimeType") == mime:
        data = part.get("body", {}).get("data")
        if data:
            return _decode_b64url(data)
    for child in part.get("parts", []) or []:
        found = _find_part(child, mime)
        if found:
            return found
    return ""


def _decode_b64url(data: str) -> str:
    import base64

    try:
        return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


# Start of a quoted reply chain — everything from here down is the prior
# message(s) the sender's client quoted back, not their new content.
_ATTRIBUTION_RE = re.compile(r"(?i)^On\b.*\bwrote:")


def _strip_quoted(text: str) -> str:
    """Keep only the sender's *new* content, dropping the quoted reply chain.

    Gmail's plain-text body inlines the whole prior conversation as quoted
    (``>``) lines under an attribution line ("On <date>, <name> wrote:") — the
    same history we already have as separate thread messages. Cutting it here
    means each message shows just its own reply (like Gmail's collapsed view)
    and stops the inbox from looking like one giant blob.
    """
    lines = text.split("\n")
    cut = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if (
            s.startswith(">")
            or _ATTRIBUTION_RE.match(s)
            or s.startswith("-----Original Message-----")
            or re.match(r"^_{5,}$", s)
        ):
            cut = i
            break
    kept = lines[:cut]
    # If the attribution line wrapped, its "On <date>… <email>" head and a lone
    # "wrote:" tail may sit just above the quote block — drop those remnants.
    while kept and (
        not kept[-1].strip()
        or re.match(r"(?i)^On\b.*(@|\d{4})", kept[-1].strip())
        or re.fullmatch(r"(?i)\s*wrote:\s*", kept[-1])
    ):
        kept.pop()
    return "\n".join(kept).strip()


def _clean_body(text: str) -> str:
    import html

    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_quoted(text)
    # Collapse spaces/tabs but keep line breaks; squeeze 3+ blank lines to one.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:_BODY_CAP]
