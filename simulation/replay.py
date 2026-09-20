"""Replay loop: compress weeks of real usage into a scripted run against
the Trust Engine, using a synthetic behavior profile in place of a real
human reviewer.

Runs against an in-memory SqliteRuleStore by default (replay doesn't need
durability across runs) and never touches the on-disk local dev DB. Every
proposed action is routed through the rule's *current* tier, resolved by
the active profile, and fed back into trust_engine.engine.record_outcome —
the same pure function the API layer will call in later milestones. This
module contains no trust-scoring logic of its own; it only orchestrates
calls into engine.py and store.py.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from trust_engine.engine import lower_confidence_bound, record_outcome
from trust_engine.models import Decision, Outcome, Rule, Tier
from trust_engine.store import RuleStore, SqliteRuleStore

from simulation.profiles import BehaviorProfile, LateCatchApprover, PROFILES, ProposedAction

# (blueprint_id, action_type, severity) — spans severities 1 through 5 so
# the "higher severity needs more evidence" property is visible in one run.
DEFAULT_RULES: list[tuple[str, str, int]] = [
    ("morning-status", "post_slack_message", 1),
    ("morning-status", "update_pr_description", 2),
    ("morning-status", "flag_blocker_to_stakeholder", 3),
    ("morning-status", "comment_on_payments_ticket", 4),
    ("morning-status", "reschedule_delivery_date", 5),
]

# Per-rule flag risk for the CLI's default "late_catch" demo run. Flag risk
# isn't uniform in reality — some action types earn trust and keep it,
# others are more failure-prone once auto-executed. post_slack_message is
# deliberately durable (0 flag risk) so the demo shows a promotion that
# holds, alongside update_pr_description which keeps the original
# promote -> demote -> slower-recover arc. Rules not listed here (severity
# 3-5) fall back to DEMO_DEFAULT_FLAG_PROBABILITY.
DEMO_FLAG_PROBABILITIES: dict[str, float] = {
    "post_slack_message": 0.0,  # durable: promotes once, never flagged again
    "update_pr_description": 0.05,  # promotes, gets flagged, recovers slower
}
DEMO_DEFAULT_FLAG_PROBABILITY = 0.05
DEMO_FLAG_DELAY_DAYS = 10


def build_demo_late_catch_profile(seed: int) -> LateCatchApprover:
    """The profile behind `python -m simulation.replay --profile=late_catch`:
    per-rule flag risk (see DEMO_FLAG_PROBABILITIES) rather than one global
    probability, so the demo can show both a durable promotion and a
    recovering one in the same run."""
    return LateCatchApprover(
        seed=seed,
        flag_probability=DEMO_FLAG_PROBABILITIES,
        default_flag_probability=DEMO_DEFAULT_FLAG_PROBABILITY,
        flag_delay_days=DEMO_FLAG_DELAY_DAYS,
    )


@dataclass
class TierTransition:
    day: int
    blueprint_id: str
    action_type: str
    old_tier: Tier
    new_tier: Tier
    outcome_index: int  # cumulative outcomes recorded for this rule so far


@dataclass
class RuleSnapshot:
    day: int
    blueprint_id: str
    action_type: str
    severity: int
    tier: Tier
    confidence_bound: float


@dataclass
class ReplayResult:
    rules: list[tuple[str, str, int]]
    transitions: list[TierTransition]
    snapshots: list[RuleSnapshot]
    outcome_counts: dict[tuple[str, str], int]
    store: RuleStore


def _key(blueprint_id: str, action_type: str) -> tuple[str, str]:
    return (blueprint_id, action_type)


def run_replay(
    profile: BehaviorProfile,
    days: int = 90,
    rules: list[tuple[str, str, int]] | None = None,
    store: RuleStore | None = None,
) -> ReplayResult:
    rules = rules if rules is not None else DEFAULT_RULES
    store = store if store is not None else SqliteRuleStore(":memory:")

    outcome_counts: dict[tuple[str, str], int] = {}
    transitions: list[TierTransition] = []
    snapshots: list[RuleSnapshot] = []
    # pending delayed outcomes: (apply_day, blueprint_id, action_type, decision, edit_distance)
    pending: list[tuple[int, str, str, Decision, float | None]] = []

    for blueprint_id, action_type, severity in rules:
        store.save_rule(Rule(blueprint_id=blueprint_id, action_type=action_type, severity=severity))
        outcome_counts[_key(blueprint_id, action_type)] = 0

    def apply_outcome(
        blueprint_id: str,
        action_type: str,
        day: int,
        decision: Decision,
        edit_distance: float | None,
    ) -> None:
        key = _key(blueprint_id, action_type)
        rule = store.get_rule(blueprint_id, action_type)
        old_tier = rule.tier

        updated = record_outcome(rule, decision, edit_distance)
        store.save_rule(updated)
        store.record_outcome(
            Outcome(
                blueprint_id=blueprint_id,
                action_type=action_type,
                decision=decision,
                edit_distance=edit_distance,
            )
        )
        outcome_counts[key] += 1

        if updated.tier != old_tier:
            transitions.append(
                TierTransition(
                    day=day,
                    blueprint_id=blueprint_id,
                    action_type=action_type,
                    old_tier=old_tier,
                    new_tier=updated.tier,
                    outcome_index=outcome_counts[key],
                )
            )

    for day in range(days):
        # Apply any delayed outcomes (late-caught flags) scheduled for today.
        due, pending = (
            [p for p in pending if p[0] == day],
            [p for p in pending if p[0] != day],
        )
        for _, blueprint_id, action_type, decision, edit_distance in due:
            apply_outcome(blueprint_id, action_type, day, decision, edit_distance)

        # Generate and resolve one proposed action per rule for today.
        for blueprint_id, action_type, severity in rules:
            rule = store.get_rule(blueprint_id, action_type)
            action = ProposedAction(
                blueprint_id=blueprint_id,
                action_type=action_type,
                content=f"Status update for {action_type}",
                tier=rule.tier,
                day=day,
            )
            result = profile.resolve(action)
            apply_outcome(blueprint_id, action_type, day, result.decision, result.edit_distance)

            for delayed in result.delayed:
                pending.append(
                    (
                        day + delayed.delay_days,
                        blueprint_id,
                        action_type,
                        delayed.decision,
                        delayed.edit_distance,
                    )
                )

        # End-of-day snapshot for plotting.
        for blueprint_id, action_type, severity in rules:
            rule = store.get_rule(blueprint_id, action_type)
            snapshots.append(
                RuleSnapshot(
                    day=day,
                    blueprint_id=blueprint_id,
                    action_type=action_type,
                    severity=severity,
                    tier=rule.tier,
                    confidence_bound=lower_confidence_bound(rule),
                )
            )

    return ReplayResult(
        rules=rules,
        transitions=transitions,
        snapshots=snapshots,
        outcome_counts=outcome_counts,
        store=store,
    )


# A promotion only counts as "false" if a flag lands within this many
# subsequent outcomes AT THAT RULE — not "at any point in the rest of the
# run." Without a window, false-promotion rate degenerates into "did this
# rule ever get flagged, no matter how long it kept running": over a long
# enough run, any nonzero per-outcome flag probability makes at least one
# flag near-certain, regardless of whether the promotion itself was
# premature. That conflates "a rare event eventually fired" with "the
# engine made a bad promotion decision." Capping the window to outcomes
# immediately following the promotion measures the thing we actually care
# about — whether the evidence available *at promotion time* held up.
FALSE_PROMOTION_WINDOW_OUTCOMES = 15


def summarize(result: ReplayResult) -> dict:
    """False-promotion rate (windowed and raw), average time-to-trust by
    severity, and demotion/recovery-time comparisons — derived entirely
    from the transitions log, not hand-computed to match a target shape.
    """
    severity_by_key = {
        _key(blueprint_id, action_type): severity
        for blueprint_id, action_type, severity in result.rules
    }

    by_rule: dict[tuple[str, str], list[TierTransition]] = {}
    for t in result.transitions:
        by_rule.setdefault(_key(t.blueprint_id, t.action_type), []).append(t)

    total_auto_promotions = 0
    raw_false_promotions = 0  # ever flagged, anywhere in the rest of the run
    windowed_false_promotions = 0  # flagged within FALSE_PROMOTION_WINDOW_OUTCOMES
    time_to_trust_by_severity: dict[int, list[int]] = {}
    recovery_comparisons: list[tuple[int, int]] = []  # (first_promotion_outcomes, recovery_outcomes)

    for key, rule_transitions in by_rule.items():
        severity = severity_by_key[key]
        auto_promotions = sorted(
            (t for t in rule_transitions if t.new_tier == Tier.AUTO),
            key=lambda t: t.outcome_index,
        )
        demotions_from_auto = sorted(
            (t for t in rule_transitions if t.old_tier == Tier.AUTO),
            key=lambda t: t.outcome_index,
        )

        total_auto_promotions += len(auto_promotions)
        for promo in auto_promotions:
            later_demotion = next(
                (d for d in demotions_from_auto if d.outcome_index > promo.outcome_index),
                None,
            )
            if later_demotion is not None:
                raw_false_promotions += 1
                if later_demotion.outcome_index - promo.outcome_index <= FALSE_PROMOTION_WINDOW_OUTCOMES:
                    windowed_false_promotions += 1

        if auto_promotions:
            first_promo = auto_promotions[0]
            time_to_trust_by_severity.setdefault(severity, []).append(first_promo.outcome_index)

            if len(auto_promotions) > 1:
                second_promo = auto_promotions[1]
                demotion_before_second = next(
                    (
                        d
                        for d in demotions_from_auto
                        if first_promo.outcome_index < d.outcome_index < second_promo.outcome_index
                    ),
                    None,
                )
                if demotion_before_second is not None:
                    recovery_outcomes = second_promo.outcome_index - demotion_before_second.outcome_index
                    recovery_comparisons.append((first_promo.outcome_index, recovery_outcomes))

    windowed_false_promotion_rate = (
        windowed_false_promotions / total_auto_promotions if total_auto_promotions else 0.0
    )
    raw_false_promotion_rate = (
        raw_false_promotions / total_auto_promotions if total_auto_promotions else 0.0
    )
    avg_time_to_trust_by_severity = {
        severity: sum(values) / len(values)
        for severity, values in sorted(time_to_trust_by_severity.items())
    }

    return {
        "false_promotion_rate": windowed_false_promotion_rate,
        "false_promotion_rate_raw": raw_false_promotion_rate,
        "false_promotion_window_outcomes": FALSE_PROMOTION_WINDOW_OUTCOMES,
        "total_auto_promotions": total_auto_promotions,
        "avg_time_to_trust_by_severity": avg_time_to_trust_by_severity,
        "recovery_comparisons": recovery_comparisons,
    }


def _print_summary(metrics: dict) -> None:
    print("\n=== Summary metrics ===")
    window = metrics["false_promotion_window_outcomes"]
    print(
        f"False-promotion rate (flag within {window} outcomes of promotion): "
        f"{metrics['false_promotion_rate']:.1%} "
        f"({metrics['total_auto_promotions']} total promotions to AUTO)"
    )
    print(
        f"  (for reference — flagged at any point in the rest of the run, "
        f"which overstates how often promotions were wrong on a long run: "
        f"{metrics['false_promotion_rate_raw']:.1%})"
    )

    print("Average outcomes needed to reach AUTO, by severity:")
    if not metrics["avg_time_to_trust_by_severity"]:
        print("  (no rule reached AUTO in this run)")
    for severity, avg_outcomes in metrics["avg_time_to_trust_by_severity"].items():
        print(f"  severity {severity}: {avg_outcomes:.1f} outcomes")

    print("Recovery time after demotion (first promotion vs re-promotion, in outcomes):")
    if not metrics["recovery_comparisons"]:
        print("  (no demotion + re-promotion observed in this run)")
    for first, recovery in metrics["recovery_comparisons"]:
        print(f"  first promotion: {first} outcomes -> re-promotion: {recovery} outcomes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trust Engine simulation replay")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="late_catch")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--seed",
        type=int,
        default=6,
        help=(
            "Default 6 is validated (not hand-picked): with the per-rule "
            "flag risk in DEMO_FLAG_PROBABILITIES it reliably produces a "
            "durable promotion (post_slack_message), a promote -> demote "
            "-> slower-recover arc (update_pr_description), and severity "
            "4/5 rules that never reach AUTO."
        ),
    )
    parser.add_argument(
        "--output",
        default="simulation/output/autonomy_curve.png",
        help="Path to save the confidence-bound plot",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Also display the plot interactively",
    )
    args = parser.parse_args()

    if args.profile == "late_catch":
        profile = build_demo_late_catch_profile(seed=args.seed)
    else:
        profile = PROFILES[args.profile](seed=args.seed)
    result = run_replay(profile, days=args.days)

    from simulation.plots import plot_confidence_bounds

    plot_confidence_bounds(result, output_path=args.output, show=args.show)
    print(f"Plot saved to {args.output}")

    metrics = summarize(result)
    _print_summary(metrics)


if __name__ == "__main__":
    main()
