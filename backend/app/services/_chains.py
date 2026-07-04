"""Shared LCEL prompt scaffolding.

Every LLM step is a ``prompt | llm.structured(...)`` (or ``| llm.text()``) chain.
The system and user strings are passed as *template variables* (not baked into
the template), so arbitrary email content — which may contain ``{`` or ``}`` —
never breaks prompt templating.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

# A single reusable two-message prompt: fill {system} and {user} at invoke time.
SYS_USER_PROMPT: ChatPromptTemplate = ChatPromptTemplate.from_messages(
    [
        ("system", "{system}"),
        ("human", "{user}"),
    ]
)
