"""EmailOS AI backend entrypoint.

Run locally:  uvicorn app.main:app --reload  (from the backend/ directory)

Safety: there is no email-send path anywhere in this service. The agent only
drafts, critiques, scores, and queues for human review.
"""
from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .repositories import get_store
from .routes import (
    agent,
    chat,
    contacts,
    drafts,
    emails,
    health,
    meetings,
    memory,
    people,
    profile,
    routines,
    todos,
)

settings = get_settings()

app = FastAPI(
    title="EmailOS AI",
    version="0.1.0",
    description="Loop-based personal email triage & draft assistant. AI never sends automatically.",
)


def _cors_config(origins: list[str]) -> tuple[list[str], str | None]:
    """Split exact origins from wildcard patterns; wildcards go into a regex."""
    exact = [o for o in origins if "*" not in o]
    wild = [o for o in origins if "*" in o]
    regex = None
    if wild:
        parts = [re.escape(o).replace(r"\*", r"[^.]+") for o in wild]
        regex = "^(" + "|".join(parts) + ")$"
    return exact, regex


_exact, _regex = _cors_config(settings.origins_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_exact,
    allow_origin_regex=_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(agent.router)
app.include_router(drafts.router)
app.include_router(emails.router)
app.include_router(contacts.router)
app.include_router(memory.router)
app.include_router(people.router)
app.include_router(profile.router)
app.include_router(meetings.router)
app.include_router(todos.router)
app.include_router(chat.router)
app.include_router(routines.router)


@app.on_event("startup")
def _seed() -> None:
    # Touch the store so seed JSON files are validated/loaded at startup.
    store = get_store()
    _ = store.emails.list()
    _ = store.memory_rules.list()


@app.on_event("startup")
async def _start_scheduler() -> None:
    # Proactive engine: checks due routines once a minute. In-app results
    # only (nudges) — the scheduler cannot send anything.
    import asyncio

    from .services.scheduler import scheduler_loop

    app.state.scheduler_task = asyncio.create_task(scheduler_loop())


@app.on_event("shutdown")
async def _stop_scheduler() -> None:
    task = getattr(app.state, "scheduler_task", None)
    if task:
        task.cancel()


@app.get("/")
def root() -> dict:
    return {"service": "EmailOS AI", "docs": "/docs", "health": "/api/health"}
