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
def search_email(query: str, max_results: int = 8) -> str:
    """Search the user's WHOLE email history (not just recent inbox) for messages
    matching a query — use this to answer questions about what was said or agreed
    in past emails (e.g. a price negotiated weeks ago).

    Use Gmail search syntax: sender (``from:maya``), keywords (``rate price``),
    dates (``after:2026/03/01``). Combine them, e.g. ``from:maya rate OR price``.
    Follow up with ``read_thread`` to see the full back-and-forth. Only state a
    figure/date you actually find here — never guess.

    Args:
        query: Gmail search query.
        max_results: max messages to return (default 8).
    """
    from ..config import get_settings

    settings = get_settings()
    store = get_store()
    if settings.email_provider == "gmail":
        try:
            from .gmail_provider import GmailProvider

            emails = GmailProvider(store, settings).search(query, max_results)
        except Exception as exc:
            return f"Search failed: {type(exc).__name__}"
    else:
        emails = _local_search(store, query, max_results)
    if not emails:
        return f"No emails found for '{query}'."
    out = []
    for e in emails:
        out.append(
            f"- [{e.id}] [thread:{e.thread_id}] {e.sender_name} <{e.sender_email}> "
            f"— {e.subject} ({e.received_at:%b %d, %Y})\n  {e.body_preview[:500]}"
        )
    return "\n".join(out)


@tool
def read_thread(thread_ref: str) -> str:
    """Read every message in an email thread, oldest first — the full
    back-and-forth. Use after ``search_email`` to see what was FINALLY agreed
    (not just an intermediate offer).

    Args:
        thread_ref: a thread id (``thread:...`` value from search_email), an
            email id, or a sender name / subject keyword to fuzzy-match.
    """
    from ..config import get_settings

    settings = get_settings()
    store = get_store()
    thread_id = _resolve_thread_id(store, thread_ref)
    if thread_id is None:
        return f"No thread matches '{thread_ref}'."

    if settings.email_provider == "gmail":
        try:
            from .gmail_provider import GmailProvider

            msgs = GmailProvider(store, settings).get_thread(thread_id)
        except Exception as exc:
            return f"Read failed: {type(exc).__name__}"
    else:
        msgs = sorted(
            (e for e in store.emails.list() if e.thread_id == thread_id),
            key=lambda e: e.received_at,
        )
    if not msgs:
        return f"Thread '{thread_ref}' has no messages."
    profile = store.profile.get("profile")
    me = (profile.email if profile else "").lower()
    out = []
    for e in msgs:
        who = "You" if (e.from_me or e.sender_email.lower() == me) else e.sender_name
        out.append(f"{e.received_at:%b %d, %Y} — {who}:\n{e.body_preview}")
    return "\n\n".join(out)


def _local_search(store, query: str, max_results: int):
    """Offline/mock fallback: keyword-match over the local store."""
    terms = [t for t in query.lower().replace("from:", " ").split() if t not in ("or", "and")]
    scored = []
    for e in store.emails.list():
        blob = f"{e.sender_name} {e.sender_email} {e.subject} {e.body_preview}".lower()
        hits = sum(1 for t in terms if t in blob)
        if hits:
            scored.append((hits, e))
    scored.sort(key=lambda x: (x[0], x[1].received_at), reverse=True)
    return [e for _, e in scored[:max_results]]


def _resolve_thread_id(store, ref: str):
    ref = ref.strip()
    if ref.startswith("thread:"):
        return ref.split(":", 1)[1]
    # a thread id present in the store?
    for e in store.emails.list():
        if e.thread_id == ref:
            return ref
    # an email id, or fuzzy sender/subject match -> its thread
    email = _find_email(store, ref)
    return email.thread_id if email else None


@tool
def query_memory() -> str:
    """What the assistant knows about Theo's preferences, rules, and past lessons."""
    store = get_store()
    rows = [f"- Rule ({r.situation}): {r.preference}" for r in store.memory_rules.list()]
    rows += [f"- Avoid ({e.situation}): {e.lesson}" for e in store.error_cases.list()]
    rows += [f"- Works well ({s.situation}): {s.why_it_worked}" for s in store.success_patterns.list()]
    return "\n".join(rows) or "Memory is empty."


@tool
def create_reply_draft(email_ref: str, instruction: str = "") -> str:
    """Draft a reply to one email and put it in Theo's review queue.

    Runs the full quality loop (draft -> critique -> score -> constraint check
    -> rewrite if needed). The draft is queued as pending_review — it is NOT
    sent; Theo reviews and approves everything.

    Args:
        email_ref: the email's id, OR the sender's name, OR a subject keyword
            (fuzzy matched, e.g. "maya" or "payment").
        instruction: optional extra guidance, e.g. "keep it to three sentences".
    """
    from ..models import Critique, DraftStatus
    from .workflow_engine import WorkflowEngine

    store = get_store()
    email = _find_email(store, email_ref)
    if email is None:
        options = "; ".join(
            f"[{e.id}] {e.sender_name} — {e.subject}" for e in store.emails.list()[:10]
        )
        return f"No email matches '{email_ref}'. Available: {options}"
    email_id = email.id

    engine = WorkflowEngine(store=store)
    triage = store.triage.get(email_id) or engine.classifier.classify(email)
    store.triage.update(triage)

    existing = next((d for d in store.drafts.list() if d.email_id == email_id), None)
    if existing and instruction:
        # Refine the existing draft with the extra guidance.
        context = engine.retriever.retrieve(email, triage)
        draft = engine.generator.rewrite(
            email, triage, context, existing, Critique(rewrite_instructions=instruction), None
        )
        draft.id = existing.id  # keep the queue reference stable
        draft.status = DraftStatus.PENDING_REVIEW
        engine.recompute(draft)
        store.drafts.update(draft)
    else:
        result = engine.graph.invoke({"email": email, "max_retries": engine.settings.max_draft_retries})
        draft = result.get("best")
        if draft is None:
            return "This email doesn't need a reply according to triage."
        if instruction:
            context = engine.retriever.retrieve(email, triage)
            draft = engine.generator.rewrite(
                email, triage, context, draft, Critique(rewrite_instructions=instruction), None
            )
            draft.status = DraftStatus.PENDING_REVIEW
            engine.recompute(draft)
        if existing:
            draft.id = existing.id
            store.drafts.update(draft)
        else:
            store.drafts.add(draft)

    score = draft.evaluation.overall_score if draft.evaluation else "?"
    return (
        f"Draft queued for review (id {draft.id}, score {score}, "
        f"constraints_passed={draft.constraints_passed}). It will NOT be sent "
        f"until Theo approves. Draft:\n{draft.draft_body}"
    )


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


def _find_email(store, ref: str):
    """Resolve an email by id, sender name, sender address, or subject keyword."""
    ref_low = ref.strip().lower()
    emails = store.emails.list()
    for e in emails:
        if e.id == ref or e.id == ref_low:
            return e
    # Newest first so "maya" hits her latest email.
    emails.sort(key=lambda e: e.received_at, reverse=True)
    for e in emails:
        blob = f"{e.sender_name} {e.sender_email}".lower()
        if ref_low in blob:
            return e
    for e in emails:
        if ref_low in e.subject.lower() or ref_low in e.body_preview.lower():
            return e
    return None


# ------------------------------------------------------------------ registry
# name -> tool object. Specialists get a subset of these by name.
TOOLBOX: dict[str, Callable] = {
    "web_search": web_search,
    "get_weather": get_weather,
    "list_recent_emails": list_recent_emails,
    "search_email": search_email,
    "read_thread": read_thread,
    "list_drafts": list_drafts,
    "list_meetings": list_meetings,
    "query_memory": query_memory,
    "get_daily_brief": get_daily_brief,
    "create_reply_draft": create_reply_draft,
}


def tools_by_names(names: list[str]) -> list:
    """Resolve tool names to tool objects, silently dropping unknown names."""
    return [TOOLBOX[n] for n in names if n in TOOLBOX]


def toolbox_catalog() -> str:
    """Human-readable toolbox list, used in Ivy's system prompt."""
    return "\n".join(f"- {name}: {t.description}" for name, t in TOOLBOX.items())
