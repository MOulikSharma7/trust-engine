"""POST /signals — ingest a single externally-submitted signal and run it
through the same draft -> route -> execute path as workflow.pipeline
(via process_signals — no logic duplicated between the CLI and the API).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from trust_engine.store import ApprovalQueueItem, RuleStore

from api.dependencies import get_store
from api.schemas import ProcessSignalsOut, RoutedActionOut, SignalIn
from workflow.ingest.normalize import Signal
from workflow.pipeline import BLUEPRINT_ID, DEFAULT_SLACK_CHANNEL, process_signals

router = APIRouter(prefix="/signals", tags=["signals"])


@router.post("", response_model=ProcessSignalsOut)
def ingest_signal(signal_in: SignalIn, store: RuleStore = Depends(get_store)) -> ProcessSignalsOut:
    signal = Signal(
        source=signal_in.source,
        kind=signal_in.kind,
        payload=signal_in.payload,
        detected_at=datetime.now(timezone.utc),
    )
    try:
        result = process_signals(store, [signal], blueprint_id=BLUEPRINT_ID, slack_channel=DEFAULT_SLACK_CHANNEL)
    except RuntimeError as exc:
        # Raised by the LLM backend factory when the configured provider's
        # API key isn't set (see workflow/llm/*_backend.py) — a config
        # problem, not a server bug, so surface it as a clean 503 rather
        # than an unhandled 500 with a stack trace.
        raise HTTPException(status_code=503, detail=f"Draft agent unavailable: {exc}") from exc

    routed_out = []
    for entry in result["routed"]:
        outcome = entry["outcome"]
        if isinstance(outcome, ApprovalQueueItem):
            routing = "queued"
            detail = f"queued for {entry['tier'].value}-tier review (item id={outcome.id})"
        else:  # ExecutionResult
            routing = "executed"
            detail = outcome.detail
        routed_out.append(
            RoutedActionOut(action=entry["action"], tier=entry["tier"].value, routing=routing, detail=detail)
        )

    return ProcessSignalsOut(
        status_summary=result["draft"].status_summary,
        signals_ingested=len(result["signals"]),
        candidate_actions_proposed=len(result["draft"].candidate_actions),
        routed=routed_out,
    )
