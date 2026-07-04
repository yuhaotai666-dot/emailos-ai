"""End-to-end agent loop behaviour on the seeded mock inbox."""
from __future__ import annotations

from app.models import Category, LearnFromEditRequest, Priority
from app.services.memory_service import MemoryService


def _by_email(results, email_id):
    return next(r for r in results if r.email_id == email_id)


def test_run_triage_processes_all_emails(engine):
    resp = engine.run_triage()
    assert resp.run.emails_processed == 10
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
