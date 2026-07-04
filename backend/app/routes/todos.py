"""Aggregated to-dos for the todo page.

Derived, read-only view: suggested actions from triage (emails that need a
reply) plus action items from upcoming meetings. Completion state lives in the
frontend for now; this endpoint only supplies the source items.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..repositories import get_store

router = APIRouter(prefix="/api/todos", tags=["todos"])


class TodoView(BaseModel):
    id: str
    text: str
    source: str            # "email" | "meeting"
    source_id: str
    context: str = ""      # who/what it relates to
    priority: Optional[str] = None
    deadline: Optional[str] = None
    created_at: Optional[datetime] = None


@router.get("", response_model=list[TodoView])
def list_todos() -> list[TodoView]:
    store = get_store()
    email_by_id = {e.id: e for e in store.emails.list()}
    todos: list[TodoView] = []

    # From triage: each needs-reply email contributes its suggested action.
    for t in store.triage.list():
        if not t.needs_reply or not t.suggested_action:
            continue
        email = email_by_id.get(t.email_id)
        todos.append(
            TodoView(
                id=f"todo-email-{t.email_id}",
                text=t.suggested_action,
                source="email",
                source_id=t.email_id,
                context=f"{email.sender_name}: {email.subject}" if email else t.summary,
                priority=t.priority.value,
                deadline=t.deadline_if_any,
                created_at=email.received_at if email else None,
            )
        )

    # From meetings: prep action items.
    for m in store.meetings.list():
        for i, item in enumerate(m.action_items):
            todos.append(
                TodoView(
                    id=f"todo-meeting-{m.id}-{i}",
                    text=item,
                    source="meeting",
                    source_id=m.id,
                    context=m.title,
                    created_at=m.starts_at,
                )
            )

    return todos
