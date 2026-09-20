"""Executor: takes a CandidateAction plus its Rule's current routing tier
(already decided by the Trust Engine — this module never re-derives it)
and either executes it (AUTO) or queues it for a human (APPROVE/HUMAN).

Which action types get a REAL side effect vs. a SIMULATED (logged-only)
one is a deliberate, explicit choice, not an oversight:
  - post_slack_message -> REAL. Posts via the Slack Web API
    (chat.postMessage), the same API workflow/ingest/slack_connector.py
    reads from.
  - update_pr_description, flag_blocker_to_stakeholder,
    comment_on_payments_ticket, reschedule_delivery_date -> SIMULATED.
    Each would need a specific, carefully-scoped target (which PR, which
    stakeholder channel/DM, which ticket system, which scheduling API)
    that this milestone deliberately does not build. Executing them for
    real risks mutating something real in a demo; simulating logs exactly
    what would have happened instead.
See REAL_EXECUTION_ACTION_TYPES below — that set is the single source of
truth for this split.

AUTO-tier executions call engine.record_outcome immediately, mirroring
the simulation harness: a successful execution with no immediate
objection is recorded as APPROVED_UNCHANGED, the same "nothing went wrong"
signal the behavior profiles produced in Milestone 2. If a real execution
fails, nothing is recorded — a failure to post is not evidence about
whether the action itself was good or bad, and guessing would corrupt the
belief state.

APPROVE/HUMAN-tier candidates are written to the approval_queue and
record_outcome is deliberately NOT called here — that only happens once a
human resolves the item (Milestone 4's API/UI), matching exactly when the
simulation harness's behavior profiles produced their outcome: on
resolution, not on submission.
"""

from __future__ import annotations

import logging
import os

import requests

from trust_engine.engine import record_outcome
from trust_engine.models import Decision, Outcome, Rule, Tier
from trust_engine.store import ApprovalQueueItem, RuleStore

from workflow.draft_agent import CandidateAction

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
REQUEST_TIMEOUT_SECONDS = 10

# Single source of truth for the real-vs-simulated split described above.
REAL_EXECUTION_ACTION_TYPES = frozenset({"post_slack_message"})


class ExecutionResult:
    def __init__(self, executed: bool, simulated: bool, detail: str):
        self.executed = executed
        self.simulated = simulated
        self.detail = detail

    def __repr__(self) -> str:
        return f"ExecutionResult(executed={self.executed}, simulated={self.simulated}, detail={self.detail!r})"


def execute_candidate(
    action: CandidateAction,
    rule: Rule,
    store: RuleStore,
    slack_channel: str | None = None,
) -> ExecutionResult | ApprovalQueueItem:
    if rule.tier == Tier.AUTO:
        return _execute_auto(action, rule, store, slack_channel)
    return _enqueue_for_review(action, rule, store)


def _execute_auto(
    action: CandidateAction, rule: Rule, store: RuleStore, slack_channel: str | None
) -> ExecutionResult:
    if action.action_type in REAL_EXECUTION_ACTION_TYPES:
        result = _perform_real_execution(action, slack_channel)
    else:
        logger.info(
            "Executor: SIMULATED execution for action_type=%r — no real side "
            "effect wired up for this action type. Would have done: %s",
            action.action_type,
            action.content,
        )
        result = ExecutionResult(executed=True, simulated=True, detail="simulated")

    if result.executed:
        updated = record_outcome(rule, Decision.APPROVED_UNCHANGED)
        store.save_rule(updated)
        store.record_outcome(
            Outcome(
                blueprint_id=rule.blueprint_id,
                action_type=rule.action_type,
                decision=Decision.APPROVED_UNCHANGED,
            )
        )
    else:
        logger.warning(
            "Executor: real execution failed for action_type=%r (%s) — not "
            "recording an outcome; a failed post is not evidence either way.",
            action.action_type,
            result.detail,
        )

    return result


def _perform_real_execution(action: CandidateAction, slack_channel: str | None) -> ExecutionResult:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        logger.warning(
            "Executor: SLACK_BOT_TOKEN not set — falling back to SIMULATED "
            "execution for action_type=%r.",
            action.action_type,
        )
        return ExecutionResult(executed=True, simulated=True, detail="simulated (no SLACK_BOT_TOKEN)")

    if not slack_channel:
        logger.warning(
            "Executor: no Slack channel configured — falling back to SIMULATED "
            "execution for action_type=%r.",
            action.action_type,
        )
        return ExecutionResult(executed=True, simulated=True, detail="simulated (no channel configured)")

    try:
        response = requests.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": slack_channel, "text": action.content},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return ExecutionResult(executed=False, simulated=False, detail=f"Slack API error: {data.get('error')}")
    except requests.RequestException as exc:
        return ExecutionResult(executed=False, simulated=False, detail=f"request failed: {exc}")

    return ExecutionResult(executed=True, simulated=False, detail="posted to Slack")


def _enqueue_for_review(action: CandidateAction, rule: Rule, store: RuleStore) -> ApprovalQueueItem:
    item = ApprovalQueueItem(
        blueprint_id=rule.blueprint_id,
        action_type=rule.action_type,
        content=action.content,
        tier=rule.tier,
        severity_hint=action.severity_hint,
    )
    store.enqueue_approval(item)
    logger.info(
        "Executor: queued action_type=%r for %s-tier review (item id=%s).",
        action.action_type,
        rule.tier.value,
        item.id,
    )
    return item
