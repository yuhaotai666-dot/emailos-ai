from __future__ import annotations

from fastapi import APIRouter, Depends

from ..context import user_scope

from ..models import (
    ErrorCase,
    LearnFromEditRequest,
    LearnFromEditResult,
    MemoryProfile,
    MemoryRule,
    SuccessPattern,
)
from ..services.memory_service import MemoryService

router = APIRouter(prefix="/api/memory", tags=["memory"], dependencies=[Depends(user_scope)])


def _svc() -> MemoryService:
    return MemoryService()


@router.get("/profile", response_model=MemoryProfile)
def get_memory_profile() -> MemoryProfile:
    """Grouped what-the-assistant-knows view for the memory page."""
    return _svc().memory_profile()


@router.get("/rules", response_model=list[MemoryRule])
def get_rules() -> list[MemoryRule]:
    return _svc().list_rules()


@router.post("/rules", response_model=MemoryRule)
def add_rule(rule: MemoryRule) -> MemoryRule:
    return _svc().add_rule(rule)


@router.get("/errors", response_model=list[ErrorCase])
def get_errors() -> list[ErrorCase]:
    return _svc().list_errors()


@router.post("/errors", response_model=ErrorCase)
def add_error(case: ErrorCase) -> ErrorCase:
    return _svc().add_error(case)


@router.get("/success-patterns", response_model=list[SuccessPattern])
def get_success_patterns() -> list[SuccessPattern]:
    return _svc().list_success()


@router.post("/success-patterns", response_model=SuccessPattern)
def add_success_pattern(pattern: SuccessPattern) -> SuccessPattern:
    return _svc().add_success(pattern)


@router.post("/learn-from-edit", response_model=LearnFromEditResult)
def learn_from_edit(req: LearnFromEditRequest) -> LearnFromEditResult:
    return _svc().learn_from_edit(req)
