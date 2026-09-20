"""Shared FastAPI dependencies. Routes depend on `get_store`, never on
SqliteRuleStore directly — that's what lets tests swap in an isolated
store via app.dependency_overrides without touching route code.
"""

from __future__ import annotations

from typing import Iterator

from trust_engine.store import RuleStore, SqliteRuleStore


def get_store() -> Iterator[RuleStore]:
    """One sqlite3 connection per request, against the real on-disk
    local.db. sqlite3 connections aren't safe to share across requests/
    threads, so each request gets its own rather than a shared singleton.
    """
    store = SqliteRuleStore()
    try:
        yield store
    finally:
        store.close()
