"""Proactive scheduler: runs due routines and stores their results as nudges.

A single asyncio background task (started from the FastAPI lifespan) ticks
every 60s. A routine is due when it is enabled, the local time has passed its
HH:MM, and it hasn't run yet today (weekly: within the last 7 days).

Execution paths:
* kind="triage_brief" -> full triage run, nudge built from the Daily Brief.
* kind="ivy_task"     -> the prompt is handed to the Ivy supervisor.

Nudges are in-app data only. Nothing is sent anywhere.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from ..models import Nudge, Routine
from ..repositories import LocalStore, get_store

logger = logging.getLogger("emailos.scheduler")

TICK_SECONDS = 60


def is_due(routine: Routine, now: datetime) -> bool:
    """Pure due-check so tests can cover the edge cases."""
    if not routine.enabled:
        return False
    try:
        hour, minute = (int(p) for p in routine.time.split(":", 1))
    except ValueError:
        return False
    if (now.hour, now.minute) < (hour, minute):
        return False
    if routine.last_run_at is None:
        return True
    last = routine.last_run_at
    if last.tzinfo is not None:
        last = last.astimezone().replace(tzinfo=None)
    if routine.schedule == "weekly":
        return now - last >= timedelta(days=7)
    return last.date() < now.date()  # daily: at most once per calendar day


def run_routine(routine: Routine, store: LocalStore | None = None) -> Nudge:
    """Execute one routine synchronously and persist the resulting nudge."""
    store = store or get_store()

    if routine.kind == "triage_brief":
        from .workflow_engine import get_engine

        resp = get_engine().run_triage()
        brief = resp.brief
        items = [f"{t.category.value}: {t.summary}" for t in brief.need_reply[:5]]
        items += [f"To do: {a}" for a in brief.suggested_actions[:3]]
        nudge = Nudge(
            routine_id=routine.id,
            title=routine.title,
            body=brief.summary,
            items=items,
        )
    else:
        from .ivy_supervisor import get_supervisor

        # Nudges are ambient UI content — always English (the product default),
        # regardless of the language the routine was created in.
        prompt = f"{routine.prompt}\n\n(Reply in English.)"
        resp = get_supervisor().chat(prompt, conversation_id=f"routine-{routine.id}")
        nudge = Nudge(routine_id=routine.id, title=routine.title, body=resp.reply)

    # A fresh result replaces the routine's previous ones — the briefing is a
    # "current state" view, not a history feed.
    for old in store.nudges.list():
        if old.routine_id == routine.id:
            store.nudges.delete(old.id)
    store.nudges.add(nudge)
    routine.last_run_at = datetime.now().astimezone()
    store.routines.update(routine)
    return nudge


def run_due_routines(store: LocalStore | None = None, now: datetime | None = None) -> list[Nudge]:
    store = store or get_store()
    now = now or datetime.now()
    produced: list[Nudge] = []
    for routine in store.routines.list():
        if not is_due(routine, now):
            continue
        try:
            produced.append(run_routine(routine, store))
            logger.info("routine ran: %s", routine.title)
        except Exception:  # one broken routine must not kill the loop
            logger.exception("routine failed: %s", routine.title)
    return produced


def run_due_routines_all_users(now: datetime | None = None) -> None:
    """One scheduler tick across every known user.

    Setting the user context around each user's pass makes ``get_store()`` /
    ``get_engine()`` / ``get_supervisor()`` inside ``run_routine`` resolve to
    that user's isolated state — same mechanism as an authenticated request.
    """
    from ..context import iter_user_ids, run_as

    for user_id in iter_user_ids():
        try:
            with run_as(user_id) as ctx:
                run_due_routines(ctx.store, now)
        except Exception:  # one user's failure must not block the others
            logger.exception("scheduler pass failed for user %s", user_id)


async def scheduler_loop() -> None:
    """Background task: check every user's due routines once a minute."""
    while True:
        try:
            await asyncio.to_thread(run_due_routines_all_users)
        except Exception:  # pragma: no cover - defensive
            logger.exception("scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)
