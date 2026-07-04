"""Shared enums used across models."""
from __future__ import annotations

from enum import Enum


class Category(str, Enum):
    NEED_REPLY = "Need Reply"
    IMPORTANT = "Important"
    FYI = "FYI"
    MEETING = "Meeting"
    PAYMENT = "Payment"
    PRODUCT_ACCESS = "Product Access"
    CREATOR_PARTNERSHIP = "Creator Partnership"
    SALES = "Sales"
    LOW_PRIORITY = "Low Priority"


class Priority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class DraftStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EDITED = "edited"
    IGNORED = "ignored"
    REGENERATED = "regenerated"
