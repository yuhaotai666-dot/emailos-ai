"""Per-request user context: which user's store/engine/supervisor to use.

The multi-tenant trick: instead of threading ``user_id`` through every one of
the ~40 ``get_store()`` call sites, an auth'd request sets a ``ContextVar``
holding that user's :class:`UserContext`; ``get_store()`` /
``get_engine()`` / ``get_supervisor()`` consult it first and fall back to the
legacy process-wide singletons when it's unset (tests, CLI scripts).

Thread caveat: ContextVars don't auto-propagate into ``ThreadPoolExecutor``
workers. ``WorkflowEngine`` is safe (it passes ``self.store`` explicitly to
every service), but any *new* threaded code that calls ``get_store()`` must
set the ContextVar itself — the scheduler does exactly that per user.
"""
from __future__ import annotations

from collections import OrderedDict
from contextvars import ContextVar
from typing import Iterator, Optional

from fastapi import Request

from .auth import LOCAL_USER_ID, CurrentUser
from .config import DATA_DIR

_USERS_DIR = DATA_DIR / "users"
_MAX_CACHED_CONTEXTS = 200  # friends-beta scale; oldest evicted beyond this


class UserContext:
    """One user's store plus lazily-built engine/supervisor bound to it."""

    def __init__(self, user_id: str, store):
        self.user_id = user_id
        self.store = store
        self._engine = None
        self._supervisor = None

    @property
    def engine(self):
        if self._engine is None:
            from .services.workflow_engine import WorkflowEngine

            self._engine = WorkflowEngine(store=self.store)
        return self._engine

    @property
    def supervisor(self):
        if self._supervisor is None:
            from .services.ivy_supervisor import IvySupervisor

            self._supervisor = IvySupervisor(store=self.store)
        return self._supervisor


_current: ContextVar[Optional[UserContext]] = ContextVar("emailos_user_ctx", default=None)
_contexts: "OrderedDict[str, UserContext]" = OrderedDict()


def current_context() -> Optional[UserContext]:
    return _current.get()


def context_for(user_id: str) -> UserContext:
    """Get (or build) the cached context for a user."""
    ctx = _contexts.get(user_id)
    if ctx is None:
        if user_id == LOCAL_USER_ID:
            # Legacy single-user store (backend/app/data) — Theo's local dev.
            from .repositories.local_store import _legacy_store

            store = _legacy_store()
        else:
            from .repositories.local_store import LocalStore

            store = LocalStore(_USERS_DIR / user_id, seed_dir=DATA_DIR)
        ctx = UserContext(user_id, store)
        _contexts[user_id] = ctx
        while len(_contexts) > _MAX_CACHED_CONTEXTS:
            _contexts.popitem(last=False)
    else:
        _contexts.move_to_end(user_id)
    return ctx


def iter_user_ids() -> Iterator[str]:
    """All known users: the legacy local user plus every per-user data dir."""
    yield LOCAL_USER_ID
    if _USERS_DIR.exists():
        for p in sorted(_USERS_DIR.iterdir()):
            if p.is_dir():
                yield p.name


async def user_scope(request: Request):
    """Router dependency: authenticate, then scope this request to the user.

    Applied per-router via ``dependencies=[Depends(user_scope)]`` so route
    bodies keep calling plain ``get_store()`` / ``get_engine()``.
    """
    from .auth import get_current_user

    user: CurrentUser = get_current_user(request)
    ctx = context_for(user.user_id)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def run_as(user_id: str):
    """Context manager for non-request code (scheduler, scripts)."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        ctx = context_for(user_id)
        token = _current.set(ctx)
        try:
            yield ctx
        finally:
            _current.reset(token)

    return _cm()
