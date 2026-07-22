"""Migrate Theo's durable local data (app/data/*.json) into Supabase.

Copies the collections worth keeping — knowledge, profile, custom specialists,
event tags, routines, success/error memory — under a Supabase user_id (the
JWT `sub`). Runtime data (emails/drafts/triage/briefs/agent_runs/meetings/
nudges) is skipped: it regenerates on the next triage.

Usage:
    python -m scripts.migrate_local_to_supabase <user_id>
    # default user_id = Theo's yuhaotai666@gmail.com account

Writes directly via SupabaseCollection (no seeding side-effects). Idempotent:
each collection is replace_all'd to exactly the local contents.
"""
from __future__ import annotations

import sys

from app.config import get_settings
from app.models import (
    ErrorCase,
    EventTag,
    EmailEventLink,
    MemoryRule,
    Routine,
    SubAgent,
    SuccessPattern,
    UserProfile,
)
from app.repositories.local_store import _legacy_store
from app.repositories.supabase_store import SupabaseCollection, _get_client

DEFAULT_USER = "cbe7c38d-5c5e-4892-975f-75c15d1c4f30"  # yuhaotai666@gmail.com

# (collection name, model, id_field) — the durable set.
DURABLE = [
    ("memory_rules", MemoryRule, "id"),
    ("error_cases", ErrorCase, "id"),
    ("success_patterns", SuccessPattern, "id"),
    ("sub_agents", SubAgent, "id"),
    ("events", EventTag, "id"),
    ("email_events", EmailEventLink, "email_id"),
    ("routines", Routine, "id"),
    ("profile", UserProfile, "id"),
]


def main() -> None:
    user_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    settings = get_settings()
    if not settings.supabase_db_url:
        raise SystemExit("SUPABASE_DB_URL not set — configure .env first.")

    local = _legacy_store()
    client = _get_client(settings)
    print(f"Migrating durable data → Supabase user {user_id}")

    for name, model, id_field in DURABLE:
        items = getattr(local, name).list()
        SupabaseCollection(client, user_id, name, model, id_field).replace_all(items)
        print(f"  {name}: {len(items)} migrated")

    print("Done. Runtime data (emails/drafts/triage/…) regenerates on next triage.")


if __name__ == "__main__":
    main()
