"""Persistence layer for the Trust Engine.

`RuleStore` is the interface calling code (simulation harness, API layer)
depends on. `SqliteRuleStore` is the local-dev implementation, backed by
sqlite3 from the standard library — no Docker, no external DB connection
string, no elevated permissions required.

A `PostgresRuleStore` implementing the same `RuleStore` interface is the
Milestone 5 deployment target; it is not implemented here. Calling code
should only ever import `RuleStore`, never `sqlite3`, so swapping backends
later is a one-line change at the call site that constructs the store.

Engine logic (engine.py) does not import this module and knows nothing
about persistence — `Rule`/`Outcome` stay plain dataclasses, and
save/load is entirely the caller's responsibility (e.g. load a Rule,
pass it through record_outcome, save the returned Rule).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .models import Decision, Outcome, Rule, Tier

DEFAULT_DB_PATH = Path(__file__).parent / "local.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    blueprint_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
    max_tier TEXT NOT NULL DEFAULT 'AUTO',
    alpha REAL NOT NULL DEFAULT 1.0,
    beta REAL NOT NULL DEFAULT 1.0,
    tier TEXT NOT NULL DEFAULT 'HUMAN',
    demotion_count INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (blueprint_id, action_type)
);

CREATE TABLE IF NOT EXISTS outcomes (
    id TEXT PRIMARY KEY,
    blueprint_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    edit_distance REAL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (blueprint_id, action_type)
        REFERENCES rules (blueprint_id, action_type)
);

-- Candidate actions routed to APPROVE or HUMAN tier land here instead of
-- executing. They must survive until a person resolves them (Milestone 4's
-- API/UI) — record_outcome is only called for these after that resolution,
-- not when they're first queued.
CREATE TABLE IF NOT EXISTS approval_queue (
    id TEXT PRIMARY KEY,
    blueprint_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    content TEXT NOT NULL,
    tier TEXT NOT NULL,
    severity_hint INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (blueprint_id, action_type)
        REFERENCES rules (blueprint_id, action_type)
);
"""


@dataclass
class ApprovalQueueItem:
    """A candidate action routed to APPROVE or HUMAN tier, waiting on a
    human decision. `tier` records which of the two it was — HUMAN-tier
    items carry the draft for reference only; per the original design, a
    human decides the actual content/action for those, not just
    approve/edit/reject a pre-filled draft.
    """

    blueprint_id: str
    action_type: str
    content: str
    tier: Tier
    severity_hint: int | None = None
    status: str = "pending"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


class RuleStore(Protocol):
    def get_rule(self, blueprint_id: str, action_type: str) -> Rule | None: ...
    def save_rule(self, rule: Rule) -> None: ...
    def list_rules(self) -> list[Rule]: ...
    def record_outcome(self, outcome: Outcome) -> None: ...
    def list_outcomes(self, blueprint_id: str, action_type: str) -> list[Outcome]: ...
    def enqueue_approval(self, item: ApprovalQueueItem) -> None: ...
    def list_approval_queue(self, status: str | None = "pending") -> list[ApprovalQueueItem]: ...
    def get_approval_item(self, item_id: str) -> ApprovalQueueItem | None: ...
    def resolve_approval(self, item_id: str, status: str) -> None: ...


def _rule_from_row(row: tuple) -> Rule:
    (
        blueprint_id,
        action_type,
        severity,
        max_tier,
        alpha,
        beta,
        tier,
        demotion_count,
        last_updated,
    ) = row
    return Rule(
        blueprint_id=blueprint_id,
        action_type=action_type,
        severity=severity,
        max_tier=Tier(max_tier),
        alpha=alpha,
        beta=beta,
        tier=Tier(tier),
        demotion_count=demotion_count,
        last_updated=datetime.fromisoformat(last_updated),
    )


def _outcome_from_row(row: tuple) -> Outcome:
    blueprint_id, action_type, decision, edit_distance, occurred_at = row
    return Outcome(
        blueprint_id=blueprint_id,
        action_type=action_type,
        decision=Decision(decision),
        edit_distance=edit_distance,
        occurred_at=datetime.fromisoformat(occurred_at),
    )


def _approval_queue_item_from_row(row: tuple) -> ApprovalQueueItem:
    (
        item_id,
        blueprint_id,
        action_type,
        content,
        tier,
        severity_hint,
        status,
        created_at,
        resolved_at,
    ) = row
    return ApprovalQueueItem(
        id=item_id,
        blueprint_id=blueprint_id,
        action_type=action_type,
        content=content,
        tier=Tier(tier),
        severity_hint=severity_hint,
        status=status,
        created_at=datetime.fromisoformat(created_at),
        resolved_at=datetime.fromisoformat(resolved_at) if resolved_at else None,
    )


class SqliteRuleStore(RuleStore):
    """RuleStore backed by sqlite3 from the standard library.

    Pass db_path=":memory:" for an ephemeral store (tests, the simulation
    harness — replay doesn't need durability across runs). Pass a file path
    (default: trust_engine/local.db) for durable local dev storage; the
    parent directory is created automatically on first use.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: callers that hand a store instance across
        # threads (e.g. a web framework's sync-dependency thread pool, where
        # one request's dependency resolution and endpoint body can land on
        # different worker threads) would otherwise hit a hard
        # sqlite3.ProgrammingError. There's no concurrent-write scenario
        # here that this would silently corrupt — each request/caller still
        # uses the connection sequentially.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_rule(self, blueprint_id: str, action_type: str) -> Rule | None:
        row = self._conn.execute(
            """
            SELECT blueprint_id, action_type, severity, max_tier, alpha, beta,
                   tier, demotion_count, last_updated
            FROM rules WHERE blueprint_id = ? AND action_type = ?
            """,
            (blueprint_id, action_type),
        ).fetchone()
        return None if row is None else _rule_from_row(row)

    def save_rule(self, rule: Rule) -> None:
        self._conn.execute(
            """
            INSERT INTO rules (
                blueprint_id, action_type, severity, max_tier, alpha, beta,
                tier, demotion_count, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (blueprint_id, action_type) DO UPDATE SET
                severity = excluded.severity,
                max_tier = excluded.max_tier,
                alpha = excluded.alpha,
                beta = excluded.beta,
                tier = excluded.tier,
                demotion_count = excluded.demotion_count,
                last_updated = excluded.last_updated
            """,
            (
                rule.blueprint_id,
                rule.action_type,
                rule.severity,
                rule.max_tier.value,
                rule.alpha,
                rule.beta,
                rule.tier.value,
                rule.demotion_count,
                rule.last_updated.isoformat(),
            ),
        )
        self._conn.commit()

    def list_rules(self) -> list[Rule]:
        rows = self._conn.execute(
            """
            SELECT blueprint_id, action_type, severity, max_tier, alpha, beta,
                   tier, demotion_count, last_updated
            FROM rules ORDER BY blueprint_id, action_type
            """
        ).fetchall()
        return [_rule_from_row(row) for row in rows]

    def record_outcome(self, outcome: Outcome) -> None:
        self._conn.execute(
            """
            INSERT INTO outcomes (
                id, blueprint_id, action_type, decision, edit_distance, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                outcome.blueprint_id,
                outcome.action_type,
                outcome.decision.value,
                outcome.edit_distance,
                outcome.occurred_at.isoformat(),
            ),
        )
        self._conn.commit()

    def list_outcomes(self, blueprint_id: str, action_type: str) -> list[Outcome]:
        rows = self._conn.execute(
            """
            SELECT blueprint_id, action_type, decision, edit_distance, occurred_at
            FROM outcomes WHERE blueprint_id = ? AND action_type = ?
            ORDER BY occurred_at
            """,
            (blueprint_id, action_type),
        ).fetchall()
        return [_outcome_from_row(row) for row in rows]

    def enqueue_approval(self, item: ApprovalQueueItem) -> None:
        self._conn.execute(
            """
            INSERT INTO approval_queue (
                id, blueprint_id, action_type, content, tier, severity_hint,
                status, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.blueprint_id,
                item.action_type,
                item.content,
                item.tier.value,
                item.severity_hint,
                item.status,
                item.created_at.isoformat(),
                item.resolved_at.isoformat() if item.resolved_at else None,
            ),
        )
        self._conn.commit()

    def list_approval_queue(self, status: str | None = "pending") -> list[ApprovalQueueItem]:
        if status is None:
            rows = self._conn.execute(
                """
                SELECT id, blueprint_id, action_type, content, tier, severity_hint,
                       status, created_at, resolved_at
                FROM approval_queue ORDER BY created_at
                """
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT id, blueprint_id, action_type, content, tier, severity_hint,
                       status, created_at, resolved_at
                FROM approval_queue WHERE status = ? ORDER BY created_at
                """,
                (status,),
            ).fetchall()
        return [_approval_queue_item_from_row(row) for row in rows]

    def get_approval_item(self, item_id: str) -> ApprovalQueueItem | None:
        row = self._conn.execute(
            """
            SELECT id, blueprint_id, action_type, content, tier, severity_hint,
                   status, created_at, resolved_at
            FROM approval_queue WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        return None if row is None else _approval_queue_item_from_row(row)

    def resolve_approval(self, item_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE approval_queue SET status = ?, resolved_at = ? WHERE id = ?",
            (status, datetime.now(timezone.utc).isoformat(), item_id),
        )
        self._conn.commit()
