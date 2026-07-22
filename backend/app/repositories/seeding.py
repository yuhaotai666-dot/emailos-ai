"""Seed a fresh store from the committed seed files.

Store-agnostic: works on any object exposing the same collection attributes
(``LocalStore`` or ``SupabaseStore``), so a brand-new user — local or in
Postgres — starts from the same mock inbox + domain agents + Morning Briefing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

from ..models import Email, Meeting, Routine, SubAgent

T = TypeVar("T", bound=BaseModel)


def _read(path: Path, model: Type[T]) -> list[T]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [model.model_validate(r) for r in json.loads(text)]


def apply_seeds(store, seed_dir: Path) -> None:
    """Populate empty collections from the seed files under ``seed_dir``."""
    # Mock inbox (Gmail provider overwrites this on the first real fetch).
    if not store.emails.list():
        seeds = _read(seed_dir / "mock_emails.json", Email)
        if seeds:
            store.emails.replace_all(seeds)

    # Mock meetings (Calendar provider overwrites on first real fetch).
    if not store.meetings.list():
        seeds = _read(seed_dir / "mock_meetings.json", Meeting)
        if seeds:
            store.meetings.replace_all(seeds)

    # Default proactive routine.
    if not store.routines.list():
        store.routines.add(
            Routine(
                id="routine-morning-briefing",
                title="Morning Briefing",
                prompt="Triage the inbox and prepare today's brief and to-dos.",
                schedule="daily",
                time="08:30",
                kind="triage_brief",
                created_from="seed",
            )
        )

    # Built-in domain agents (email/meeting/reminder): never duplicated, but
    # their definition is re-synced from the seed on every load so code-side
    # updates reach existing users. Usage stats + id/created_at are preserved.
    by_name = {a.name: a for a in store.sub_agents.list()}
    for agent in _read(seed_dir / "system_agents.json", SubAgent):
        current = by_name.get(agent.name)
        if current is None:
            store.sub_agents.add(agent)
        else:
            synced = current.model_copy(
                update={
                    "description": agent.description,
                    "system_prompt": agent.system_prompt,
                    "tools": agent.tools,
                    "display_name": agent.display_name,
                }
            )
            if synced != current:
                store.sub_agents.update(synced)
