"""Stage-1 multi-user behaviour: JWT auth gate + per-user store isolation."""
from __future__ import annotations

import jwt as pyjwt
import pytest

TEST_SECRET = "test-secret-0123456789abcdef0123456789abcdef"


@pytest.fixture
def multi_user_env(monkeypatch, tmp_path):
    """Auth ON with an HS256 test secret, user dirs under tmp, all providers
    forced to mock so no network/LLM calls can escape, scheduler neutered."""
    from app import context as ctx_mod
    from app.config import get_settings

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("EMAIL_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("CALENDAR_PROVIDER", "mock")
    get_settings.cache_clear()

    monkeypatch.setattr(ctx_mod, "_USERS_DIR", tmp_path / "users")
    ctx_mod._contexts.clear()
    # The startup scheduler tick must not run real routines during tests.
    monkeypatch.setattr(
        "app.services.scheduler.run_due_routines_all_users", lambda now=None: None
    )
    yield
    get_settings.cache_clear()
    ctx_mod._contexts.clear()


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _token(user_id: str) -> str:
    return pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "email": f"{user_id}@example.com"},
        TEST_SECRET,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def test_requests_without_token_get_401(multi_user_env):
    with _client() as client:
        assert client.get("/api/emails").status_code == 401
        assert client.get("/api/drafts").status_code == 401
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_health_stays_open_without_token(multi_user_env):
    with _client() as client:
        assert client.get("/api/health").status_code == 200


def test_garbage_token_rejected(multi_user_env):
    with _client() as client:
        r = client.get("/api/emails", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401


def test_valid_token_accepted(multi_user_env):
    with _client() as client:
        r = client.get("/api/emails", headers=_auth("user-a"))
        assert r.status_code == 200


def test_two_users_have_isolated_stores(multi_user_env):
    rule = {"situation": "pricing pushback", "preference": "offer the pilot structure"}
    with _client() as client:
        created = client.post("/api/memory/rules", json=rule, headers=_auth("user-a"))
        assert created.status_code == 200

        a_rules = client.get("/api/memory/rules", headers=_auth("user-a")).json()
        b_rules = client.get("/api/memory/rules", headers=_auth("user-b")).json()
        assert any(r["situation"] == "pricing pushback" for r in a_rules)
        assert not any(r["situation"] == "pricing pushback" for r in b_rules)


def test_new_user_store_gets_seeded(multi_user_env):
    """A fresh user starts from the shared seeds (mock inbox, system agents),
    not an empty app."""
    with _client() as client:
        emails = client.get("/api/emails", headers=_auth("user-c")).json()
        assert len(emails) > 0
        specialists = client.get("/api/specialists", headers=_auth("user-c")).json()
        assert any(s["name"] == "email-agent" for s in specialists)
