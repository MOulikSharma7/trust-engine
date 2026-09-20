"""GET /rules — current tier, belief state, and confidence bound per Rule."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from trust_engine.engine import lower_confidence_bound
from trust_engine.store import RuleStore

from api.dependencies import get_store
from api.schemas import RuleOut

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(store: RuleStore = Depends(get_store)) -> list[RuleOut]:
    return [
        RuleOut(
            blueprint_id=rule.blueprint_id,
            action_type=rule.action_type,
            severity=rule.severity,
            max_tier=rule.max_tier.value,
            tier=rule.tier.value,
            alpha=rule.alpha,
            beta=rule.beta,
            demotion_count=rule.demotion_count,
            last_updated=rule.last_updated,
            confidence_bound=lower_confidence_bound(rule),
        )
        for rule in store.list_rules()
    ]
