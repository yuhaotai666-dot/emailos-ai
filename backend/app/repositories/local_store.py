"""JSON-file backed persistence.

Single-process, one-user MVP store. Each collection is one JSON file under
``app/data``. Writes are guarded by a process-level lock; this is intentionally
simple and documented as a limitation (not hardened for concurrency).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional, Type

from ..config import DATA_DIR
from ..models import (
    AgentRun,
    DailyBrief,
    Draft,
    Email,
    EmailEventLink,
    ErrorCase,
    EventTag,
    Meeting,
    MemoryRule,
    Nudge,
    Routine,
    SubAgent,
    SuccessPattern,
    TriageResult,
    UserProfile,
)
from .base import Collection, T
from .seeding import apply_seeds

_LOCK = threading.RLock()


class JsonCollection(Collection[T]):
    def __init__(self, path: Path, model: Type[T], id_field: str = "id"):
        self._path = path
        self._model = model
        self._id_field = id_field
        if not self._path.exists():
            self._write([])

    # ---- internal io ----
    def _read_raw(self) -> list[dict]:
        with _LOCK:
            if not self._path.exists():
                return []
            text = self._path.read_text(encoding="utf-8").strip()
            if not text:
                return []
            return json.loads(text)

    def _write(self, rows: list[dict]) -> None:
        with _LOCK:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(rows, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )

    def _key(self, row: dict) -> str:
        return str(row.get(self._id_field))

    # ---- Collection API ----
    def list(self) -> list[T]:
        return [self._model.model_validate(r) for r in self._read_raw()]

    def get(self, item_id: str) -> Optional[T]:
        for r in self._read_raw():
            if self._key(r) == str(item_id):
                return self._model.model_validate(r)
        return None

    def add(self, item: T) -> T:
        with _LOCK:
            rows = self._read_raw()
            rows.append(json.loads(item.model_dump_json()))
            self._write(rows)
        return item

    def update(self, item: T) -> T:
        target = str(getattr(item, self._id_field))
        with _LOCK:
            rows = self._read_raw()
            replaced = False
            for i, r in enumerate(rows):
                if self._key(r) == target:
                    rows[i] = json.loads(item.model_dump_json())
                    replaced = True
                    break
            if not replaced:
                rows.append(json.loads(item.model_dump_json()))
            self._write(rows)
        return item

    def delete(self, item_id: str) -> bool:
        with _LOCK:
            rows = self._read_raw()
            new_rows = [r for r in rows if self._key(r) != str(item_id)]
            self._write(new_rows)
            return len(new_rows) != len(rows)

    def replace_all(self, items: list[T]) -> None:
        self._write([json.loads(i.model_dump_json()) for i in items])


class LocalStore:
    """Container exposing one typed collection per domain entity.

    ``seed_dir`` is where the committed seed files (mock_emails.json,
    system_agents.json, …) live. It defaults to ``data_dir`` (the legacy
    single-user layout); per-user stores pass the shared ``DATA_DIR`` so every
    new user starts from the same seeds while their data stays isolated.
    """

    def __init__(self, data_dir: Path = DATA_DIR, seed_dir: Optional[Path] = None):
        self.data_dir = data_dir
        self.seed_dir = seed_dir or data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        # One JSON file per collection. Emails/meetings are the working sets the
        # Gmail/Calendar providers overwrite; the rest are runtime collections.
        self.emails = JsonCollection(data_dir / "emails.json", Email)
        self.memory_rules = JsonCollection(data_dir / "memory_rules.json", MemoryRule)
        self.error_cases = JsonCollection(data_dir / "error_library.json", ErrorCase)
        self.success_patterns = JsonCollection(
            data_dir / "success_patterns.json", SuccessPattern
        )
        self.meetings = JsonCollection(data_dir / "meetings.json", Meeting)
        self.drafts = JsonCollection(data_dir / "drafts.json", Draft)
        self.briefs = JsonCollection(data_dir / "briefs.json", DailyBrief)
        self.agent_runs = JsonCollection(data_dir / "agent_runs.json", AgentRun)
        self.triage = JsonCollection(
            data_dir / "triage.json", TriageResult, id_field="email_id"
        )
        self.profile = JsonCollection(data_dir / "profile.json", UserProfile)
        self.sub_agents = JsonCollection(data_dir / "sub_agents.json", SubAgent)
        # Manual event tags + their email assignments (user-managed, no LLM).
        self.events = JsonCollection(data_dir / "events.json", EventTag)
        self.email_events = JsonCollection(
            data_dir / "email_events.json", EmailEventLink, id_field="email_id"
        )
        # Proactive engine collections.
        self.routines = JsonCollection(data_dir / "routines.json", Routine)
        self.nudges = JsonCollection(data_dir / "nudges.json", Nudge)

        # Seed empty collections + re-sync the built-in domain agents.
        apply_seeds(self, self.seed_dir)


_store: Optional[LocalStore] = None


def _legacy_store() -> LocalStore:
    """The process-wide single-user store (backend/app/data)."""
    global _store
    if _store is None:
        _store = LocalStore()
    return _store


def get_store() -> LocalStore:
    """The store for the *current request's user* when a request context is
    active (multi-user mode), else the legacy single-user store (tests, CLI,
    auth-disabled local dev). Existing call sites need no changes."""
    from ..context import current_context  # runtime import avoids a cycle

    ctx = current_context()
    if ctx is not None:
        return ctx.store
    return _legacy_store()
