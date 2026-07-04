"""Scorer thresholds and rewrite decisions (mock path)."""
from __future__ import annotations

from app.models import Category, Draft, Email, Priority, TriageResult
from app.services import constraint_checker
from app.services.draft_scorer import DraftScorer


def _email(subject="", body="") -> Email:
    return Email(sender_name="Test", sender_email="t@x.com", subject=subject, body_preview=body)


def _triage(email, category=Category.CREATOR_PARTNERSHIP) -> TriageResult:
    return TriageResult(
        email_id=email.id, category=category, priority=Priority.MEDIUM, needs_reply=True,
        confidence=0.8, summary="s", why_it_matters="w", suggested_action="a", risk_if_ignored="r",
    )


def _draft(body: str, email: Email) -> Draft:
    return Draft(email_id=email.id, original_email_summary="s", draft_body=body)


def test_harsh_draft_low_tone_and_should_rewrite(mock_llm, settings):
    scorer = DraftScorer(mock_llm, settings)
    email = _email("rate", "my rate is $600")
    triage = _triage(email)
    draft = _draft("We cannot afford this, it's too expensive.", email)
    cc = constraint_checker.check(draft.draft_body, "", email, triage)
    ev = scorer.score(email, triage, draft, cc)
    assert ev.tone_score < 8
    assert ev.should_rewrite is True


def test_guarantee_draft_high_risk(mock_llm, settings):
    scorer = DraftScorer(mock_llm, settings)
    email = _email("collab", "can you help")
    triage = _triage(email)
    draft = _draft("We guarantee results and I promise growth.", email)
    ev = scorer.score(email, triage, draft, None)
    assert ev.risk_score > 3
    assert ev.should_rewrite is True


def test_clean_draft_scores_high_no_rewrite(mock_llm, settings):
    scorer = DraftScorer(mock_llm, settings)
    email = _email("collab", "how does it work")
    triage = _triage(email)
    body = (
        "Hi Noah, thanks for your interest. At this stage we run a pilot: a smaller base fee plus "
        "a revenue share, with room to grow. Happy to walk through it on a quick call. Best, Theo"
    )
    draft = _draft(body, email)
    cc = constraint_checker.check(draft.draft_body, "", email, triage)
    ev = scorer.score(email, triage, draft, cc)
    assert ev.overall_score >= settings.min_draft_score
    assert ev.should_rewrite is False
