"""Shared test fixtures. Everything runs on the deterministic mock LLM path."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import DATA_DIR, Settings
from app.repositories.local_store import LocalStore
from app.services.llm_client import LLMClient

SEED_FILES = [
    "mock_emails.json",
    "memory_rules.json",
    "error_library.json",
    "success_patterns.json",
    "mock_meetings.json",
    "system_agents.json",
]


@pytest.fixture
def settings() -> Settings:
    # Force every provider offline regardless of the developer's real .env
    # (which may point at real OpenAI + real Gmail).
    return Settings(
        anthropic_api_key="",
        llm_provider="mock",
        email_provider="mock",
        calendar_provider="mock",
        max_draft_retries=2,
        min_draft_score=8,
    )


@pytest.fixture
def mock_llm(settings: Settings) -> LLMClient:
    llm = LLMClient(settings)
    assert llm.mock is True
    return llm


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for f in SEED_FILES:
        shutil.copy(DATA_DIR / f, data_dir / f)
    return LocalStore(data_dir)


@pytest.fixture
def engine(store, mock_llm, settings):
    from app.services.workflow_engine import WorkflowEngine

    return WorkflowEngine(store=store, llm=mock_llm, settings=settings)
