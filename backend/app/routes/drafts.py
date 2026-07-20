from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..context import user_scope
from pydantic import BaseModel

from ..models import Draft, DraftStatus, LearnFromEditRequest, LearnFromEditResult
from ..repositories import get_store
from ..services.memory_service import MemoryService
from ..services.workflow_engine import get_engine

router = APIRouter(prefix="/api/drafts", tags=["drafts"], dependencies=[Depends(user_scope)])


class RegenerateRequest(BaseModel):
    instruction: Optional[str] = None


class EditRequest(BaseModel):
    body: str
    subject_suggestion: Optional[str] = None
    learn: bool = False
    auto_save_memory: bool = False


class EditResponse(BaseModel):
    draft: Draft
    learn_result: Optional[LearnFromEditResult] = None


def _get_or_404(draft_id: str) -> Draft:
    draft = get_store().drafts.get(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.get("", response_model=list[Draft])
def list_drafts() -> list[Draft]:
    return get_store().drafts.list()


@router.get("/{draft_id}", response_model=Draft)
def get_draft(draft_id: str) -> Draft:
    return _get_or_404(draft_id)


@router.post("/{draft_id}/critique", response_model=Draft)
def critique_draft(draft_id: str) -> Draft:
    draft = _get_or_404(draft_id)
    draft = get_engine().recompute(draft)
    return get_store().drafts.update(draft)


@router.post("/{draft_id}/regenerate", response_model=Draft)
def regenerate_draft(draft_id: str, req: RegenerateRequest | None = None) -> Draft:
    draft = _get_or_404(draft_id)
    instruction = req.instruction if req else None
    new_draft = get_engine().regenerate(draft, instruction)
    return get_store().drafts.update(new_draft)


@router.post("/{draft_id}/approve", response_model=Draft)
def approve_draft(draft_id: str) -> Draft:
    """Mark a draft approved. This DOES NOT send the email — sending is out of scope."""
    draft = _get_or_404(draft_id)
    draft.status = DraftStatus.APPROVED
    return get_store().drafts.update(draft)


@router.post("/{draft_id}/edit", response_model=EditResponse)
def edit_draft(draft_id: str, req: EditRequest) -> EditResponse:
    draft = _get_or_404(draft_id)
    original_body = draft.draft_body

    learn_result: Optional[LearnFromEditResult] = None
    if req.learn and req.body.strip() and req.body.strip() != original_body.strip():
        triage = get_store().triage.get(draft.email_id)
        situation = triage.summary if triage else draft.original_email_summary
        learn_result = MemoryService().learn_from_edit(
            LearnFromEditRequest(
                original_draft=original_body,
                edited_draft=req.body,
                situation=situation,
                email_id=draft.email_id,
                auto_save=req.auto_save_memory,
            )
        )

    draft.draft_body = req.body
    if req.subject_suggestion is not None:
        draft.subject_suggestion = req.subject_suggestion
    draft.status = DraftStatus.EDITED
    draft = get_store().drafts.update(draft)
    return EditResponse(draft=draft, learn_result=learn_result)


@router.post("/{draft_id}/ignore", response_model=Draft)
def ignore_draft(draft_id: str) -> Draft:
    draft = _get_or_404(draft_id)
    draft.status = DraftStatus.IGNORED
    return get_store().drafts.update(draft)
