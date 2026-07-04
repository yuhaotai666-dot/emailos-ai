"""Meetings for the meetings + calendar pages.

Backed by the ``meetings`` collection (mock seed now; a Google Calendar
provider can replace the source later at the same seam). The follow-up recap
in each meeting is draft content for review — nothing is sent.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import Meeting
from ..repositories import get_store

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


@router.get("", response_model=list[Meeting])
def list_meetings() -> list[Meeting]:
    meetings = get_store().meetings.list()
    return sorted(meetings, key=lambda m: m.starts_at)


@router.get("/{meeting_id}", response_model=Meeting)
def get_meeting(meeting_id: str) -> Meeting:
    meeting = get_store().meetings.get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting
