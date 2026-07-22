from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..context import user_scope
from pydantic import BaseModel

from ..models import Draft, DraftStatus, LearnFromEditRequest, LearnFromEditResult, MemoryRule
from ..repositories import get_store
from ..services.memory_service import MemoryService
from ..services.workflow_engine import get_engine

router = APIRouter(prefix="/api/drafts", tags=["drafts"], dependencies=[Depends(user_scope)])


class RegenerateRequest(BaseModel):
    instruction: Optional[str] = None


class ReviseRequest(BaseModel):
    message: str


class PushToGmailRequest(BaseModel):
    body: str
    subject: Optional[str] = None


class PushToGmailResponse(BaseModel):
    synced: bool
    gmail_draft_id: Optional[str] = None
    detail: str = ""


class ReviseResponse(BaseModel):
    draft: Draft
    reply_text: str  # the agent's short ack for the chat
    suggested_rule: Optional[MemoryRule] = None  # present when feedback is a standing preference


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


@router.post("/{draft_id}/revise", response_model=ReviseResponse)
def revise_draft(draft_id: str, req: ReviseRequest) -> ReviseResponse:
    """Conversational revision: apply the user's feedback to the draft, and — if
    the feedback reads as a standing preference — suggest a rule to remember
    (the client confirms before it's saved to Knowledge)."""
    draft = _get_or_404(draft_id)
    store = get_store()
    new_draft = store.drafts.update(get_engine().regenerate(draft, req.message))
    triage = store.triage.get(draft.email_id)
    situation = triage.summary if triage else draft.original_email_summary
    rule, ack = MemoryService().analyze_feedback(req.message, situation)
    return ReviseResponse(draft=new_draft, reply_text=ack, suggested_rule=rule)


@router.post("/{draft_id}/push-to-gmail", response_model=PushToGmailResponse)
def push_to_gmail(draft_id: str, req: PushToGmailRequest) -> PushToGmailResponse:
    """Sync the (edited) reply into the user's Gmail Drafts. NEVER sends —
    the user reviews and sends from Gmail. No-op when Gmail isn't connected."""
    from ..config import get_settings

    draft = _get_or_404(draft_id)
    settings = get_settings()
    store = get_store()
    if settings.email_provider != "gmail":
        return PushToGmailResponse(
            synced=False, detail="Gmail not connected — connect it to sync drafts."
        )
    try:
        from ..services.gmail_draft_writer import GmailDraftWriter

        updated = GmailDraftWriter(store, settings).sync(draft, req.body, req.subject)
        updated.status = DraftStatus.APPROVED
        store.drafts.update(updated)
        return PushToGmailResponse(
            synced=True,
            gmail_draft_id=updated.gmail_draft_id,
            detail="Saved to your Gmail Drafts.",
        )
    except Exception as exc:  # never 500 the review flow
        return PushToGmailResponse(synced=False, detail=f"Sync failed: {type(exc).__name__}")


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
