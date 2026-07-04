"""Draft evaluation (scoring) and critique models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Evaluation(BaseModel):
    tone_score: int = Field(ge=1, le=10)
    clarity_score: int = Field(ge=1, le=10)
    completeness_score: int = Field(ge=1, le=10)
    context_score: int = Field(ge=1, le=10)
    risk_score: int = Field(ge=1, le=10, description="Higher = more risky")
    overall_score: int = Field(ge=1, le=10)
    feedback: str = ""
    should_rewrite: bool = False


class Critique(BaseModel):
    issues: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    tone_feedback: str = ""
    risk_feedback: str = ""
    rewrite_instructions: str = ""
