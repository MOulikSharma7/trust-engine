"""POST /simulation/run — runs the Milestone 2 simulation harness on
demand and returns its summary metrics plus a freshly-rendered plot, so
the dashboard can demo the autonomy curve without a CLI.

Uses an in-memory SqliteRuleStore internally (via run_replay's own
default) — this never touches the real local.db that the rest of the API
reads/writes, exactly per the Milestone 2 design ("no on-disk file needed
for simulation runs").
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from simulation.plots import plot_confidence_bounds
from simulation.profiles import ConsistentApprover, NoisyApprover
from simulation.replay import build_demo_late_catch_profile, run_replay, summarize

from api.schemas import SimulationRunIn, SimulationRunOut

router = APIRouter(prefix="/simulation", tags=["simulation"])

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "simulation" / "output"

_PROFILE_BUILDERS = {
    "consistent": lambda seed: ConsistentApprover(seed=seed),
    "noisy": lambda seed: NoisyApprover(seed=seed),
    "late_catch": build_demo_late_catch_profile,
}


@router.post("/run", response_model=SimulationRunOut)
def run_simulation(run_in: SimulationRunIn) -> SimulationRunOut:
    builder = _PROFILE_BUILDERS.get(run_in.profile)
    if builder is None:
        raise HTTPException(status_code=422, detail=f"Unknown profile {run_in.profile!r}")

    profile = builder(run_in.seed)
    result = run_replay(profile, days=run_in.days)
    metrics = summarize(result)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_path = OUTPUT_DIR / "autonomy_curve.png"
    plot_confidence_bounds(result, output_path=str(plot_path))

    cache_bust = uuid.uuid4().hex[:8]
    return SimulationRunOut(
        false_promotion_rate=metrics["false_promotion_rate"],
        false_promotion_rate_raw=metrics["false_promotion_rate_raw"],
        false_promotion_window_outcomes=metrics["false_promotion_window_outcomes"],
        avg_time_to_trust_by_severity=metrics["avg_time_to_trust_by_severity"],
        recovery_comparisons=metrics["recovery_comparisons"],
        plot_url=f"/static/simulation-output/autonomy_curve.png?v={cache_bust}",
    )
