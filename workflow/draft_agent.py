"""Draft agent: one LLM call per invocation, turning recent Signals into
structured candidate actions.

Depends only on the LLMBackend Protocol — never a specific provider's SDK
directly. `propose()` takes `known_rules` (action_type -> configured
severity), supplied by the caller from the Trust Engine's own store —
that's the actual source of truth for "which action types have somewhere
to route," not a hardcoded list here. Any proposed action_type without a
matching Rule is logged and dropped, never passed downstream.

severity_hint is the model's own per-call guess and is informational
only: the Trust Engine's routing severity always comes from the
pre-configured Rule. A meaningful mismatch is logged as a warning, never
acted on — see check_severity_hint().
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field, ValidationError

from workflow.ingest.normalize import Signal
from workflow.llm.base import LLMBackend

logger = logging.getLogger(__name__)

# |severity_hint - configured_severity| at or above this logs a warning.
SEVERITY_HINT_MISMATCH_THRESHOLD = 2


class CandidateAction(BaseModel):
    action_type: str
    content: str
    severity_hint: int = Field(ge=1, le=5)


class DraftResult(BaseModel):
    status_summary: str
    candidate_actions: list[CandidateAction]


class DraftAgent:
    def __init__(self, backend: LLMBackend):
        self._backend = backend

    def propose(self, signals: list[Signal], known_rules: dict[str, int]) -> DraftResult:
        """known_rules maps action_type -> configured severity, for every
        Rule that already exists for this blueprint. Used both to constrain
        which action_types the LLM may propose and to sanity-check its
        severity_hint against the real, authoritative severity.
        """
        prompt = _build_prompt(signals, known_rules)
        raw = self._backend.generate(prompt)

        try:
            payload = json.loads(raw)
            draft = DraftResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "Draft agent: LLM response failed validation (%s) — discarding this draft.",
                exc,
            )
            return DraftResult(status_summary="", candidate_actions=[])

        valid_actions = []
        for action in draft.candidate_actions:
            if action.action_type not in known_rules:
                logger.warning(
                    "Draft agent: LLM proposed action_type=%r with no matching Rule "
                    "(known types: %s) — dropping this candidate action.",
                    action.action_type,
                    sorted(known_rules),
                )
                continue
            _check_severity_hint(action.action_type, action.severity_hint, known_rules[action.action_type])
            valid_actions.append(action)

        draft.candidate_actions = valid_actions
        return draft


def _check_severity_hint(action_type: str, severity_hint: int, configured_severity: int) -> None:
    if abs(severity_hint - configured_severity) >= SEVERITY_HINT_MISMATCH_THRESHOLD:
        logger.warning(
            "Draft agent: severity_hint=%d for action_type=%r disagrees with its "
            "configured severity=%d. The configured severity is authoritative for "
            "routing; the hint is logged only, never acted on.",
            severity_hint,
            action_type,
            configured_severity,
        )


def _build_prompt(signals: list[Signal], known_rules: dict[str, int]) -> str:
    signal_lines = "\n".join(f"- [{s.source}/{s.kind}] {json.dumps(s.payload)}" for s in signals)
    return f"""You are a morning-status assistant. Given the signals below, write a short
status summary and propose 0-3 candidate actions.

Signals:
{signal_lines or "(no signals)"}

Respond with ONLY valid JSON matching this shape:
{{
  "status_summary": "string",
  "candidate_actions": [
    {{"action_type": "string", "content": "string", "severity_hint": 1-5}}
  ]
}}

action_type MUST be exactly one of: {sorted(known_rules)}
Do not invent any other action_type — there is nowhere for it to route.
"""
