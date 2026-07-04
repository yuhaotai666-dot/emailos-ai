"""Typed domain models for EmailOS AI."""
from .agent_run import (
    AgentRun,
    DailyBrief,
    FollowUp,
    TriageRunResponse,
)
from .draft import ConstraintCheck, Draft
from .email import Email
from .enums import Category, DraftStatus, Priority
from .evaluation import Critique, Evaluation
from .memory import (
    ErrorCase,
    LearnFromEditRequest,
    LearnFromEditResult,
    MemoryRule,
    SuccessPattern,
)
from .triage import TriageResult

__all__ = [
    "AgentRun",
    "Category",
    "ConstraintCheck",
    "Critique",
    "DailyBrief",
    "Draft",
    "DraftStatus",
    "Email",
    "ErrorCase",
    "Evaluation",
    "FollowUp",
    "LearnFromEditRequest",
    "LearnFromEditResult",
    "MemoryRule",
    "Priority",
    "SuccessPattern",
    "TriageResult",
    "TriageRunResponse",
]
