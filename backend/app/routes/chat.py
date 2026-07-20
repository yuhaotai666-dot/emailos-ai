"""Chat with Ivy (the supervisor agent) + specialist registry views."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends

from ..context import user_scope

from ..config import Settings, get_settings
from ..models import ChatRequest, ChatResponse, SubAgent
from ..repositories import get_store
from ..services.ivy_supervisor import get_supervisor

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(user_scope)])


def _gmail_connected(settings: Settings) -> bool:
    return settings.email_provider == "gmail" and Path(settings.gmail_token_path).exists()


# Which system agents are gated behind a real integration being linked, and
# how to check it. Agents not listed here (e.g. meeting-agent, reminder-agent
# today) default to always-connected — unchanged from today's behavior.
_CONNECTION_CHECKS: dict[str, Callable[[Settings], bool]] = {
    "email-agent": _gmail_connected,
}


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Talk to Ivy. She plans, delegates to (persistent) specialists, reviews,
    then answers. No send capability exists anywhere in the loop."""
    return get_supervisor().chat(req.message, req.conversation_id)


@router.get("/specialists", response_model=list[SubAgent])
def list_specialists() -> list[SubAgent]:
    """The specialist team Ivy has built so far. ``connected`` on system
    agents is recomputed fresh here — never trusted from disk — since it
    reflects live integration state (e.g. whether Gmail is linked)."""
    settings = get_settings()
    agents = get_store().sub_agents.list()
    for agent in agents:
        if agent.kind == "system":
            check = _CONNECTION_CHECKS.get(agent.name)
            agent.connected = check(settings) if check else True
    return agents
