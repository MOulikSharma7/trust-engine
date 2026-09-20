"""Core Trust Engine logic: confidence-bound promotion, outcome updates,
and asymmetric demotion. Pure functions over the Rule model — no I/O.
"""

from __future__ import annotations

from scipy.stats import beta as beta_dist

from .models import Decision, Rule, Tier, TIER_ORDER, _now

# Higher severity = higher bar required to trust. Tunable, but must stay
# monotonically non-decreasing in severity.
SEVERITY_REQUIRED_BOUND: dict[int, float] = {
    1: 0.75,
    2: 0.80,
    3: 0.85,
    4: 0.92,
    5: 0.97,
}

# Each demotion raises the bar for the next promotion by this much (capped).
DEMOTION_PENALTY_PER_COUNT = 0.03
MAX_REQUIRED_BOUND = 0.995


def lower_confidence_bound(rule: Rule, confidence: float = 0.90) -> float:
    """Lower bound of the (confidence*100)% credible interval on the true
    approval rate, computed from the rule's Beta(alpha, beta) belief.
    """
    return float(beta_dist.ppf(1 - confidence, rule.alpha, rule.beta))


def required_bound_for_severity(severity: int) -> float:
    return SEVERITY_REQUIRED_BOUND[severity]


def promote_one_tier(tier: Tier) -> Tier:
    idx = TIER_ORDER.index(tier)
    if idx == len(TIER_ORDER) - 1:
        return tier
    return TIER_ORDER[idx + 1]


def demote_one_tier(tier: Tier) -> Tier:
    idx = TIER_ORDER.index(tier)
    if idx == 0:
        return tier
    return TIER_ORDER[idx - 1]


def maybe_promote(rule: Rule) -> Rule:
    required = required_bound_for_severity(rule.severity)
    # Re-promotion after a demotion requires a stricter bound.
    if rule.demotion_count > 0:
        required = min(
            MAX_REQUIRED_BOUND,
            required + DEMOTION_PENALTY_PER_COUNT * rule.demotion_count,
        )

    # A rule never promotes past its configured ceiling, regardless of evidence.
    if TIER_ORDER.index(rule.tier) >= TIER_ORDER.index(rule.max_tier):
        return rule

    if lower_confidence_bound(rule) >= required:
        rule.tier = promote_one_tier(rule.tier)
    return rule


def normalize(edit_distance: float) -> float:
    """Edit distance is expected in [0, 1], representing the fraction of the
    drafted action that a human had to change. Clamp defensively.
    """
    return max(0.0, min(1.0, edit_distance))


def demote(rule: Rule) -> Rule:
    rule.tier = demote_one_tier(rule.tier)
    rule.demotion_count += 1
    # Partially reset confidence, don't wipe it entirely — some history still counts.
    rule.alpha = max(1.0, rule.alpha * 0.5)
    rule.beta = rule.beta + 2.0
    return rule


def record_outcome(
    rule: Rule, decision: Decision, edit_distance: float | None = None
) -> Rule:
    if decision == Decision.APPROVED_UNCHANGED:
        weight = 1.0
    elif decision == Decision.APPROVED_WITH_EDITS:
        # Heavier edits = weaker positive evidence; scale down the success weight.
        weight = max(0.1, 1.0 - normalize(edit_distance if edit_distance is not None else 0.0))
    elif decision == Decision.REJECTED:
        weight = 0.0
    elif decision == Decision.FLAGGED_AFTER_AUTO_EXECUTION:
        # A mistake on an auto-executed action: strong negative signal + demotion.
        rule.beta += 3.0
        rule = demote(rule)
        rule.last_updated = _now()
        return rule
    else:
        raise ValueError(f"Unhandled decision: {decision!r}")

    rule.alpha += weight
    rule.beta += 1.0 - weight
    rule = maybe_promote(rule)
    rule.last_updated = _now()
    return rule
