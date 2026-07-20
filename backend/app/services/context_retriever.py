"""Retrieve the knowledge to ground a draft in.

Consumer accounts hold only a handful of MemoryRules/ErrorCases/SuccessPatterns,
so the default is to inject *all* of them — no filtering, no "keyword miss".
Only if a user's knowledge grows past a token budget do we fall back to
keyword-ranked top-K (the graduation path toward real RAG / pgvector). Also
attaches prior messages in the same thread (``thread_history``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Email, ErrorCase, MemoryRule, SuccessPattern, TriageResult
from ..repositories import LocalStore, get_store
from ._match import score_overlap
from .error_library import ErrorLibrary


_MAX_THREAD_HISTORY = 5

# Rough char budget for the whole injected knowledge block. Under this we inject
# everything (perfect recall); over it we fall back to relevance-ranked top-K.
# ~6000 chars ≈ 1.5k tokens — comfortably small for a consumer account.
_KNOWLEDGE_CHAR_BUDGET = 6000


def _by_priority(rules: list[MemoryRule]) -> list[MemoryRule]:
    """Highest priority first; stable, so same-priority order is preserved."""
    return sorted(rules, key=lambda r: r.priority, reverse=True)


def _within_budget(
    rules: list[MemoryRule],
    successes: list[SuccessPattern],
    errors: list[ErrorCase],
) -> bool:
    total = sum(len(r.situation) + len(r.preference) for r in rules)
    total += sum(len(s.situation) + len(s.why_it_worked) for s in successes)
    total += sum(len(e.situation) + len(e.lesson) for e in errors)
    return total <= _KNOWLEDGE_CHAR_BUDGET


@dataclass
class RetrievedContext:
    rules: list[MemoryRule] = field(default_factory=list)
    errors: list[ErrorCase] = field(default_factory=list)
    successes: list[SuccessPattern] = field(default_factory=list)
    thread_history: list[str] = field(default_factory=list)
    # The user's standing directives (Settings → Agent Rules) — the "user
    # rules" tier, carried here so the (thread-safe, per-user) store lookup
    # happens once in retrieve() rather than inside the generator.
    agent_rules: str = ""


class ContextRetriever:
    def __init__(self, store: LocalStore | None = None):
        self.store = store or get_store()
        self.errors = ErrorLibrary(self.store)

    def retrieve(self, email: Email, triage: TriageResult, k: int = 3) -> RetrievedContext:
        """Gather the knowledge to ground a draft in.

        Consumer accounts carry only a handful of rules, so the default is
        *inject everything* — no filtering, hence no "keyword miss". Only when
        a user's knowledge grows past a token budget do we fall back to
        keyword-ranked top-K (the graduation path toward real RAG). Rules are
        ordered by priority so higher-precedence guidance leads and, when
        capped, survives.
        """
        query = f"{email.subject} {email.body_preview} {triage.category.value} {triage.summary}"

        all_rules = _by_priority(self.store.memory_rules.list())
        all_successes = self.store.success_patterns.list()
        all_errors = self.errors.all()

        if _within_budget(all_rules, all_successes, all_errors):
            rules, successes, errors = all_rules, all_successes, all_errors
        else:
            # Corpus too large for full injection — rank by relevance, but keep
            # rule priority as the tie-break so precedence still wins.
            rules = _by_priority(
                sorted(
                    all_rules,
                    key=lambda r: score_overlap(query, f"{r.situation} {r.preference}"),
                    reverse=True,
                )[: max(k, 5)]
            )
            successes = sorted(
                all_successes,
                key=lambda s: score_overlap(query, f"{s.situation} {s.why_it_worked}"),
                reverse=True,
            )[:k]
            errors = self.errors.relevant(query, triage, k)

        profile = self.store.profile.get("profile")
        return RetrievedContext(
            rules=rules,
            errors=errors,
            successes=successes,
            thread_history=self._thread_history(email),
            agent_rules=(profile.agent_rules if profile else "").strip(),
        )

    def _thread_history(self, email: Email) -> list[str]:
        """Prior messages in the same thread, oldest first, capped so the
        prompt stays bounded. Theo's own past replies are labeled "You" so
        the draft prompt reads like an actual conversation."""
        siblings = [
            e
            for e in self.store.emails.list()
            if e.thread_id == email.thread_id and e.id != email.id
        ]
        if not siblings:
            return []
        siblings.sort(key=lambda e: e.received_at)
        siblings = siblings[-_MAX_THREAD_HISTORY:]

        profile = self.store.profile.get("profile")
        theo_email = (profile.email if profile else "").lower()

        lines = []
        for e in siblings:
            own = e.from_me or (theo_email and e.sender_email.lower() == theo_email)
            who = "You" if own else e.sender_name
            lines.append(f"{e.received_at:%b %d} — {who}: {e.body_preview}")
        return lines
