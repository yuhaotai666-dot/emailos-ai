"""SupabaseCollection CRUD + isolation, against an in-memory fake PostgREST
client (no network). Verifies (user_id, collection, item_id) scoping."""
from __future__ import annotations

from app.models import MemoryRule, TriageResult, Category, Priority
from app.repositories.supabase_store import SupabaseCollection


# ---- minimal in-memory fake of the supabase-py fluent client ----
class _Resp:
    def __init__(self, data):
        self.data = data


class _Select:
    def __init__(self, rows):
        self._rows = rows
        self._f = {}
        self._limit = None

    def eq(self, k, v):
        self._f[k] = v
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        out = [r for r in self._rows if all(r[k] == v for k, v in self._f.items())]
        if self._limit is not None:
            out = out[: self._limit]
        return _Resp([{"data": r["data"]} for r in out])


class _Delete:
    def __init__(self, rows):
        self._rows = rows
        self._f = {}

    def eq(self, k, v):
        self._f[k] = v
        return self

    def execute(self):
        hit = [r for r in self._rows if all(r[k] == v for k, v in self._f.items())]
        for r in hit:
            self._rows.remove(r)
        return _Resp([{"item_id": r["item_id"]} for r in hit])


class _Done:
    def execute(self):
        return _Resp([])


class _Table:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_):
        return _Select(self._rows)

    def delete(self):
        return _Delete(self._rows)

    def upsert(self, rows):
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            self._rows[:] = [
                r
                for r in self._rows
                if not (
                    r["user_id"] == row["user_id"]
                    and r["collection"] == row["collection"]
                    and r["item_id"] == row["item_id"]
                )
            ]
            self._rows.append(row)
        return _Done()


class FakeClient:
    def __init__(self):
        self.rows: list[dict] = []

    def table(self, _name):
        return _Table(self.rows)


def _rule(situation, pref):
    return MemoryRule(situation=situation, preference=pref)


def test_crud_and_replace_all():
    c = FakeClient()
    col = SupabaseCollection(c, "user-a", "memory_rules", MemoryRule)
    r = _rule("payment", "reference the review process")
    col.add(r)
    assert col.get(r.id).preference == "reference the review process"
    assert len(col.list()) == 1

    r.preference = "updated"
    col.update(r)
    assert col.get(r.id).preference == "updated"

    col.replace_all([_rule("a", "1"), _rule("b", "2")])
    assert len(col.list()) == 2  # old one gone
    col.delete(col.list()[0].id)
    assert len(col.list()) == 1


def test_two_users_isolated():
    c = FakeClient()
    a = SupabaseCollection(c, "user-a", "memory_rules", MemoryRule)
    b = SupabaseCollection(c, "user-b", "memory_rules", MemoryRule)
    a.add(_rule("only-a", "x"))
    assert len(a.list()) == 1
    assert b.list() == []  # b never sees a's row


def test_id_field_override_for_triage():
    c = FakeClient()
    col = SupabaseCollection(c, "user-a", "triage", TriageResult, id_field="email_id")
    t = TriageResult(
        email_id="em-1", category=Category.PAYMENT, priority=Priority.HIGH,
        needs_reply=True, confidence=0.9, summary="s", why_it_matters="w",
        suggested_action="a", risk_if_ignored="r",
    )
    col.add(t)
    assert col.get("em-1").email_id == "em-1"  # keyed on email_id, not id
