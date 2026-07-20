from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..context import user_scope

from ..models import DailyBrief, TriageRunResponse
from ..services.workflow_engine import get_engine

router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[Depends(user_scope)])


@router.post("/run-triage", response_model=TriageRunResponse)
def run_triage() -> TriageRunResponse:
    """Run the full loop-based agent over recent (mock) emails.

    Reads emails, classifies, drafts + critiques + rewrites, checks constraints,
    and populates the review queue. Never sends anything.
    """
    return get_engine().run_triage()


@router.get("/brief", response_model=DailyBrief)
def get_brief() -> DailyBrief:
    brief = get_engine().latest_brief()
    if brief is None:
        raise HTTPException(status_code=404, detail="No brief yet. Run triage first.")
    return brief
