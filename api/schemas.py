"""Pydantic I/O models for the API layer. Kept separate from the Trust
Engine's own dataclasses (Rule, Outcome, ApprovalQueueItem) — those stay
plain and dependency-light; only this layer knows about pydantic-for-
serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workflow.draft_agent import CandidateAction


class SignalIn(BaseModel):
    source: str
    kind: str
    payload: dict = Field(default_factory=dict)


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    blueprint_id: str
    action_type: str
    severity: int
    max_tier: str
    tier: str
    alpha: float
    beta: float
    demotion_count: int
    last_updated: datetime
    confidence_bound: float  # computed by the route, not stored on Rule itself


class ApprovalQueueItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    blueprint_id: str
    action_type: str
    content: str
    tier: str
    severity_hint: int | None
    status: str
    created_at: datetime
    resolved_at: datetime | None


class ResolveDecisionIn(BaseModel):
    decision: Literal["approved_unchanged", "approved_with_edits", "rejected"]
    edited_content: str | None = None  # required when decision == "approved_with_edits"


class RoutedActionOut(BaseModel):
    action: CandidateAction
    tier: str
    routing: Literal["executed", "queued"]
    detail: str


class ProcessSignalsOut(BaseModel):
    status_summary: str
    signals_ingested: int
    candidate_actions_proposed: int
    routed: list[RoutedActionOut]


class SimulationRunIn(BaseModel):
    profile: Literal["consistent", "noisy", "late_catch"] = "late_catch"
    days: int = 90
    seed: int = 6


class SimulationRunOut(BaseModel):
    false_promotion_rate: float
    false_promotion_rate_raw: float
    false_promotion_window_outcomes: int
    avg_time_to_trust_by_severity: dict[int, float]
    recovery_comparisons: list[tuple[int, int]]
    plot_url: str
