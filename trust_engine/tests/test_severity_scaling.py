"""Severity determines how much evidence is required to trust an action,
and can permanently cap how far a rule is allowed to climb."""

from trust_engine.models import Decision, Rule, Tier
from trust_engine.engine import record_outcome


def approve(rule: Rule, n: int) -> Rule:
    for _ in range(n):
        rule = record_outcome(rule, Decision.APPROVED_UNCHANGED)
    return rule


def test_severity_scales_required_evidence():
    """Two rules with identical outcome histories but different severities
    end up at different tiers."""
    low_severity = Rule(
        blueprint_id="morning-status",
        action_type="post_slack_message",
        severity=1,
    )
    high_severity = Rule(
        blueprint_id="morning-status",
        action_type="rollback_deploy",
        severity=5,
    )

    low_severity = approve(low_severity, 15)
    high_severity = approve(high_severity, 15)

    assert low_severity.tier != high_severity.tier
    assert low_severity.tier == Tier.AUTO
    assert high_severity.tier == Tier.HUMAN


def test_max_tier_cap_respected():
    """A rule configured with max_tier=APPROVE never reaches AUTO regardless
    of evidence."""
    rule = Rule(
        blueprint_id="morning-status",
        action_type="post_slack_message",
        severity=1,
        max_tier=Tier.APPROVE,
    )
    rule = approve(rule, 100)
    assert rule.tier == Tier.APPROVE
