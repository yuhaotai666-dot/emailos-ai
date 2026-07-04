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


def test_create_reply_draft_tool_queues_pending_review(store, mock_llm, monkeypatch):
    import app.repositories.local_store as local_store
    import app.services.llm_client as llm_client
    from app.services.agent_tools import create_reply_draft

    monkeypatch.setattr(local_store, "_store", store)
    monkeypatch.setattr(llm_client, "_client", mock_llm)  # keep the test offline
    out = create_reply_draft.invoke({"email_ref": "em-001", "instruction": ""})
    assert "queued for review" in out
    assert "NOT be sent" in out
    drafts = [d for d in store.drafts.list() if d.email_id == "em-001"]
    assert len(drafts) == 1
    assert drafts[0].status.value == "pending_review"

    # Fuzzy match by sender name resolves to the same email (updates the draft).
    out_fuzzy = create_reply_draft.invoke({"email_ref": "maya", "instruction": ""})
    assert "queued for review" in out_fuzzy
    assert len([d for d in store.drafts.list() if d.email_id == "em-001"]) == 1

    # Unknown reference fails helpfully instead of crashing.
    out2 = create_reply_draft.invoke({"email_ref": "zzz-nonexistent", "instruction": ""})
    assert "No email matches" in out2
