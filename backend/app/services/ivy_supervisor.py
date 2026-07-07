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


def _contains_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


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
                f"- {a.name} [{a.kind}]: {a.description} (used {a.runs}x)" for a in agents
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

        @tool
        def create_routine(title: str, prompt: str, time: str = "08:30", schedule: str = "daily") -> str:
            """Schedule a recurring task. Ivy will run it automatically and the
            result appears in the app (never sent anywhere).

            Args:
                title: short name, e.g. "Morning to-do briefing".
                prompt: what to do each run, fully self-contained.
                time: local HH:MM, e.g. "08:30".
                schedule: "daily" or "weekly".
            """
            from ..models import Routine

            routine = Routine(
                title=title,
                prompt=prompt,
                time=time,
                schedule=schedule if schedule in ("daily", "weekly") else "daily",
                kind="ivy_task",
                created_from="chat",
            )
            supervisor.store.routines.add(routine)
            supervisor._events.append(
                ChatEvent(kind="routine_created", label=f"新建定时任务 {title}（{schedule} {time}）")
            )
            return f"Routine '{title}' scheduled ({schedule} at {time}). Results will appear in the app."

        return [list_specialists, create_specialist, delegate, create_routine]

    # -------------------------------------------------------- specialist run
    def _run_specialist(self, agent: SubAgent, task: str) -> str:
        from langgraph.prebuilt import create_react_agent

        worker = create_react_agent(
            self.llm.model,
            tools=tools_by_names(agent.tools),
            prompt=(
                f"{agent.system_prompt}\n\n"
                "You are a specialist working for Ivy (Theo's assistant). Work "
                "efficiently: call each tool AT MOST twice, then give your final "
                "answer — do not keep re-calling tools to polish. If a tool result "
                "is good enough, stop and report it. You cannot send emails — if "
                "asked to, produce draft text instead. Report in the language of "
                "the task you were given (default English)."
            ),
        )
        try:
            out = worker.invoke(
                {"messages": [HumanMessage(content=task)]},
                config={"recursion_limit": 25},
            )
        except Exception as exc:  # tool loop / recursion / provider error
            return (
                f"(specialist '{agent.name}' stopped early: {type(exc).__name__}) "
                "Partial or no result — consider retrying with a narrower task."
            )
        final = out["messages"][-1]
        return final.content if isinstance(final.content, str) else str(final.content)

    # ---------------------------------------------------------------- system
    def _system_prompt(self) -> str:
        return (
            "You are Ivy, Theo's personal assistant at SuperIntern (growth & partnerships). "
            "You are a planner-supervisor: you rarely do specialised work yourself.\n\n"
            "How you work:\n"
            "1. For trivial chit-chat or a single quick lookup (weather, today's schedule, "
            "the daily brief), answer DIRECTLY with your own tools — one tool call, then "
            "answer. Do not call list_specialists for these.\n"
            "2. You have three PERMANENT domain agents — email-agent (inbox, triage, reply "
            "drafts), meeting-agent (schedule, prep, action items), reminder-agent (to-dos, "
            "follow-ups, deadlines). Tasks in those domains ALWAYS go to the domain agent — "
            "never create a new specialist for email/meeting/reminder work.\n"
            "3. For substantive tasks OUTSIDE those domains: call list_specialists; if a "
            "custom specialist matches, delegate to it; otherwise create_specialist first, "
            "then delegate. When creating one, grant ALL tools it needs end-to-end — a "
            "specialist that acts on emails needs list_recent_emails AND the acting tool.\n"
            "4. REVIEW every specialist result before answering: check it actually answers "
            "the task, is accurate, and contains no promises or commitments Theo hasn't "
            "made. If inadequate, delegate again with concrete feedback (max 2 retries). "
            "NEVER create a duplicate specialist for a role that already exists — if a "
            "specialist keeps failing, stop, and tell the user what went wrong instead.\n"
            "5. Answer concisely, integrating the reviewed result.\n\n"
            "Language: your default language is ENGLISH. Mirror the user's language per "
            "message — if the user writes in Chinese, reply in Chinese; otherwise reply in "
            "English. Email drafts themselves stay in the language of the email being "
            "answered (usually English).\n\n"
            f"Shared toolbox available to you and specialists:\n{toolbox_catalog()}\n\n"
            "Scheduling: when the user asks for something recurring ('every day at "
            "8:30…', 'each week…'), use create_routine — do not delegate it.\n\n"
            "Drafting hint: for reply-drafting tasks give the specialist "
            "list_recent_emails + create_reply_draft. create_reply_draft already runs "
            "the full quality loop internally — one call (with the user's tone/length "
            "wishes as `instruction`) is enough; report its output back.\n\n"
            "Hard rule: neither you nor any specialist can send email — there is no such "
            "tool. Drafts are always reviewed by Theo before anything leaves."
        )

    # ------------------------------------------------------------------ chat
    def chat(self, message: str, conversation_id: str = "default") -> ChatResponse:
        if self.llm.mock or self.llm.model is None:
            return self._mock_chat(message, conversation_id)

        from langgraph.prebuilt import create_react_agent

        self._events = []
        # Ivy's own quick-lookup tools: enough to answer common "what's my
        # day look like" questions in one step without delegation.
        own_tools = [
            TOOLBOX["get_weather"],
            TOOLBOX["web_search"],
            TOOLBOX["get_daily_brief"],
            TOOLBOX["list_meetings"],
        ]
        ivy = create_react_agent(
            self.llm.model,
            tools=own_tools + self._make_meta_tools(),
            prompt=self._system_prompt(),
        )

        history = self._histories.setdefault(conversation_id, [])
        # Deterministic language mirroring: small models miss prompt-level
        # language rules, so attach an explicit per-message instruction.
        lang_hint = (
            "（请用中文回答。）" if _contains_chinese(message) else "(Reply in English.)"
        )
        history.append(HumanMessage(content=f"{message}\n\n{lang_hint}"))

        try:
            out = ivy.invoke(
                {"messages": list(history)},
                config={"recursion_limit": 40},
            )
        except Exception as exc:  # never 500 the chat endpoint
            history.pop()  # drop the failed turn so retries start clean
            return ChatResponse(
                reply=(
                    "这个任务我这次没跑完（" + type(exc).__name__ + "）。"
                    "可以换个更具体的说法再试一次，比如指明哪封邮件或哪件事。"
                ),
                events=self._events,
                conversation_id=conversation_id,
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
