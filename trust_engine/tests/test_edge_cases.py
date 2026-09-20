"""Edge cases: weaker signal from edited approvals, and tier floor/ceiling
boundaries."""

from trust_engine.models import Decision, Rule, Tier
from trust_engine.engine import record_outcome


def make_rule(severity: int = 2, **overrides) -> Rule:
    return Rule(
        blueprint_id="morning-status",
        action_type="comment_on_ticket",
        severity=severity,
        **overrides,
    )


def test_edited_approval_weaker_signal():
    """A heavily-edited approval increases alpha by less than an unedited
    approval, starting from identical rule state."""
    rule_unchanged = make_rule()
    rule_edited = make_rule()
    starting_alpha = rule_unchanged.alpha

    rule_unchanged = record_outcome(rule_unchanged, Decision.APPROVED_UNCHANGED)
    rule_edited = record_outcome(
        rule_edited, Decision.APPROVED_WITH_EDITS, edit_distance=0.8
    )

    unchanged_delta = rule_unchanged.alpha - starting_alpha
    edited_delta = rule_edited.alpha - starting_alpha

    assert edited_delta < unchanged_delta
    assert edited_delta > 0  # still positive evidence, just weaker


def test_demotion_cannot_go_below_human():
    """Demoting an already HUMAN-tier rule is a no-op on tier (floor), but
    the strike against it is still recorded."""
    rule = make_rule(severity=3, tier=Tier.HUMAN)
    rule = record_outcome(rule, Decision.FLAGGED_AFTER_AUTO_EXECUTION)
    assert rule.tier == Tier.HUMAN
    assert rule.demotion_count == 1


def test_promotion_cannot_exceed_auto():
    """Promoting an already AUTO-tier rule is a no-op — there's no tier
    beyond AUTO to climb to."""
    rule = make_rule(severity=1, tier=Tier.AUTO, alpha=100.0, beta=1.0)
    rule = record_outcome(rule, Decision.APPROVED_UNCHANGED)
    assert rule.tier == Tier.AUTO


def test_invalid_severity_rejected():
    """Severity outside 1-5 is a construction-time error, not a silent
    fallback."""
    import pytest

    with pytest.raises(ValueError):
        make_rule(severity=6)
