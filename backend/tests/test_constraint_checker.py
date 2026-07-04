"""Hard-constraint checks (deterministic, no LLM)."""
from __future__ import annotations

from app.models import Category, Email, Priority, TriageResult
from app.services import constraint_checker


def _email(subject="", body="") -> Email:
    return Email(sender_name="Test", sender_email="t@x.com", subject=subject, body_preview=body)


def _triage(email, category=Category.NEED_REPLY) -> TriageResult:
    return TriageResult(
        email_id=email.id, category=category, priority=Priority.MEDIUM, needs_reply=True,
        confidence=0.8, summary="s", why_it_matters="w", suggested_action="a", risk_if_ignored="r",
    )


def test_guarantee_results_fails():
    res = constraint_checker.check("We guarantee results for your channel.")
    assert res.passed is False
    assert any("guarantee" in f.lower() for f in res.failed_constraints)


def test_cannot_afford_flagged_as_harsh():
    res = constraint_checker.check("We cannot afford this rate, it is too expensive.")
    assert res.passed is False
    joined = " ".join(res.failed_constraints).lower()
    assert "harsh" in joined or "forbidden" in joined


def test_clean_pilot_rejection_passes():
    body = (
        "Hi Liam, to be transparent, at this stage we're on an early pilot budget, so that "
        "fixed fee is above what we can commit to. We'd love to work together on a smaller base "
        "fee plus a revenue share. Best, Theo"
    )
    res = constraint_checker.check(body)
    assert res.passed is True
    assert res.failed_constraints == []


def test_product_access_without_screenshot_fails():
    email = _email("Access", "I can't log in to the product")
    triage = _triage(email, Category.PRODUCT_ACCESS)
    res = constraint_checker.check("Hi, sorry about that, I'll look into it. Best, Theo", "", email, triage)
    assert res.passed is False
    assert any("screenshot" in f.lower() or "dashboard" in f.lower() for f in res.failed_constraints)


def test_payment_promise_without_confirmation_fails():
    email = _email("Payment", "when will I get paid")
    triage = _triage(email, Category.PAYMENT)
    res = constraint_checker.check("You will be paid on Friday.", "", email, triage)
    assert res.passed is False


def test_over_word_limit_fails():
    body = "word " * 200
    res = constraint_checker.check(body)
    assert res.passed is False
    assert any("word" in f.lower() for f in res.failed_constraints)
