"""User + assistant profile (fed by onboarding, shown in settings)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    # Singleton row — the store always keeps exactly one, keyed by this id.
    id: str = "profile"
    name: str = "Theo"
    full_name: str = "Theo"
    email: str = ""
    role: str = "Growth & Partnerships"
    company: str = "SuperIntern"
    assistant_name: str = "Ivy"
    # Routines from onboarding (e.g. "Triage my inbox every morning").
    # Stored as configuration only — no scheduler runs them yet.
    routines: list[str] = Field(default_factory=list)
    # The user's standing directives (free text, one per line) — the "user
    # rules" tier of the instruction hierarchy. Injected above Knowledge and
    # below safety/app rules. Edited via Settings → Agent Rules.
    agent_rules: str = ""
    # Whether this user finished the onboarding flow. Lives here (not in
    # browser localStorage) so it follows the account across devices/domains.
    onboarded: bool = False
