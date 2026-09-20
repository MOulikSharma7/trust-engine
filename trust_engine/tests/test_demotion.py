"""Asymmetric demotion: immediate on a flagged auto-execution, and strictly
harder to re-earn trust afterward."""

from trust_engine.models import Decision, Rule, Tier
from trust_engine.engine import record_outcome


def make_auto_rule(severity: int = 2) -> Rule:
    return Rule(
        blueprint_id="morning-status",
        action_type="comment_on_ticket",
        severity=severity,
        tier=Tier.AUTO,
        alpha=20.0,
        beta=2.0,
    )


def test_demotion_on_flagged_auto_action():
    """An AUTO-tier rule demotes immediately on a single
    FLAGGED_AFTER_AUTO_EXECUTION."""
    rule = make_auto_rule()
    rule = record_outcome(rule, Decision.FLAGGED_AFTER_AUTO_EXECUTION)

    assert rule.tier == Tier.APPROVE
    assert rule.demotion_count == 1


def test_reprovision_harder_after_demotion():
    """After a demotion, the same volume of subsequent approvals that
    originally triggered promotion is NOT sufficient to re-promote; more
    evidence is required."""
    rule = Rule(
        blueprint_id="morning-status",
        action_type="post_slack_message",
        severity=1,
    )

    # Find how many consecutive approvals it takes to reach AUTO the first time.
    outcomes_to_first_auto = 0
    while rule.tier != Tier.AUTO:
        rule = record_outcome(rule, Decision.APPROVED_UNCHANGED)
        outcomes_to_first_auto += 1
        assert outcomes_to_first_auto < 1000  # sanity guard against a bad model

    # Now it gets burned: a flagged auto-execution demotes it and raises the bar.
    rule = record_outcome(rule, Decision.FLAGGED_AFTER_AUTO_EXECUTION)
    assert rule.tier == Tier.APPROVE
    assert rule.demotion_count == 1

    # Feed it the exact same number of unedited approvals that sufficed the
    # first time around. It should NOT be enough to reach AUTO again.
    for _ in range(outcomes_to_first_auto):
        rule = record_outcome(rule, Decision.APPROVED_UNCHANGED)

    assert rule.tier != Tier.AUTO
