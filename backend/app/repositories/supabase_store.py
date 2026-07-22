"""Postgres-backed store (Theo-owned Supabase project, separate from auth).

Implements the same ``Collection`` interface as ``LocalStore`` over a single
generic ``collections(user_id, collection, item_id, data jsonb)`` table, so the
whole service layer is unchanged. Selected when ``database_provider ==
"supabase"``; per-user isolation is by ``user_id`` (the JWT ``sub``). The
backend uses the service_role key (bypasses RLS) and filters by user_id in code.
"""
from __future__ import annotations

from typing import Optional, Type

from ..config import Settings, get_settings
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

_TABLE = "collections"
_client = None


def _get_client(settings: Settings):
    """One Supabase client per process (service_role)."""
    global _client
    if _client is None:
        from supabase import create_client

        _client = create_client(settings.supabase_db_url, settings.supabase_service_role_key)
    return _client


class SupabaseCollection(Collection[T]):
    """CRUD over the shared ``collections`` table for one (user, collection)."""

    def __init__(self, client, user_id: str, name: str, model: Type[T], id_field: str = "id"):
        self._c = client
        self._user = user_id
        self._name = name
        self._model = model
        self._id_field = id_field

    def _rows(self):
        return self._c.table(_TABLE).select("data").eq("user_id", self._user).eq(
            "collection", self._name
        )

    def _key(self, item: T) -> str:
        return str(getattr(item, self._id_field))

    def _row(self, item: T) -> dict:
        return {
            "user_id": self._user,
            "collection": self._name,
            "item_id": self._key(item),
            "data": item.model_dump(mode="json"),
        }

    # ---- Collection API ----
    def list(self) -> list[T]:
        res = self._rows().execute()
        return [self._model.model_validate(r["data"]) for r in (res.data or [])]

    def get(self, item_id: str) -> Optional[T]:
        res = (
            self._c.table(_TABLE)
            .select("data")
            .eq("user_id", self._user)
            .eq("collection", self._name)
            .eq("item_id", str(item_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return self._model.model_validate(rows[0]["data"]) if rows else None

    def add(self, item: T) -> T:
        self._c.table(_TABLE).upsert(self._row(item)).execute()
        return item

    def update(self, item: T) -> T:
        self._c.table(_TABLE).upsert(self._row(item)).execute()
        return item

    def delete(self, item_id: str) -> bool:
        res = (
            self._c.table(_TABLE)
            .delete()
            .eq("user_id", self._user)
            .eq("collection", self._name)
            .eq("item_id", str(item_id))
            .execute()
        )
        return bool(res.data)

    def replace_all(self, items: list[T]) -> None:
        self._c.table(_TABLE).delete().eq("user_id", self._user).eq(
            "collection", self._name
        ).execute()
        if items:
            self._c.table(_TABLE).upsert([self._row(i) for i in items]).execute()


class SupabaseStore:
    """Same collection surface as LocalStore, backed by Postgres per user."""

    def __init__(self, user_id: str, settings: Optional[Settings] = None):
        settings = settings or get_settings()
        c = _get_client(settings)
        self.user_id = user_id

        def col(name: str, model, id_field: str = "id") -> SupabaseCollection:
            return SupabaseCollection(c, user_id, name, model, id_field)

        self.emails = col("emails", Email)
        self.memory_rules = col("memory_rules", MemoryRule)
        self.error_cases = col("error_cases", ErrorCase)
        self.success_patterns = col("success_patterns", SuccessPattern)
        self.meetings = col("meetings", Meeting)
        self.drafts = col("drafts", Draft)
        self.briefs = col("briefs", DailyBrief)
        self.agent_runs = col("agent_runs", AgentRun)
        self.triage = col("triage", TriageResult, id_field="email_id")
        self.profile = col("profile", UserProfile)
        self.sub_agents = col("sub_agents", SubAgent)
        self.events = col("events", EventTag)
        self.email_events = col("email_events", EmailEventLink, id_field="email_id")
        self.routines = col("routines", Routine)
        self.nudges = col("nudges", Nudge)

        from ..config import DATA_DIR

        apply_seeds(self, DATA_DIR)
