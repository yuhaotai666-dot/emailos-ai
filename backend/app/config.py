"""Application configuration.

All tunables come from environment variables (loaded from ``backend/.env`` if
present). Nothing is hardcoded, and a missing ``ANTHROPIC_API_KEY`` is a
supported state: the LLM client falls back to deterministic mock output.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Custom OpenAI-compatible endpoint (proxy/relay). Empty = official API.
    openai_base_url: str = ""
    llm_provider: str = "anthropic"  # "anthropic" | "openai" | "mock"
    llm_model: str = "claude-sonnet-4-6"

    # Sources / persistence
    email_provider: str = "mock"  # "mock" | "gmail"
    database_provider: str = "local"

    # Multi-user auth (Supabase JWT). Disabled = single-user local dev: every
    # request maps to the legacy store, no token required.
    auth_enabled: bool = False
    supabase_url: str = ""  # Lovable's Supabase — issues + validates the JWTs
    supabase_jwt_secret: str = ""  # legacy HS256 secret; empty = use JWKS
    # Backend data store (Theo-owned Supabase project, separate from auth).
    # Used when database_provider == "supabase".
    supabase_db_url: str = ""
    supabase_service_role_key: str = ""

    # Gmail (read-only). Files live under backend/secrets/ (gitignored).
    gmail_credentials_path: str = str(BACKEND_DIR / "secrets" / "gmail_credentials.json")
    gmail_token_path: str = str(BACKEND_DIR / "secrets" / "gmail_token.json")
    # Which messages to pull, Gmail search syntax.
    gmail_query: str = "newer_than:7d"

    # Calendar (read-only). Same OAuth token as Gmail.
    calendar_provider: str = "mock"  # "mock" | "google"
    calendar_days_ahead: int = 7

    # Agent loop
    max_draft_retries: int = 2
    min_draft_score: int = 8
    # When False (default), a draft is generated in ONE LLM call — no LLM
    # critique, scoring, or rewrite (the expensive part of the loop). The
    # deterministic constraint check still runs (free) to flag violations.
    # Set True to re-enable the full critique→score→rewrite quality loop.
    enable_quality_loop: bool = False
    # Emails processed concurrently per triage run (LLM calls dominate latency).
    triage_concurrency: int = 8

    # CORS
    allowed_origins: str = (
        "http://localhost:5173,http://localhost:3000,"
        "https://*.lovable.app,https://*.lovableproject.com"
    )

    @property
    def active_api_key(self) -> str:
        """The API key for the currently selected provider."""
        if self.llm_provider == "openai":
            return self.openai_api_key.strip()
        return self.anthropic_api_key.strip()

    @property
    def has_llm_key(self) -> bool:
        return bool(self.active_api_key)

    @property
    def use_mock_llm(self) -> bool:
        """Mock when the selected provider has no key or provider is 'mock'."""
        return self.llm_provider == "mock" or not self.has_llm_key

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
