"""Data model for the Trust Engine: Rule, Outcome, Tier, Decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tier(str, Enum):
    HUMAN = "HUMAN"
    APPROVE = "APPROVE"
    AUTO = "AUTO"


# Ordering used to promote/demote one step at a time and to compare tiers
# (e.g. enforcing max_tier caps).
TIER_ORDER: list[Tier] = [Tier.HUMAN, Tier.APPROVE, Tier.AUTO]


class Decision(str, Enum):
    APPROVED_UNCHANGED = "APPROVED_UNCHANGED"
    APPROVED_WITH_EDITS = "APPROVED_WITH_EDITS"
    REJECTED = "REJECTED"
    FLAGGED_AFTER_AUTO_EXECUTION = "FLAGGED_AFTER_AUTO_EXECUTION"


@dataclass
class Rule:
    """Tracks trust for one (blueprint_id, action_type) pair."""

    blueprint_id: str
    action_type: str
    severity: int  # 1 (low risk) to 5 (high risk), set at design time
    max_tier: Tier = Tier.AUTO
    alpha: float = 1.0  # Beta distribution successes (+1 prior)
    beta: float = 1.0  # Beta distribution failures (+1 prior)
    tier: Tier = Tier.HUMAN
    demotion_count: int = 0
    last_updated: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not 1 <= self.severity <= 5:
            raise ValueError(f"severity must be between 1 and 5, got {self.severity}")


@dataclass
class Outcome:
    """A single recorded result of a routed action, for audit/history."""

    blueprint_id: str
    action_type: str
    decision: Decision
    edit_distance: float | None = None
    occurred_at: datetime = field(default_factory=_now)
