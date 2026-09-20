"""Store-level tests: SQLite persistence, kept separate from the pure-logic
Milestone 1 tests. Every test runs against its own in-memory (":memory:")
SqliteRuleStore, so nothing here touches the real local dev DB or leaks
state between tests.
"""

from trust_engine.engine import record_outcome
from trust_engine.models import Decision, Outcome, Rule, Tier
from trust_engine.store import SqliteRuleStore


def make_store() -> SqliteRuleStore:
    return SqliteRuleStore(":memory:")


def make_rule(**overrides) -> Rule:
    defaults = dict(
        blueprint_id="morning-status",
        action_type="post_slack_message",
        severity=1,
    )
    defaults.update(overrides)
    return Rule(**defaults)


def test_save_and_get_rule_roundtrip():
    store = make_store()
    rule = make_rule()

    store.save_rule(rule)
    fetched = store.get_rule(rule.blueprint_id, rule.action_type)

    assert fetched is not None
    assert fetched.blueprint_id == rule.blueprint_id
    assert fetched.action_type == rule.action_type
    assert fetched.severity == rule.severity
    assert fetched.max_tier == rule.max_tier
    assert fetched.alpha == rule.alpha
    assert fetched.beta == rule.beta
    assert fetched.tier == rule.tier
    assert fetched.demotion_count == rule.demotion_count
    assert fetched.last_updated == rule.last_updated


def test_get_rule_returns_none_when_missing():
    store = make_store()
    assert store.get_rule("no-such-blueprint", "no-such-action") is None


def test_save_rule_after_outcome_upserts_not_duplicates():
    """Creating a rule, then updating it after an outcome, should overwrite
    the same row rather than create a second one."""
    store = make_store()
    rule = make_rule(severity=1)
    store.save_rule(rule)

    updated = record_outcome(rule, Decision.APPROVED_UNCHANGED)
    store.save_rule(updated)

    assert len(store.list_rules()) == 1
    fetched = store.get_rule(rule.blueprint_id, rule.action_type)
    assert fetched.alpha == updated.alpha
    assert fetched.beta == updated.beta
    assert fetched.tier == updated.tier


def test_list_rules_returns_all_saved_rules():
    store = make_store()
    rule_a = make_rule(action_type="post_slack_message")
    rule_b = make_rule(action_type="comment_on_ticket", severity=3)

    store.save_rule(rule_a)
    store.save_rule(rule_b)

    rules = store.list_rules()
    action_types = {r.action_type for r in rules}
    assert action_types == {"post_slack_message", "comment_on_ticket"}


def test_record_outcome_and_list_outcomes_roundtrip():
    store = make_store()
    rule = make_rule()
    store.save_rule(rule)  # outcomes FK-reference an existing rule

    outcome = Outcome(
        blueprint_id=rule.blueprint_id,
        action_type=rule.action_type,
        decision=Decision.APPROVED_WITH_EDITS,
        edit_distance=0.4,
    )
    store.record_outcome(outcome)

    fetched = store.list_outcomes(rule.blueprint_id, rule.action_type)
    assert len(fetched) == 1
    assert fetched[0].decision == Decision.APPROVED_WITH_EDITS
    assert fetched[0].edit_distance == 0.4
    assert fetched[0].occurred_at == outcome.occurred_at


def test_list_outcomes_scoped_to_rule():
    store = make_store()
    rule_a = make_rule(action_type="post_slack_message")
    rule_b = make_rule(action_type="comment_on_ticket", severity=3)
    store.save_rule(rule_a)
    store.save_rule(rule_b)

    store.record_outcome(
        Outcome(
            blueprint_id=rule_a.blueprint_id,
            action_type=rule_a.action_type,
            decision=Decision.APPROVED_UNCHANGED,
        )
    )
    store.record_outcome(
        Outcome(
            blueprint_id=rule_b.blueprint_id,
            action_type=rule_b.action_type,
            decision=Decision.REJECTED,
        )
    )

    assert len(store.list_outcomes(rule_a.blueprint_id, rule_a.action_type)) == 1
    assert len(store.list_outcomes(rule_b.blueprint_id, rule_b.action_type)) == 1


def test_outcome_requires_existing_rule_via_foreign_key():
    """Foreign key enforcement is explicitly enabled (PRAGMA foreign_keys =
    ON); recording an outcome for a rule that was never saved must fail
    rather than silently succeed."""
    import sqlite3

    import pytest

    store = make_store()
    orphan = Outcome(
        blueprint_id="never-saved",
        action_type="post_slack_message",
        decision=Decision.APPROVED_UNCHANGED,
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.record_outcome(orphan)


def test_end_to_end_create_then_update_after_outcome():
    """Full flow: create a rule, persist it, run it through the pure engine
    logic, persist the result, and read back the updated state — mirroring
    how the simulation harness/API layer will use the store."""
    store = make_store()
    rule = make_rule(severity=2, tier=Tier.HUMAN)
    store.save_rule(rule)

    loaded = store.get_rule(rule.blueprint_id, rule.action_type)
    updated = record_outcome(loaded, Decision.APPROVED_UNCHANGED)
    store.save_rule(updated)
    store.record_outcome(
        Outcome(
            blueprint_id=rule.blueprint_id,
            action_type=rule.action_type,
            decision=Decision.APPROVED_UNCHANGED,
        )
    )

    final = store.get_rule(rule.blueprint_id, rule.action_type)
    assert final.alpha == updated.alpha
    assert len(store.list_outcomes(rule.blueprint_id, rule.action_type)) == 1
