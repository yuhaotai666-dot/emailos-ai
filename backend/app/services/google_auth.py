"""Shared Google OAuth for Gmail + Calendar (both READ-ONLY).

One consent, one token file, two APIs. The scopes are strictly read-only:
the token structurally cannot send mail, nor create/modify calendar events.
"""
from __future__ import annotations

from pathlib import Path

from ..config import Settings

# Read-only everywhere. Adding a scope requires re-running `python -m app.gmail_auth`.
SCOPES = [
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
