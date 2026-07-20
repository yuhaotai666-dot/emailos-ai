"""End-to-end agent loop behaviour on the seeded mock inbox."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import (
    Category,
    Email,
    LearnFromEditRequest,
    MemoryRule,
    Priority,
    TriageResult,
    UserProfile,
)
from app.services.context_retriever import ContextRetriever
from app.services.memory_service import MemoryService


def _by_email(results, email_id):
    return next(r for r in results if r.email_id == email_id)


def test_run_triage_processes_all_emails(engine):
    resp = engine.run_triage()
    assert resp.run.emails_processed == 11
    assert resp.run.drafts_created >= 8


def test_payment_email_classified_need_reply_payment(engine):
    resp = engine.run_triage()
    t = _by_email(resp.triage_results, "em-001")
    assert t.category == Category.PAYMENT
    assert t.needs_reply is True
    assert t.priority in (Priority.HIGH, Priority.MEDIUM)


def test_rate_email_produces_pilot_budget_rejection(engine):
    resp = engine.run_triage()
    draft = next(d for d in resp.drafts if d.email_id == "em-002")
    body = draft.draft_body.lower()
    assert "pilot" in body or "at this stage" in body
    # Must not use harsh affordability wording.
    assert "cannot afford" not in body and "too expensive" not in body
    assert draft.constraints_passed is True


def test_product_access_asks_for_screenshot_or_dashboard(engine):
    resp = engine.run_triage()
    draft = next(d for d in resp.drafts if d.email_id == "em-008")
    body = draft.draft_body.lower()
    assert "screenshot" in body or "dashboard" in body


def test_newsletter_creates_no_draft(engine):
    resp = engine.run_triage()
    t = _by_email(resp.triage_results, "em-010")
    assert t.needs_reply is False
    assert all(d.email_id != "em-010" for d in resp.drafts)


def test_need_reply_emails_create_drafts(engine):
    resp = engine.run_triage()
    drafted = {d.email_id for d in resp.drafts}
    for t in resp.triage_results:
        if t.needs_reply:
            assert t.email_id in drafted


def test_all_drafts_pending_review_never_sent(engine):
    resp = engine.run_triage()
    for d in resp.drafts:
        assert d.status.value == "pending_review"


def _payment_triage(email_id: str) -> TriageResult:
    return TriageResult(
        email_id=email_id,
        category=Category.PAYMENT,
        priority=Priority.HIGH,
        needs_reply=True,
        confidence=0.85,
        summary="Follow-up on a payment.",
        why_it_matters="Affects trust with the creator.",
        suggested_action="Reassure and confirm timing.",
        risk_if_ignored="Creator loses trust.",
    )


def test_thread_history_includes_prior_message_excludes_self(store):
    # em-001 and em-012 (seeded) share thread_id "th-001".
    email = next(e for e in store.emails.list() if e.id == "em-012")
    context = ContextRetriever(store=store).retrieve(email, _payment_triage(email.id))
    joined = " ".join(context.thread_history).lower()
    assert "payment for my last video" in joined  # em-001's body_preview
    assert "following up again" not in joined  # em-012's own text must not appear


def test_thread_history_labels_theos_own_reply_as_you(store):
    theo_email = "theo@example.com"
    store.profile.add(UserProfile(email=theo_email))
    store.emails.add(
        Email(
            id="em-901",
            thread_id="th-001",
            sender_name="Theo",
            sender_email=theo_email,
            subject="Re: Payment for last video?",
            body_preview="Thanks for checking in, reviewing now.",
            received_at=datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc),
            source="mock",
        )
    )
    email = next(e for e in store.emails.list() if e.id == "em-012")
    context = ContextRetriever(store=store).retrieve(email, _payment_triage(email.id))
    assert any(line.startswith("Jul 01 — You:") for line in context.thread_history)


def test_thread_history_excludes_unrelated_threads(store):
    email = next(e for e in store.emails.list() if e.id == "em-002")  # thread_id "th-002", no siblings
    context = ContextRetriever(store=store).retrieve(email, _payment_triage(email.id))
    assert context.thread_history == []


def test_retrieve_injects_all_rules_ordered_by_priority(store):
    """Consumer scale: every rule is injected (no keyword filtering), highest
    priority first — so a rule that shares no keywords with the email still
    reaches the draft prompt."""
    store.memory_rules.replace_all(
        [
            MemoryRule(situation="unrelated topic xyz", preference="always sign off warmly", priority=5),
            MemoryRule(situation="another unrelated abc", preference="keep it under 120 words", priority=1),
        ]
    )
    email = next(e for e in store.emails.list() if e.id == "em-001")
    triage = _payment_triage("em-001")
    ctx = ContextRetriever(store=store).retrieve(email, triage)
    prefs = [r.preference for r in ctx.rules]
    # Both rules present despite zero keyword overlap with a payment email.
    assert "always sign off warmly" in prefs
    assert "keep it under 120 words" in prefs
    # Higher priority leads.
    assert prefs[0] == "always sign off warmly"


def test_direct_generate_mode_skips_llm_evaluation(engine):
    """Quality loop off (default): drafts are generated in one pass — no LLM
    critique/evaluation — but the deterministic constraint flag still runs."""
    resp = engine.run_triage()
    assert resp.drafts, "still produces drafts"
    for d in resp.drafts:
        assert d.critique is None  # no LLM critique
        assert d.evaluation is None  # no LLM scoring
        assert d.constraints_passed is not None  # deterministic check still ran


def test_learn_from_edit_extracts_memory_rule(store, mock_llm):
    svc = MemoryService(store=store, llm=mock_llm)
    result = svc.learn_from_edit(
        LearnFromEditRequest(
            original_draft="We cannot afford this rate.",
            edited_draft="At this stage this is above what we can commit to for the pilot.",
            situation="Declining a creator's rate",
            auto_save=False,
        )
    )
    assert result.extracted_preference
    assert result.possible_memory_rule is not None
    # Not saved unless auto_save=True.
    assert result.saved is False
    before = len(store.memory_rules.list())

    saved = svc.learn_from_edit(
        LearnFromEditRequest(
            original_draft="We cannot afford this rate.",
            edited_draft="At this stage this is above what we can commit to for the pilot.",
            situation="Declining a creator's rate",
            auto_save=True,
        )
    )
    assert saved.saved is True
    assert len(store.memory_rules.list()) == before + 1
