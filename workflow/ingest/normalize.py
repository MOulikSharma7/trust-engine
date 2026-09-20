"""The stable schema every ingest connector normalizes into.

Downstream code (the draft agent, the pipeline) only ever consumes
`Signal` objects — never a raw GitHub/Slack API response. This is what
lets a connector's upstream API shape change, or a new connector get
added, without touching anything past ingest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Signal:
    source: str  # "github" | "slack"
    kind: str  # e.g. "pr_stale", "pr_opened", "standup_message"
    payload: dict
    detected_at: datetime
