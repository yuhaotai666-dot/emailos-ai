"""LangGraph agent: the per-email triage + draft loop as a compiled StateGraph."""
from __future__ import annotations

from .build import EmailGraphDeps, build_email_graph
from .state import EmailState

__all__ = ["EmailGraphDeps", "build_email_graph", "EmailState"]
