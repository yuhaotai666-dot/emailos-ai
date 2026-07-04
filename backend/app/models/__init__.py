"""Typed domain models for EmailOS AI."""
from .agent_registry import ChatEvent, ChatRequest, ChatResponse, SubAgent
from .agent_run import (
    AgentRun,
    DailyBrief,
    FollowUp,
    TriageRunResponse,
)
from .draft import ConstraintCheck, Draft
from .email import Email
from .meeting import Meeting, MeetingFollowUp, MeetingTodo
from .profile import UserProfile
from .enums import Category, DraftStatus, Priority
from .evaluation import Critique, Evaluation
from .memory import (
    ErrorCase,
    LearnFromEditRequest,
    LearnFromEditResult,
    MemoryProfile,
    MemoryRule,
    MemorySection,
    SuccessPattern,
)
from .triage import TriageResult

__all__ = [
    "AgentRun",
    "ChatEvent",
    "ChatRequest",
    "ChatResponse",
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
    "Meeting",
    "MeetingFollowUp",
    "MeetingTodo",
    "MemoryProfile",
    "MemoryRule",
    "MemorySection",
    "Priority",
    "SubAgent",
    "SuccessPattern",
    "TriageResult",
    "TriageRunResponse",
    "UserProfile",
]
