from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..services.llm_client import get_llm_client

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    llm = get_llm_client()
    return {
        "status": "ok",
        "service": "EmailOS AI backend",
        "llm_mode": "mock" if llm.mock else settings.llm_provider,
        "llm_model": settings.llm_model if not llm.mock else None,
        "email_provider": settings.email_provider,
        "database_provider": settings.database_provider,
        "auto_send_enabled": False,
    }
