"""Supabase JWT verification for the multi-user backend.

The frontend logs users in with Supabase; every API request carries the
session's access token as ``Authorization: Bearer <jwt>``. This module only
answers "who is calling?" — per-user store/engine scoping lives in
``app/context.py``.

Verification paths:
* ``SUPABASE_JWT_SECRET`` set → HS256 with the shared secret (legacy keys).
* otherwise → the project's JWKS endpoint (RS256/ES256, new signing keys).

``AUTH_ENABLED=false`` (local dev / tests) skips verification entirely and
maps every request to the fixed ``local`` user, which is backed by the
legacy single-user store — so Theo's laptop setup keeps working unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request

from .config import Settings, get_settings

# The pseudo-user for auth-disabled mode; context.py maps it to the legacy store.
LOCAL_USER_ID = "local"

_jwks_client = None  # lazily built, caches keys internally


@dataclass
class CurrentUser:
    user_id: str
    email: str = ""


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _decode(token: str, settings: Settings) -> dict:
    import jwt

    if settings.supabase_jwt_secret:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

    global _jwks_client
    if _jwks_client is None:
        if not settings.supabase_url:
            raise RuntimeError("AUTH_ENABLED needs SUPABASE_URL or SUPABASE_JWT_SECRET")
        _jwks_client = jwt.PyJWKClient(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        )
    key = _jwks_client.get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience="authenticated")


def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: resolve the caller from the Authorization header."""
    settings = get_settings()
    if not settings.auth_enabled:
        return CurrentUser(user_id=LOCAL_USER_ID)

    header: Optional[str] = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        raise _unauthorized("Missing bearer token")
    token = header.split(" ", 1)[1].strip()

    try:
        payload = _decode(token, settings)
    except Exception:
        raise _unauthorized("Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized("Token has no subject")
    return CurrentUser(user_id=str(user_id), email=str(payload.get("email") or ""))
