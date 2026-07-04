"""Ivy supervisor: registry persistence + offline chat path (mock LLM)."""
from __future__ import annotations

from app.models import SubAgent
from app.services.ivy_supervisor import IvySupervisor


def test_registry_persists_specialists(store):
    agent = SubAgent(
        name="rate-analyst",
        description="Analyzes creator rates",
        system_prompt="You analyze rates.",
        tools=["list_recent_emails"],
    )
    store.sub_agents.add(agent)
    loaded = store.sub_agents.list()
    assert len(loaded) == 1
    assert loaded[0].name == "rate-analyst"
    assert loaded[0].runs == 0


def test_mock_chat_returns_scripted_response(store, mock_llm):
    ivy = IvySupervisor(store=store, llm=mock_llm)
    resp = ivy.chat("帮我看看邮件")
    assert resp.reply  # never empty, never crashes without a key
    assert resp.events
    assert resp.conversation_id == "default"


def test_toolbox_has_no_send_capability():
    from app.services.agent_tools import TOOLBOX

    assert not any("send" in name for name in TOOLBOX)  # structural never-send
