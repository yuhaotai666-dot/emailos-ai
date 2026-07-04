"""Abstract repository interface.

The service layer depends only on these methods, so the JSON-backed
``LocalStore`` can later be swapped for a Supabase/Postgres implementation
without touching business logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Collection(ABC, Generic[T]):
    """CRUD over a homogeneous collection of Pydantic models keyed by ``id``."""

    @abstractmethod
    def list(self) -> list[T]:
        ...

    @abstractmethod
    def get(self, item_id: str) -> Optional[T]:
        ...

    @abstractmethod
    def add(self, item: T) -> T:
        ...

    @abstractmethod
    def update(self, item: T) -> T:
        """Replace the stored item with matching ``id`` (or insert if absent)."""
        ...

    @abstractmethod
    def delete(self, item_id: str) -> bool:
        ...

    @abstractmethod
    def replace_all(self, items: list[T]) -> None:
        ...
