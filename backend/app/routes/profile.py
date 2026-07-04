"""User + assistant profile endpoints (onboarding writes, settings reads)."""
from __future__ import annotations

from fastapi import APIRouter

from ..models import UserProfile
from ..repositories import get_store

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=UserProfile)
def get_profile() -> UserProfile:
    """Return the stored profile, or sensible defaults if onboarding hasn't run."""
    profile = get_store().profile.get("profile")
    return profile or UserProfile()


@router.put("", response_model=UserProfile)
def put_profile(profile: UserProfile) -> UserProfile:
    profile.id = "profile"  # enforce the singleton key
    return get_store().profile.update(profile)
