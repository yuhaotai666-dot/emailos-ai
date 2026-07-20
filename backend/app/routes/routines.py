"""Routines (proactive schedules) and nudges (their in-app results)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..context import user_scope
from pydantic import BaseModel

from ..models import Nudge, Routine
from ..repositories import get_store
from ..services.scheduler import run_routine

router = APIRouter(prefix="/api", tags=["routines"], dependencies=[Depends(user_scope)])


class RoutinePatch(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    schedule: Optional[str] = None
    time: Optional[str] = None
    enabled: Optional[bool] = None


def _get_or_404(routine_id: str) -> Routine:
    routine = get_store().routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")
    return routine


@router.get("/routines", response_model=list[Routine])
def list_routines() -> list[Routine]:
    return get_store().routines.list()


@router.post("/routines", response_model=Routine)
def create_routine(routine: Routine) -> Routine:
    return get_store().routines.add(routine)


@router.patch("/routines/{routine_id}", response_model=Routine)
def patch_routine(routine_id: str, patch: RoutinePatch) -> Routine:
    routine = _get_or_404(routine_id)
    for field, value in patch.model_dump(exclude_none=True).items():
        setattr(routine, field, value)
    return get_store().routines.update(routine)


@router.delete("/routines/{routine_id}")
def delete_routine(routine_id: str) -> dict:
    _get_or_404(routine_id)
    get_store().routines.delete(routine_id)
    return {"deleted": routine_id}


@router.post("/routines/{routine_id}/run", response_model=Nudge)
def run_routine_now(routine_id: str) -> Nudge:
    """Execute a routine immediately (testing / 'Run now' button)."""
    return run_routine(_get_or_404(routine_id))


@router.get("/nudges", response_model=list[Nudge])
def list_nudges(unread_only: bool = False) -> list[Nudge]:
    nudges = get_store().nudges.list()
    if unread_only:
        nudges = [n for n in nudges if not n.read]
    return sorted(nudges, key=lambda n: n.created_at, reverse=True)


@router.post("/nudges/{nudge_id}/read", response_model=Nudge)
def mark_nudge_read(nudge_id: str) -> Nudge:
    nudge = get_store().nudges.get(nudge_id)
    if nudge is None:
        raise HTTPException(status_code=404, detail="Nudge not found")
    nudge.read = True
    return get_store().nudges.update(nudge)
