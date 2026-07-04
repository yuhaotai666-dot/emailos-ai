"""The shared toolbox for Ivy and her specialists.

Every tool is a plain LangChain ``@tool``. Specialists are granted a *subset*
of these by name. There is deliberately no email-sending tool — the hard
"AI never sends" rule holds at the toolbox level: a capability that doesn't
exist can't be delegated.
"""
from __future__ import annotations

import json
from typing import Callable

import httpx
from langchain_core.tools import tool

from ..repositories import get_store

# ---------------------------------------------------------------- web search
@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results (title, snippet, url)."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # network/service hiccups shouldn't kill the agent
        return f"Search failed: {exc}"
    if not rows:
        return "No results."
    out = []
    for r in rows:
        out.append(f"- {r.get('title', '')}\n  {r.get('body', '')}\n  {r.get('href', '')}")
    return "\n".join(out)


# ------------------------------------------------------------------- weather
@tool
def get_weather(city: str) -> str:
    """Current weather and today's forecast for a city (no API key needed)."""
    try:
        with httpx.Client(timeout=10) as client:
            geo = client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1},
            ).json()
            results = geo.get("results") or []
            if not results:
                return f"City not found: {city}"
            place = results[0]
            wx = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 2,
                    "timezone": "auto",
                },
            ).json()
    except Exception as exc:
        return f"Weather lookup failed: {exc}"
    cur = wx.get("current", {})
    daily = wx.get("daily", {})
    return (
        f"{place['name']}, {place.get('country', '')}: now {cur.get('temperature_2m')}°C, "
        f"wind {cur.get('wind_speed_10m')} km/h, precipitation {cur.get('precipitation')} mm. "
        f"Today {daily.get('temperature_2m_min', ['?'])[0]}–{daily.get('temperature_2m_max', ['?'])[0]}°C, "
        f"rain chance {daily.get('precipitation_probability_max', ['?'])[0]}%. "
        f"Tomorrow {daily.get('temperature_2m_min', ['?', '?'])[1]}–{daily.get('temperature_2m_max', ['?', '?'])[1]}°C."
    )


# ------------------------------------------------- internal EmailOS AI data
@tool
def list_recent_emails() -> str:
    """List Theo's recent emails with their triage analysis (category, priority, needs_reply, summary)."""
    store = get_store()
    triage = {t.email_id: t for t in store.triage.list()}
    rows = []
    for e in sorted(store.emails.list(), key=lambda e: e.received_at, reverse=True):
        t = triage.get(e.id)
        tri = (
            f"{t.category.value}/{t.priority.value}, needs_reply={t.needs_reply}: {t.summary}"
            if t
            else "not triaged yet"
        )
        rows.append(f"- [{e.id}] {e.sender_name} — {e.subject} ({tri})")
    return "\n".join(rows) or "No emails."


@tool
def list_drafts() -> str:
    """List reply drafts waiting in the review queue (id, status, score, first line)."""
    store = get_store()
    rows = []
    for d in store.drafts.list():
        score = d.evaluation.overall_score if d.evaluation else "?"
        first = d.draft_body.strip().splitlines()[0] if d.draft_body.strip() else ""
        rows.append(f"- [{d.id}] {d.status.value}, score {score}, passes_constraints={d.constraints_passed}: {first}")
    return "\n".join(rows) or "No drafts yet — run triage first."


@tool
def list_meetings() -> str:
    """List upcoming meetings with prep notes and action items."""
    store = get_store()
    rows = []
    for m in sorted(store.meetings.list(), key=lambda m: m.starts_at):
        rows.append(
            f"- [{m.id}] {m.title} at {m.starts_at:%a %d %b %H:%M} with {', '.join(m.attendees)}. "
            f"Prep: {m.prep} Actions: {'; '.join(m.action_items)}"
        )
    return "\n".join(rows) or "No meetings."


@tool
def query_memory() -> str:
    """What the assistant knows about Theo's preferences, rules, and past lessons."""
    store = get_store()
    rows = [f"- Rule ({r.situation}): {r.preference}" for r in store.memory_rules.list()]
    rows += [f"- Avoid ({e.situation}): {e.lesson}" for e in store.error_cases.list()]
    rows += [f"- Works well ({s.situation}): {s.why_it_worked}" for s in store.success_patterns.list()]
    return "\n".join(rows) or "Memory is empty."


@tool
def get_daily_brief() -> str:
    """The latest daily brief: top priorities, who needs a reply, drafts ready."""
    store = get_store()
    briefs = store.briefs.list()
    if not briefs:
        return "No brief yet — triage hasn't run."
    b = sorted(briefs, key=lambda b: b.generated_at)[-1]
    top = "; ".join(t.summary for t in b.top_priority[:3])
    return f"{b.summary} Top priorities: {top}. Suggested: {'; '.join(b.suggested_actions[:3])}"


# ------------------------------------------------------------------ registry
# name -> tool object. Specialists get a subset of these by name.
TOOLBOX: dict[str, Callable] = {
    "web_search": web_search,
    "get_weather": get_weather,
    "list_recent_emails": list_recent_emails,
    "list_drafts": list_drafts,
    "list_meetings": list_meetings,
    "query_memory": query_memory,
    "get_daily_brief": get_daily_brief,
}


def tools_by_names(names: list[str]) -> list:
    """Resolve tool names to tool objects, silently dropping unknown names."""
    return [TOOLBOX[n] for n in names if n in TOOLBOX]


def toolbox_catalog() -> str:
    """Human-readable toolbox list, used in Ivy's system prompt."""
    return "\n".join(f"- {name}: {t.description}" for name, t in TOOLBOX.items())
