"""Synthetic "human responder" profiles for the simulation harness.

Each profile is handed a `ProposedAction` (the drafted action plus the
tier the owning Rule is currently in) and returns a `ProfileResult`: the
decision a human would render on it today, plus zero or more delayed
outcomes to be applied N simulated days later (used by LateCatchApprover
to model a bad auto-executed action that only gets caught weeks after
the fact).

Profiles never touch the Trust Engine directly — replay.py is responsible
for calling engine.record_outcome with whatever a profile returns.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from trust_engine.models import Decision, Tier


@dataclass
class ProposedAction:
    blueprint_id: str
    action_type: str
    content: str
    tier: Tier  # tier the rule was in when this action was routed/executed
    day: int


@dataclass
class DelayedOutcome:
    decision: Decision
    delay_days: int
    edit_distance: float | None = None


@dataclass
class ProfileResult:
    decision: Decision
    edit_distance: float | None = None
    delayed: list[DelayedOutcome] = field(default_factory=list)


class BehaviorProfile(Protocol):
    def resolve(self, action: ProposedAction) -> ProfileResult: ...


class ConsistentApprover:
    """Approves unchanged ~95% of the time, rejects ~5%."""

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def resolve(self, action: ProposedAction) -> ProfileResult:
        if self._rng.random() < 0.95:
            return ProfileResult(Decision.APPROVED_UNCHANGED)
        return ProfileResult(Decision.REJECTED)


class NoisyApprover:
    """Approves with edits ~50%, approves unchanged ~35%, rejects ~15%."""

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def resolve(self, action: ProposedAction) -> ProfileResult:
        roll = self._rng.random()
        if roll < 0.50:
            edit_distance = self._rng.uniform(0.2, 0.9)
            return ProfileResult(
                Decision.APPROVED_WITH_EDITS, edit_distance=edit_distance
            )
        if roll < 0.85:
            return ProfileResult(Decision.APPROVED_UNCHANGED)
        return ProfileResult(Decision.REJECTED)


class LateCatchApprover:
    """Day-to-day, behaves like ConsistentApprover (95% approve unchanged,
    5% reject) — the immediate decision on today's draft is unaffected by
    tier. Independently, for a configurable fraction of actions that were
    routed AUTO (i.e. already auto-executed with no review), it schedules a
    delayed FLAGGED_AFTER_AUTO_EXECUTION some days later: the approval felt
    fine in the moment, but the auto-executed action turns out to have been
    a mistake once someone notices downstream. This delayed flag is what
    should trigger demotion later in the timeline, not immediately.

    Flag risk is not uniform across rules in reality — some action types
    are just more failure-prone once trusted, others genuinely aren't.
    `flag_probability` accepts either a single float (applied uniformly,
    the original behavior) or a dict mapping action_type -> probability,
    letting a caller mark specific rules as "durable" (probability 0, or
    near it) alongside others that behave like the original scenario.
    Action types not present in the dict fall back to
    `default_flag_probability`.
    """

    def __init__(
        self,
        seed: int = 0,
        flag_probability: float | dict[str, float] = 0.05,
        default_flag_probability: float = 0.05,
        flag_delay_days: int = 10,
    ):
        self._rng = random.Random(seed)
        self._flag_delay_days = flag_delay_days
        if isinstance(flag_probability, dict):
            self._flag_probability_by_action = flag_probability
            self._default_flag_probability = default_flag_probability
        else:
            self._flag_probability_by_action = {}
            self._default_flag_probability = flag_probability

    def _flag_probability_for(self, action_type: str) -> float:
        return self._flag_probability_by_action.get(
            action_type, self._default_flag_probability
        )

    def resolve(self, action: ProposedAction) -> ProfileResult:
        decision = (
            Decision.APPROVED_UNCHANGED
            if self._rng.random() < 0.95
            else Decision.REJECTED
        )

        delayed: list[DelayedOutcome] = []
        if action.tier == Tier.AUTO:
            flag_probability = self._flag_probability_for(action.action_type)
            if flag_probability > 0 and self._rng.random() < flag_probability:
                delayed.append(
                    DelayedOutcome(
                        decision=Decision.FLAGGED_AFTER_AUTO_EXECUTION,
                        delay_days=self._flag_delay_days,
                    )
                )

        return ProfileResult(decision=decision, delayed=delayed)


PROFILES: dict[str, type] = {
    "consistent": ConsistentApprover,
    "noisy": NoisyApprover,
    "late_catch": LateCatchApprover,
}
