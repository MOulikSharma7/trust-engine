"""GET /approval-queue, POST /approval-queue/{item_id}/resolve.

Resolution is where engine.record_outcome finally fires for APPROVE/
HUMAN-tier candidates — deliberately deferred by the Milestone 3 executor,
which only enqueued them. Both tiers resolve the same way: a human either
used the draft as-is, used it with edits, or rejected it outright, and
that's the same signal engine.py has always consumed via Decision.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException

from trust_engine.engine import record_outcome
from trust_engine.models import Decision, Outcome
from trust_engine.store import RuleStore

from api.dependencies import get_store
from api.schemas import ApprovalQueueItemOut, ResolveDecisionIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approval-queue", tags=["approval-queue"])

_DECISION_MAP = {
    "approved_unchanged": Decision.APPROVED_UNCHANGED,
    "approved_with_edits": Decision.APPROVED_WITH_EDITS,
    "rejected": Decision.REJECTED,
}


@router.get("", response_model=list[ApprovalQueueItemOut])
def list_approval_queue(
    status: str | None = "pending", store: RuleStore = Depends(get_store)
) -> list[ApprovalQueueItemOut]:
    return list(store.list_approval_queue(status=status))


@router.post("/{item_id}/resolve", response_model=ApprovalQueueItemOut)
def resolve_approval(
    item_id: str, decision_in: ResolveDecisionIn, store: RuleStore = Depends(get_store)
) -> ApprovalQueueItemOut:
    item = store.get_approval_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No approval-queue item with id={item_id!r}")
    if item.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"Item {item_id!r} was already resolved (status={item.status!r})"
        )

    decision = _DECISION_MAP[decision_in.decision]
    edit_distance = None
    if decision == Decision.APPROVED_WITH_EDITS:
        if not decision_in.edited_content:
            raise HTTPException(status_code=422, detail="edited_content is required for approved_with_edits")
        edit_distance = _normalized_edit_distance(item.content, decision_in.edited_content)

    rule = store.get_rule(item.blueprint_id, item.action_type)
    if rule is None:
        raise HTTPException(status_code=500, detail="Approval item references a Rule that no longer exists")

    updated_rule = record_outcome(rule, decision, edit_distance)
    store.save_rule(updated_rule)
    store.record_outcome(
        Outcome(
            blueprint_id=item.blueprint_id,
            action_type=item.action_type,
            decision=decision,
            edit_distance=edit_distance,
        )
    )
    store.resolve_approval(item_id, status=decision_in.decision)

    logger.info("Approval queue: resolved item id=%s as %s", item_id, decision_in.decision)
    return store.get_approval_item(item_id)


def _normalized_edit_distance(original: str, edited: str) -> float:
    """1 - similarity ratio: identical text -> 0.0, completely different
    text -> close to 1.0 — matching engine.normalize()'s expected [0, 1]
    "fraction of the draft that was changed" semantics.
    """
    similarity = SequenceMatcher(None, original, edited).ratio()
    return max(0.0, min(1.0, 1.0 - similarity))
