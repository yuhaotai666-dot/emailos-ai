"""Proactive scheduler: due-check edge cases + routine execution (mock path)."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Routine
from app.services.scheduler import is_due, run_due_routines, run_routine


def _routine(**over) -> Routine:
    base = dict(title="Test", prompt="do it", schedule="daily", time="08:30", kind="ivy_task")
    base.update(over)
    return Routine(**base)


def test_due_after_time_never_run():
    now = datetime(2026, 7, 5, 9, 0)
    assert is_due(_routine(), now) is True


def test_not_due_before_time():
    now = datetime(2026, 7, 5, 8, 0)
    assert is_due(_routine(), now) is False


def test_daily_runs_once_per_day():
    now = datetime(2026, 7, 5, 9, 0)
    already = _routine(last_run_at=datetime(2026, 7, 5, 8, 31))
    assert is_due(already, now) is False
    yesterday = _routine(last_run_at=datetime(2026, 7, 4, 8, 31))
    assert is_due(yesterday, now) is True


def test_weekly_waits_seven_days():
    now = datetime(2026, 7, 5, 9, 0)
    recent = _routine(schedule="weekly", last_run_at=now - timedelta(days=3))
    assert is_due(recent, now) is False
    old = _routine(schedule="weekly", last_run_at=now - timedelta(days=8))
    assert is_due(old, now) is True


def test_disabled_never_due():
    now = datetime(2026, 7, 5, 9, 0)
    assert is_due(_routine(enabled=False), now) is False


def test_run_routine_triage_brief_produces_nudge(store, mock_llm, settings, monkeypatch):
    # Point globals at the offline fixtures so run_routine's engine is mock.
    import app.repositories.local_store as local_store
    import app.services.llm_client as llm_client
    import app.services.workflow_engine as wf

    monkeypatch.setattr(local_store, "_store", store)
    monkeypatch.setattr(llm_client, "_client", mock_llm)
    monkeypatch.setattr(wf, "_engine", wf.WorkflowEngine(store=store, llm=mock_llm, settings=settings))

    routine = store.routines.get("routine-morning-briefing")
    assert routine is not None  # seeded

    nudge = run_routine(routine, store)
    assert nudge.title == "Morning Briefing"
    assert nudge.items  # need-reply summaries / to-dos
    assert store.routines.get(routine.id).last_run_at is not None
    assert len(store.nudges.list()) == 1

    # Immediately re-checking: not due again today.
    assert run_due_routines(store, datetime.now()) == []

    # A re-run replaces the previous nudge instead of stacking a history.
    run_routine(routine, store)
    assert len([n for n in store.nudges.list() if n.routine_id == routine.id]) == 1
