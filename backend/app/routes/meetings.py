"""Meetings for the meetings + calendar pages.

Source is selected by CALENDAR_PROVIDER: "mock" serves the seeded meetings;
"google" refreshes from the real (read-only) Google Calendar on request,
throttled so repeated page loads don't hammer the API. The follow-up recap
in each meeting is draft content for review — nothing is sent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..models import Meeting
from ..repositories import get_store

logger = logging.getLogger("emailos.meetings")

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

_REFRESH_EVERY = timedelta(minutes=10)
_last_refresh: datetime | None = None


def _maybe_refresh() -> None:
    global _last_refresh
    settings = get_settings()
    if settings.calendar_provider != "google":
        return
    now = datetime.now()
    if _last_refresh is not None and now - _last_refresh < _REFRESH_EVERY:
        return
    try:
        from ..services.calendar_provider import GoogleCalendarProvider

        GoogleCalendarProvider().fetch_upcoming()
        _last_refresh = now
    except Exception:  # stale data beats a broken page
        logger.exception("calendar refresh failed; serving cached meetings")


@router.get("", response_model=list[Meeting])
def list_meetings() -> list[Meeting]:
    _maybe_refresh()
    meetings = get_store().meetings.list()
    return sorted(meetings, key=lambda m: m.starts_at)


@router.post("/refresh", response_model=list[Meeting])
def refresh_meetings() -> list[Meeting]:
    """Force a calendar re-fetch (ignores the throttle)."""
    global _last_refresh
    _last_refresh = None
    _maybe_refresh()
    return sorted(get_store().meetings.list(), key=lambda m: m.starts_at)


@router.get("/{meeting_id}", response_model=Meeting)
def get_meeting(meeting_id: str) -> Meeting:
    meeting = get_store().meetings.get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting
