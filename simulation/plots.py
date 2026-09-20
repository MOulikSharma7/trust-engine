"""Renders the confidence-bound-over-time chart for a replay run.

One line per rule (lower confidence bound on the true approval rate, per
§3.2), with markers overlaid at every tier transition day — promotions up,
demotions down — so a promotion -> demotion -> slower re-promotion arc is
visible at a glance.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # safe default for headless/CLI runs; pyplot still supports show()
import matplotlib.pyplot as plt

from trust_engine.models import Tier

PROMOTION_MARKER = "^"
DEMOTION_MARKER = "v"


def plot_confidence_bounds(result, output_path: str, show: bool = False) -> None:
    by_rule: dict[tuple[str, str], list] = {}
    for snap in result.snapshots:
        by_rule.setdefault((snap.blueprint_id, snap.action_type), []).append(snap)

    fig, ax = plt.subplots(figsize=(11, 6))

    for (blueprint_id, action_type), snaps in sorted(by_rule.items()):
        snaps = sorted(snaps, key=lambda s: s.day)
        severity = snaps[0].severity
        days = [s.day for s in snaps]
        bounds = [s.confidence_bound for s in snaps]
        label = f"{action_type} (sev {severity})"
        (line,) = ax.plot(days, bounds, label=label, linewidth=1.8)

        rule_transitions = [
            t
            for t in result.transitions
            if t.blueprint_id == blueprint_id and t.action_type == action_type
        ]
        for t in rule_transitions:
            is_promotion = _tier_rank(t.new_tier) > _tier_rank(t.old_tier)
            marker = PROMOTION_MARKER if is_promotion else DEMOTION_MARKER
            # Use the snapshot's bound on the transition day so the marker
            # sits on the line, not at an arbitrary height.
            y = next((s.confidence_bound for s in snaps if s.day == t.day), None)
            if y is None:
                continue
            ax.scatter(
                [t.day],
                [y],
                marker=marker,
                color=line.get_color(),
                s=90,
                zorder=5,
                edgecolors="black",
                linewidths=0.6,
            )

    ax.set_xlabel("Simulated day")
    ax.set_ylabel("Lower confidence bound on approval rate")
    ax.set_title("Trust Engine autonomy curve: confidence bound over time")
    ax.axhline(0, color="grey", linewidth=0.5)
    ax.legend(loc="lower right", fontsize=9)
    ax.margins(x=0.02)

    fig.tight_layout()

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    fig.savefig(output_path, dpi=150)

    if show:
        plt.show()
    plt.close(fig)


def _tier_rank(tier: Tier) -> int:
    return {Tier.HUMAN: 0, Tier.APPROVE: 1, Tier.AUTO: 2}[tier]
