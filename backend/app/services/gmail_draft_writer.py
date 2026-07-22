"""Write a reply into the user's Gmail *Drafts* — never send.

This is the ONLY module that talks to Gmail with write access, and it only
ever calls ``drafts().create`` / ``drafts().update``. There is deliberately no
``messages().send`` call anywhere in the codebase: the "AI never sends" rule is
enforced in code (the ``gmail.compose`` scope would technically permit send,
but we don't). The draft is placed in the original thread so the user can open
Gmail, review, and hit send themselves.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Optional

from ..config import Settings, get_settings
from ..models import Draft, Email
from ..repositories import LocalStore, get_store
from .google_auth import load_credentials


class GmailDraftWriter:
    def __init__(self, store: Optional[LocalStore] = None, settings: Optional[Settings] = None):
        self.store = store or get_store()
        self.settings = settings or get_settings()

    def _svc(self):
        from googleapiclient.discovery import build

        creds = load_credentials(self.settings)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _build_raw(self, email: Email, body: str, subject: str) -> str:
        msg = EmailMessage()
        msg["To"] = f"{email.sender_name} <{email.sender_email}>"
        msg["Subject"] = subject
        # Thread it in the recipient's client too, when we know the Message-ID.
        original_mid = (email.metadata or {}).get("message_id")
        if original_mid:
            msg["In-Reply-To"] = original_mid
            msg["References"] = original_mid
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    def sync(self, draft: Draft, body: str, subject: Optional[str] = None) -> Draft:
        """Create or update the Gmail draft for this reply. Returns the updated
        Draft (with ``gmail_draft_id`` set). Never sends."""
        email = self.store.emails.get(draft.email_id)
        if email is None:
            raise ValueError("original email not found for this draft")
        subj = subject or draft.subject_suggestion or f"Re: {email.subject}"
        raw = self._build_raw(email, body, subj)
        message = {"raw": raw, "threadId": email.thread_id}

        svc = self._svc()
        if draft.gmail_draft_id:
            svc.users().drafts().update(
                userId="me", id=draft.gmail_draft_id, body={"message": message}
            ).execute()
        else:
            created = svc.users().drafts().create(
                userId="me", body={"message": message}
            ).execute()
            draft.gmail_draft_id = created.get("id")

        draft.draft_body = body
        if subject is not None:
            draft.subject_suggestion = subject
        return draft
