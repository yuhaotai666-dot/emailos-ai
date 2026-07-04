"""Mock email source.

Implements the ``EmailProvider`` seam. A real ``GmailProvider`` would implement
the same ``fetch_recent`` contract later without touching the workflow engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Email
from ..repositories import LocalStore, get_store


class EmailProvider(ABC):
    @abstractmethod
    def fetch_recent(self, limit: int = 50) -> list[Email]:
        ...


class MockEmailProvider(EmailProvider):
    def __init__(self, store: LocalStore | None = None):
        self.store = store or get_store()

    def fetch_recent(self, limit: int = 50) -> list[Email]:
        emails = self.store.emails.list()
        emails.sort(key=lambda e: e.received_at, reverse=True)
        return emails[:limit]
