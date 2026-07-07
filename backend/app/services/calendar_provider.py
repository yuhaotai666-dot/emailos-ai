"""Google Calendar source (READ-ONLY).

Pulls upcoming events from the primary calendar and mirrors them into the
meetings store, so the meetings/calendar pages, todos, and the meeting-agent
all see real events. Uses the same OAuth token as Gmail (calendar.readonly —
the token cannot create, modify, or delete events).

For events we haven't seen before, a one-time prep briefing (prep notes,
questions, action items) is generated: by the LLM when a key is configured,
by a deterministic template otherwise. Existing prep is never regenerated,
so cost stays at one call per new event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..models import Meeting
from ..repositories import LocalStore, get_store
from ._chains import SYS_USER_PROMPT
from ._theo import THEO_CONTEXT
from .google_auth import load_credentials
from .llm_client import LLMClient, get_llm_client


class _PrepOut(BaseModel):
    prep: str = ""
    questions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


_PREP_SYSTEM = f"""{THEO_CONTEXT}

You prepare Theo for an upcoming meeting. Given the event title and attendees,
produce: prep (2-3 sentences on what to review/bring), 2-3 questions worth
asking, and 1-3 concrete action items. Be specific to the event, no filler."""


class GoogleCalendarProvider:
    def __init__(self, store: Optional[LocalStore] = None, settings: Optional[Settings] = None,
                 llm: Optional[LLMClient] = None):
        self.store = store or get_store()
        self.settings = settings or get_settings()
        self.llm = llm or get_llm_client()
        self._service = None

    def _svc(self):
        if self._service is None:
            from googleapiclient.discovery import build

            creds = load_credentials(self.settings)
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def fetch_upcoming(self) -> list[Meeting]:
        """Fetch upcoming events and mirror them into the meetings store."""
        now = datetime.now(timezone.utc)
        result = (
            self._svc()
            .events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=(now + timedelta(days=self.settings.calendar_days_ahead)).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=25,
            )
            .execute()
        )

        # Keep prep/recaps already generated for events we've seen before.
        previous = {m.id: m for m in self.store.meetings.list()}

        meetings: list[Meeting] = []
        for event in result.get("items", []):
            meeting = _to_meeting(event)
            if meeting is None:
                continue
            old = previous.get(meeting.id)
            if old is not None and old.prep:
                meeting.prep = old.prep
                meeting.questions = old.questions
                meeting.action_items = old.action_items
                meeting.follow_up = old.follow_up
            else:
                self._generate_prep(meeting)
            meetings.append(meeting)

        self.store.meetings.replace_all(meetings)
        return meetings

    def _generate_prep(self, meeting: Meeting) -> None:
        if self.llm.mock or self.llm.model is None:
            meeting.prep = (
                f"Review recent emails with {', '.join(meeting.attendees[:2]) or 'the attendees'} "
                f"before '{meeting.title}'."
            )
            meeting.questions = ["What outcome does each side want?", "What are the next steps?"]
            meeting.action_items = ["Agree on next steps and owners"]
            return
        user = (
            f"Event: {meeting.title}\n"
            f"When: {meeting.starts_at:%A %d %b %H:%M}\n"
            f"Attendees: {', '.join(meeting.attendees) or 'unknown'}"
        )
        chain = SYS_USER_PROMPT | self.llm.structured(_PrepOut)
        try:
            out: _PrepOut = chain.invoke({"system": _PREP_SYSTEM, "user": user})
            meeting.prep = out.prep
            meeting.questions = out.questions
            meeting.action_items = out.action_items
        except Exception:  # prep is nice-to-have; never fail the fetch over it
            meeting.prep = f"Prep for '{meeting.title}'."


def _to_meeting(event: dict) -> Optional[Meeting]:
    """Map a Calendar API event to our Meeting model. Returns None for all-day
    events without a concrete start time we can schedule around."""
    start = event.get("start", {})
    when = start.get("dateTime") or start.get("date")
    if not when:
        return None
    try:
        starts_at = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if starts_at.tzinfo is None:  # all-day date -> midnight local
            starts_at = starts_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    attendees = [
        a.get("displayName") or a.get("email", "").split("@")[0]
        for a in event.get("attendees", [])
        if not a.get("resource")
    ]

    return Meeting(
        id=event.get("id", ""),
        title=event.get("summary", "(no title)"),
        starts_at=starts_at,
        attendees=[a for a in attendees if a],
        source="google",
    )
