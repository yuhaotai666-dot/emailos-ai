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
