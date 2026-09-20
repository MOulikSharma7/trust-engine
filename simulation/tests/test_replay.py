"""Tests for the simulation harness: confirms the replay loop produces
genuinely different outcomes per profile (not hardcoded to look right),
and that the LateCatchApprover scenario produces a real promotion ->
demotion -> slower re-promotion arc.
"""

from trust_engine.models import Tier

from simulation.profiles import ConsistentApprover, LateCatchApprover, NoisyApprover
from simulation.replay import build_demo_late_catch_profile, run_replay, summarize

SEV1_RULE = [("morning-status", "post_slack_message", 1)]
SEV3_RULE = [("morning-status", "flag_blocker_to_stakeholder", 3)]


def test_consistent_approver_reaches_auto_on_severity_one():
    result = run_replay(ConsistentApprover(seed=42), days=20, rules=SEV1_RULE)
    rule = result.store.get_rule(*SEV1_RULE[0][:2])
    assert rule.tier == Tier.AUTO


def test_noisy_approver_does_not_reach_auto_on_severity_three_in_same_window():
    """Same day budget (20) that comfortably promotes a severity-1 rule
    under ConsistentApprover is NOT enough for NoisyApprover on a
    severity-3+ rule — profiles must produce meaningfully different
    outcomes, not just cosmetically different labels."""
    result = run_replay(NoisyApprover(seed=42), days=20, rules=SEV3_RULE)
    rule = result.store.get_rule(*SEV3_RULE[0][:2])
    assert rule.tier != Tier.AUTO


def test_late_catch_approver_produces_promotion_demotion_and_slower_reproduction():
    result = run_replay(LateCatchApprover(seed=42), days=90)
    metrics = summarize(result)

    promotions_to_auto = [t for t in result.transitions if t.new_tier == Tier.AUTO]
    demotions_from_auto = [t for t in result.transitions if t.old_tier == Tier.AUTO]

    assert len(promotions_to_auto) >= 1
    assert len(demotions_from_auto) >= 1
    assert len(metrics["recovery_comparisons"]) >= 1

    for first_promotion_outcomes, recovery_outcomes in metrics["recovery_comparisons"]:
        assert recovery_outcomes > first_promotion_outcomes


def test_durable_rule_promotes_and_is_never_demoted():
    """Under the demo's per-rule flag-risk configuration, post_slack_message
    is durable (flag_probability = 0.0): it should promote to AUTO and stay
    there for the rest of the run, receiving zero flags — unlike
    update_pr_description, which is deliberately configured to be flagged
    and must recover more slowly (covered by the test above)."""
    result = run_replay(build_demo_late_catch_profile(seed=6), days=90)

    rule = result.store.get_rule("morning-status", "post_slack_message")
    assert rule.tier == Tier.AUTO

    promotions = [
        t
        for t in result.transitions
        if t.action_type == "post_slack_message" and t.new_tier == Tier.AUTO
    ]
    demotions = [
        t
        for t in result.transitions
        if t.action_type == "post_slack_message" and t.old_tier == Tier.AUTO
    ]
    assert len(promotions) >= 1
    assert len(demotions) == 0


def test_replay_is_deterministic_given_a_seed():
    result_a = run_replay(LateCatchApprover(seed=42), days=60)
    result_b = run_replay(LateCatchApprover(seed=42), days=60)

    transitions_a = [(t.day, t.blueprint_id, t.action_type, t.old_tier, t.new_tier) for t in result_a.transitions]
    transitions_b = [(t.day, t.blueprint_id, t.action_type, t.old_tier, t.new_tier) for t in result_b.transitions]

    assert transitions_a == transitions_b
    assert result_a.outcome_counts == result_b.outcome_counts


def test_different_seeds_can_produce_different_transition_histories():
    result_a = run_replay(LateCatchApprover(seed=1), days=90)
    result_b = run_replay(LateCatchApprover(seed=999), days=90)

    transitions_a = [(t.day, t.action_type, t.new_tier) for t in result_a.transitions]
    transitions_b = [(t.day, t.action_type, t.new_tier) for t in result_b.transitions]

    assert transitions_a != transitions_b


def test_severity_scaling_holds_across_the_default_rule_set():
    """Higher-severity rules in the same run take more outcomes to reach
    AUTO than lower-severity ones — the monotonic relationship the metrics
    are supposed to surface."""
    result = run_replay(ConsistentApprover(seed=42), days=60)
    metrics = summarize(result)
    time_to_trust = metrics["avg_time_to_trust_by_severity"]

    severities = sorted(time_to_trust)
    assert len(severities) >= 2
    outcomes = [time_to_trust[s] for s in severities]
    assert outcomes == sorted(outcomes)
