"""EmailOS AI backend entrypoint.

Run locally:  uvicorn app.main:app --reload  (from the backend/ directory)

Safety: there is no email-send path anywhere in this service. The agent only
drafts, critiques, scores, and queues for human review.
"""
from __future__ import annotations

import os
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
    events,
    health,
    knowledge,
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
app.include_router(knowledge.router)
app.include_router(people.router)
app.include_router(profile.router)
app.include_router(meetings.router)
app.include_router(todos.router)
app.include_router(chat.router)
app.include_router(routines.router)
app.include_router(events.router)


@app.on_event("startup")
def _materialize_google_secrets() -> None:
    """On an ephemeral host there's no local secrets file. If the token /
    credentials are supplied as base64 env vars, write them to the configured
    paths so Gmail/Calendar work. (Bridge for Theo's own account until Stage 3
    stores per-user tokens in Supabase.)"""
    import base64
    from pathlib import Path

    for env_key, path_str in (
        ("GMAIL_TOKEN_B64", settings.gmail_token_path),
        ("GMAIL_CREDENTIALS_B64", settings.gmail_credentials_path),
    ):
        b64 = os.environ.get(env_key)
        path = Path(path_str)
        if b64 and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(b64))


@app.on_event("startup")
def _seed() -> None:
    # In Supabase mode the per-user store is built per request; nothing to
    # touch here. In local mode, load the legacy store so seed files validate.
    if settings.database_provider == "supabase":
        return
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
