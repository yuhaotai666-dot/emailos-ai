"""Knowledge page: user-authored reference knowledge, grouped base → entry.

Backed by the existing ``memory_rules`` collection (no new storage model):
  base name  -> MemoryRule.section
  entry title -> MemoryRule.situation
  entry detail -> MemoryRule.preference

An empty base (no entries) has nothing to persist — the frontend keeps a
freshly-created base locally until its first entry is added. This is the
*reference* tier of the instruction hierarchy; it's injected into the draft /
chat prompts via ``ContextRetriever`` (inject-all at consumer scale).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import user_scope
from ..models import MemoryProfile, MemoryRule
from ..repositories import get_store
from ..services.memory_service import MemoryService

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(user_scope)])


# ---- view models ------------------------------------------------------------
class KnowledgeEntry(BaseModel):
    id: str
    title: str
    detail: str


class KnowledgeBaseView(BaseModel):
    id: str  # == name (section is the stable key)
    name: str
    entries: list[KnowledgeEntry]


class EntryCreate(BaseModel):
    base: str
    title: str
    detail: str = ""
    priority: int = 0


class EntryPatch(BaseModel):
    title: Optional[str] = None
    detail: Optional[str] = None
    base: Optional[str] = None
    priority: Optional[int] = None


class BaseRename(BaseModel):
    new_name: str


def _entry(rule: MemoryRule) -> KnowledgeEntry:
    return KnowledgeEntry(id=rule.id, title=rule.situation, detail=rule.preference)


# ---- reads ------------------------------------------------------------------
@router.get("", response_model=list[KnowledgeBaseView])
def list_bases() -> list[KnowledgeBaseView]:
    """All knowledge grouped into bases (only bases that have entries)."""
    grouped: dict[str, list[KnowledgeEntry]] = {}
    for rule in get_store().memory_rules.list():
        section = rule.section or "General"
        grouped.setdefault(section, []).append(_entry(rule))
    return [KnowledgeBaseView(id=name, name=name, entries=entries) for name, entries in grouped.items()]


@router.get("/profile", response_model=MemoryProfile)
def get_knowledge_profile() -> MemoryProfile:
    """Grouped what-the-assistant-knows view (renamed from /api/memory/profile)."""
    return MemoryService().memory_profile()


# ---- entries ----------------------------------------------------------------
@router.post("/entries", response_model=KnowledgeEntry)
def add_entry(body: EntryCreate) -> KnowledgeEntry:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Entry title cannot be empty")
    rule = MemoryRule(
        situation=title,
        preference=body.detail,
        section=body.base.strip() or "General",
        priority=body.priority,
        created_from="knowledge-page",
    )
    get_store().memory_rules.add(rule)
    return _entry(rule)


@router.patch("/entries/{entry_id}", response_model=KnowledgeEntry)
def update_entry(entry_id: str, body: EntryPatch) -> KnowledgeEntry:
    store = get_store()
    rule = store.memory_rules.get(entry_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if body.title is not None and body.title.strip():
        rule.situation = body.title.strip()
    if body.detail is not None:
        rule.preference = body.detail
    if body.base is not None and body.base.strip():
        rule.section = body.base.strip()
    if body.priority is not None:
        rule.priority = body.priority
    store.memory_rules.update(rule)
    return _entry(rule)


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: str) -> dict:
    if not get_store().memory_rules.delete(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"deleted": entry_id}


# ---- bases (a base is just the section string on its rules) -----------------
@router.patch("/bases/{name}")
def rename_base(name: str, body: BaseRename) -> dict:
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="Base name cannot be empty")
    store = get_store()
    moved = 0
    for rule in store.memory_rules.list():
        if (rule.section or "General") == name:
            rule.section = new_name
            store.memory_rules.update(rule)
            moved += 1
    return {"renamed": name, "to": new_name, "entries": moved}


@router.delete("/bases/{name}")
def delete_base(name: str) -> dict:
    store = get_store()
    removed = 0
    for rule in store.memory_rules.list():
        if (rule.section or "General") == name:
            store.memory_rules.delete(rule.id)
            removed += 1
    return {"deleted": name, "entries": removed}
