"""Confidence-bound promotion gating (no promotion on a lucky short streak,
but a real statistical trend does promote)."""

from trust_engine.models import Decision, Rule, Tier
from trust_engine.engine import record_outcome


def make_rule(severity: int) -> Rule:
    return Rule(
        blueprint_id="morning-status",
        action_type="post_slack_message",
        severity=severity,
    )


def test_no_promotion_on_short_streak():
    """5/5 approvals is NOT enough evidence to promote a severity-3+ rule."""
    rule = make_rule(severity=3)
    for _ in range(5):
        rule = record_outcome(rule, Decision.APPROVED_UNCHANGED)
    assert rule.tier == Tier.HUMAN


def test_promotion_with_sufficient_history():
    """~40/42 approvals over time promotes a severity-1 or 2 rule."""
    rule = make_rule(severity=1)
    for _ in range(40):
        rule = record_outcome(rule, Decision.APPROVED_UNCHANGED)
    for _ in range(2):
        rule = record_outcome(rule, Decision.REJECTED)
    assert rule.tier == Tier.AUTO
