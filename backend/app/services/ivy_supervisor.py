"""Ivy — the supervisor agent.

Architecture (per Theo's design):

    user task ─► Ivy (planner)
                  ├─ checks the specialist registry (persistent)
                  ├─ existing specialist fits? ─► delegate to it
                  ├─ none fits? ─► create one (persisted for reuse), then delegate
                  └─ reviews the specialist's output before answering the user

Both Ivy and her specialists are LangGraph ReAct agents over the same chat
model. Specialists only get the tools Ivy grants from the shared toolbox —
which contains no email-sending capability, so the "AI never sends" rule is
structural.

In mock mode (no API key) ``IvySupervisor.chat`` returns a deterministic
scripted response so the endpoint and UI still work offline.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from ..models import ChatEvent, ChatResponse, SubAgent
from ..repositories import LocalStore, get_store
from .agent_tools import TOOLBOX, tools_by_names, toolbox_catalog
from .llm_client import LLMClient, get_llm_client

_MAX_HISTORY = 20  # messages kept per conversation (in-memory, single user)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "specialist"


class IvySupervisor:
    def __init__(self, store: Optional[LocalStore] = None, llm: Optional[LLMClient] = None):
        self.store = store or get_store()
        self.llm = llm or get_llm_client()
        self._histories: dict[str, list] = {}
        self._events: list[ChatEvent] = []

    # ------------------------------------------------------------- meta-tools
    def _make_meta_tools(self) -> list:
        supervisor = self

        @tool
        def list_specialists() -> str:
            """List existing specialist sub-agents (name, what they handle, run count)."""
            agents = supervisor.store.sub_agents.list()
            if not agents:
                return "No specialists exist yet."
            return "\n".join(
                f"- {a.name}: {a.description} (used {a.runs}x)" for a in agents
            )

        @tool
        def create_specialist(name: str, description: str, system_prompt: str, tools: str) -> str:
            """Create and persist a new specialist sub-agent.

            Args:
                name: short kebab-case handle, e.g. "rate-analyst".
                description: one sentence — which tasks it handles (used for matching later).
                system_prompt: the specialist's standing instructions.
                tools: comma-separated tool names from the toolbox it may use.
            """
            slug = _slugify(name)
            existing = {a.name for a in supervisor.store.sub_agents.list()}
            if slug in existing:
                return f"Specialist '{slug}' already exists — delegate to it instead."
            tool_names = [t.strip() for t in tools.split(",") if t.strip() in TOOLBOX]
            agent = SubAgent(
                name=slug,
                description=description,
                system_prompt=system_prompt,
                tools=tool_names,
            )
            supervisor.store.sub_agents.add(agent)
            supervisor._events.append(
                ChatEvent(kind="specialist_created", label=f"新建专员 {slug}（工具: {', '.join(tool_names) or '无'}）")
            )
            return f"Created specialist '{slug}' with tools {tool_names}."

        @tool
        def delegate(specialist_name: str, task: str) -> str:
            """Hand a task to an existing specialist and get its result back.

            Args:
                specialist_name: the specialist's handle from list_specialists.
                task: a complete, self-contained task description.
            """
            agent = next(
                (a for a in supervisor.store.sub_agents.list() if a.name == specialist_name),
                None,
            )
            if agent is None:
                return f"No specialist named '{specialist_name}'. Use list_specialists or create_specialist."
            supervisor._events.append(
                ChatEvent(kind="delegated", label=f"派活给专员 {agent.name}")
            )
            result = supervisor._run_specialist(agent, task)
            agent.runs += 1
            agent.last_used_at = _now()
            supervisor.store.sub_agents.update(agent)
            return f"[{agent.name} 的产出]\n{result}"

        return [list_specialists, create_specialist, delegate]

    # -------------------------------------------------------- specialist run
    def _run_specialist(self, agent: SubAgent, task: str) -> str:
        from langgraph.prebuilt import create_react_agent

        worker = create_react_agent(
            self.llm.model,
            tools=tools_by_names(agent.tools),
            prompt=(
                f"{agent.system_prompt}\n\n"
                "You are a specialist working for Ivy (Theo's assistant). Complete the "
                "task fully and reply with your final result only. You cannot send "
                "emails — if asked to, produce draft text instead."
            ),
        )
        out = worker.invoke(
            {"messages": [HumanMessage(content=task)]},
            config={"recursion_limit": 25},
        )
        final = out["messages"][-1]
        return final.content if isinstance(final.content, str) else str(final.content)

    # ---------------------------------------------------------------- system
    def _system_prompt(self) -> str:
        return (
            "You are Ivy, Theo's personal assistant at SuperIntern (growth & partnerships). "
            "You are a planner-supervisor: you rarely do specialised work yourself.\n\n"
            "How you work:\n"
            "1. For trivial chit-chat or a single quick lookup, just answer (you may use "
            "your own tools directly).\n"
            "2. For any substantive task: first call list_specialists. If one matches the "
            "task, delegate to it. If none matches, create_specialist first (give it a "
            "clear role, instructions, and only the tools it needs), then delegate.\n"
            "3. REVIEW every specialist result before answering: check it actually answers "
            "the task, is accurate, and contains no promises or commitments Theo hasn't "
            "made. If inadequate, delegate again with concrete feedback (max 2 retries).\n"
            "4. Answer the user concisely in the user's language, integrating the reviewed result.\n\n"
            f"Shared toolbox available to you and specialists:\n{toolbox_catalog()}\n\n"
            "Hard rule: neither you nor any specialist can send email — there is no such "
            "tool. Drafts are always reviewed by Theo before anything leaves."
        )

    # ------------------------------------------------------------------ chat
    def chat(self, message: str, conversation_id: str = "default") -> ChatResponse:
        if self.llm.mock or self.llm.model is None:
            return self._mock_chat(message, conversation_id)

        from langgraph.prebuilt import create_react_agent

        self._events = []
        own_tools = [TOOLBOX["get_weather"], TOOLBOX["web_search"], TOOLBOX["get_daily_brief"]]
        ivy = create_react_agent(
            self.llm.model,
            tools=own_tools + self._make_meta_tools(),
            prompt=self._system_prompt(),
        )

        history = self._histories.setdefault(conversation_id, [])
        history.append(HumanMessage(content=message))

        out = ivy.invoke(
            {"messages": list(history)},
            config={"recursion_limit": 40},
        )
        new_messages = out["messages"][len(history):]
        # Surface direct tool usage (delegation events are added by the tools).
        for m in new_messages:
            if isinstance(m, ToolMessage) and m.name in TOOLBOX:
                self._events.append(ChatEvent(kind="tool", label=f"使用工具 {m.name}"))

        final = out["messages"][-1]
        reply = final.content if isinstance(final.content, str) else str(final.content)

        history.append(AIMessage(content=reply))
        del history[:-_MAX_HISTORY]

        return ChatResponse(reply=reply, events=self._events, conversation_id=conversation_id)

    # ------------------------------------------------------------------ mock
    def _mock_chat(self, message: str, conversation_id: str) -> ChatResponse:
        """Deterministic offline script so the endpoint works without a key."""
        low = message.lower()
        events = [ChatEvent(kind="review", label="演示模式（未配置 API key）")]
        if any(w in low for w in ("天气", "weather")):
            reply = "（演示）我会创建/复用一个 weather 专员去查天气。配置 API key 后这里就是真实结果。"
        elif any(w in low for w in ("邮件", "email", "回复")):
            reply = "（演示）我会让 inbox 专员分析你的邮件并汇报。配置 API key 后这里就是真实结果。"
        else:
            reply = "（演示）我是 Ivy。配置 API key 后，我会规划任务、创建/复用专员、审核产出后答复你。"
        return ChatResponse(reply=reply, events=events, conversation_id=conversation_id)


_supervisor: Optional[IvySupervisor] = None


def get_supervisor() -> IvySupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = IvySupervisor()
    return _supervisor
