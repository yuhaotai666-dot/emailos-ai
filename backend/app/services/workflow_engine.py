"""Workflow engine: orchestrates the loop-based agent over the inbox.

``run_triage`` fetches recent emails and runs each one through the compiled
LangGraph agent (``app.graph``): classify -> (if needs reply) retrieve memory ->
draft -> critique -> score -> check constraints -> rewrite up to
``MAX_DRAFT_RETRIES`` -> keep the best draft. It then assembles the Daily Brief.
Nothing is ever sent; drafts land in the review queue as ``pending_review``.

The per-email graph owns the draft loop; this engine owns fetching, persistence,
the brief, follow-up detection, and the single-draft route operations
(``recompute`` / ``regenerate``).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from ..config import Settings, get_settings
from ..graph import EmailGraphDeps, build_email_graph
from ..graph.nodes import _attach  # reuse the graph's attach helper for route ops
from ..models import (
    AgentRun,
    ConstraintCheck,
    Critique,
    DailyBrief,
    Draft,
    DraftStatus,
    Email,
    FollowUp,
    Priority,
    TriageResult,
    TriageRunResponse,
)
from ..repositories import LocalStore, get_store
from . import constraint_checker
from .classifier import Classifier
from .context_retriever import ContextRetriever
from .draft_critic import DraftCritic
from .draft_generator import DraftGenerator
from .draft_scorer import DraftScorer
from .llm_client import LLMClient, get_llm_client
from .mock_email_provider import EmailProvider, MockEmailProvider


class WorkflowEngine:
    def __init__(
        self,
        store: Optional[LocalStore] = None,
        llm: Optional[LLMClient] = None,
        settings: Optional[Settings] = None,
        email_provider: Optional[EmailProvider] = None,
    ):
        self.store = store or get_store()
        self.llm = llm or get_llm_client()
        self.settings = settings or get_settings()
        self.provider = email_provider or _default_provider(self.store, self.settings)
        self.classifier = Classifier(self.llm)
        self.retriever = ContextRetriever(self.store)
        self.generator = DraftGenerator(self.llm)
        self.critic = DraftCritic(self.llm)
        self.scorer = DraftScorer(self.llm, self.settings)
        # Compile the per-email agent once; it wires the services into a loop.
        self.graph = build_email_graph(
            EmailGraphDeps(
                classifier=self.classifier,
                retriever=self.retriever,
                generator=self.generator,
                critic=self.critic,
                scorer=self.scorer,
                quality_loop=self.settings.enable_quality_loop,
            )
        )

    # -------------------- main entrypoint --------------------
    def run_triage(self, limit: int = 50) -> TriageRunResponse:
        run = AgentRun()
        self.llm.reset_cost()

        # Fresh queue each run (MVP: rebuild triage + drafts; memory persists).
        self.store.triage.replace_all([])
        self.store.drafts.replace_all([])

        emails = self.provider.fetch_recent(limit)
        triage_results: list[TriageResult] = []
        drafts: list[Draft] = []

        # LLM latency dominates, so process emails concurrently. Each graph
        # invocation carries its own state; results are collected and written
        # to the store afterwards (in inbox order) to keep persistence simple.
        def _process(email: Email):
            try:
                return email, self.graph.invoke(
                    {"email": email, "max_retries": self.settings.max_draft_retries}
                ), None
            except Exception as exc:  # keep the run alive on a single bad email
                return email, None, exc

        workers = max(1, self.settings.triage_concurrency)
        if workers == 1 or len(emails) <= 1:
            outcomes = [_process(e) for e in emails]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(_process, emails))

        for email, result, exc in outcomes:
            if exc is not None:
                run.errors.append(f"agent {email.id}: {exc}")
                continue
            triage = result.get("triage")
            if triage is not None:
                self.store.triage.update(triage)
                triage_results.append(triage)

            best = result.get("best")
            if best is not None:
                self.store.drafts.add(best)
                drafts.append(best)

        follow_ups = _detect_follow_ups(emails, triage_results)
        brief = self._build_brief(triage_results, drafts, follow_ups)
        self.store.briefs.add(brief)

        run.completed_at = datetime.now(timezone.utc)
        run.emails_processed = len(emails)
        run.drafts_created = len(drafts)
        run.total_cost_estimate = self.llm.cost_estimate
        run.summary = (
            f"Processed {len(emails)} emails, {len(triage_results)} triaged, "
            f"{len(drafts)} drafts queued for review."
        )
        self.store.agent_runs.add(run)

        return TriageRunResponse(
            brief=brief,
            triage_results=triage_results,
            drafts=drafts,
            follow_ups=follow_ups,
            run=run,
        )

    # -------------------- single-draft operations (routes) --------------------
    def recompute(self, draft: Draft) -> Draft:
        """Re-run the constraint check (always, free) plus — when the quality
        loop is enabled — the LLM critique + score, in place."""
        email = self.store.emails.get(draft.email_id)
        triage = self.store.triage.get(draft.email_id)
        constraint = constraint_checker.check(
            draft.draft_body, draft.subject_suggestion or "", email, triage
        )
        if self.settings.enable_quality_loop and email and triage:
            critique = self.critic.critique(email, triage, draft)
            evaluation = self.scorer.score(email, triage, draft, constraint)
            _attach(draft, critique, evaluation, constraint)
        else:
            draft.constraint_detail = constraint
            draft.constraints_passed = constraint.passed
        return draft

    def regenerate(self, draft: Draft, instruction: Optional[str] = None) -> Draft:
        email = self.store.emails.get(draft.email_id)
        triage = self.store.triage.get(draft.email_id)
        if not email or not triage:
            # Nothing to regenerate against; return as-is.
            return draft
        context = self.retriever.retrieve(email, triage)
        seed_critique = Critique(rewrite_instructions=instruction) if instruction else None
        new_draft = self.generator.rewrite(email, triage, context, draft, seed_critique, None)
        # Keep the queue id stable so the UI reference persists.
        new_draft.id = draft.id
        new_draft.status = DraftStatus.PENDING_REVIEW
        self.recompute(new_draft)
        return new_draft

    # -------------------- brief --------------------
    def _build_brief(
        self,
        triage_results: list[TriageResult],
        drafts: list[Draft],
        follow_ups: list[FollowUp],
    ) -> DailyBrief:
        need_reply = [t for t in triage_results if t.needs_reply]
        top_priority = sorted(
            triage_results,
            key=lambda t: (_priority_rank(t.priority), t.confidence),
            reverse=True,
        )[:5]
        actions = []
        for t in need_reply[:5]:
            actions.append(f"{t.category.value}: {t.suggested_action}")

        summary = (
            f"{len(need_reply)} emails need a reply, {len(drafts)} drafts are ready to review, "
            f"{len(follow_ups)} follow-ups detected."
        )
        return DailyBrief(
            top_priority=top_priority,
            need_reply=need_reply,
            drafts_ready=[d.id for d in drafts],
            follow_ups=follow_ups,
            suggested_actions=actions,
            summary=summary,
        )

    def latest_brief(self) -> Optional[DailyBrief]:
        briefs = self.store.briefs.list()
        if not briefs:
            return None
        return sorted(briefs, key=lambda b: b.generated_at)[-1]


def _priority_rank(p: Priority) -> int:
    return {Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}[p]


def _detect_follow_ups(emails: list[Email], triage: list[TriageResult]) -> list[FollowUp]:
    by_id = {e.id: e for e in emails}
    out: list[FollowUp] = []
    for t in triage:
        email = by_id.get(t.email_id)
        if not email:
            continue
        blob = f"{email.subject} {email.body_preview}".lower()
        waiting = any(w in blob for w in ("follow up", "following up", "waiting", "still", "chase", "reminder"))
        if t.deadline_if_any or (t.needs_reply and (waiting or t.priority == Priority.HIGH)):
            out.append(
                FollowUp(
                    email_id=email.id,
                    sender_name=email.sender_name,
                    subject=email.subject,
                    reason=t.why_it_matters or t.summary,
                    deadline_if_any=t.deadline_if_any,
                )
            )
    return out


def _default_provider(store: LocalStore, settings: Settings) -> EmailProvider:
    """Select the email source from EMAIL_PROVIDER ("mock" | "gmail")."""
    if settings.email_provider == "gmail":
        from .gmail_provider import GmailProvider

        return GmailProvider(store, settings)
    return MockEmailProvider(store)


_engine: Optional[WorkflowEngine] = None


def get_engine() -> WorkflowEngine:
    """Current user's engine when a request context is active, else the
    legacy process-wide engine (tests, CLI, auth-disabled local dev)."""
    from ..context import current_context  # runtime import avoids a cycle

    ctx = current_context()
    if ctx is not None:
        return ctx.engine
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
