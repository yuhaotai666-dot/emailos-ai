"""Shared Google OAuth for Gmail + Calendar.

Calendar stays read-only. Gmail uses ``gmail.compose`` so the app can write a
reply into the user's *Drafts* — it never sends. Sending is prevented at the
CODE level: there is no ``messages().send`` call anywhere in the codebase (only
``drafts().create/update``). Adding/removing a scope requires re-running
``python -m app.gmail_auth`` to re-consent.
"""
from __future__ import annotations

from pathlib import Path

from ..config import Settings

SCOPES = [
    # Create/read/update/delete drafts (write). Also technically permits send at
    # the OAuth level, but the code never calls send — drafts only.
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def load_credentials(settings: Settings):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(settings.gmail_token_path)
    if not token_path.exists():
        raise RuntimeError(
            f"Google token not found at {token_path}. Run: python -m app.gmail_auth"
        )
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
