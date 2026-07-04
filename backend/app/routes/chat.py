"""Chat with Ivy (the supervisor agent) + specialist registry views."""
from __future__ import annotations

from fastapi import APIRouter

from ..models import ChatRequest, ChatResponse, SubAgent
from ..repositories import get_store
from ..services.ivy_supervisor import get_supervisor

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Talk to Ivy. She plans, delegates to (persistent) specialists, reviews,
    then answers. No send capability exists anywhere in the loop."""
    return get_supervisor().chat(req.message, req.conversation_id)


@router.get("/specialists", response_model=list[SubAgent])
def list_specialists() -> list[SubAgent]:
    """The specialist team Ivy has built so far."""
    return get_store().sub_agents.list()
