"""Executor tests: Auto-tier candidates execute (real or simulated) and
record an outcome; Approve/Human-tier candidates land in the approval
queue, are not executed, and record no outcome until later resolution."""

from unittest.mock import Mock, patch

import pytest

from trust_engine.models import Rule, Tier
from trust_engine.store import ApprovalQueueItem, SqliteRuleStore

from workflow.draft_agent import CandidateAction
from workflow.executor import execute_candidate


def make_store_and_rule(tier: Tier, action_type: str, severity: int) -> tuple:
    store = SqliteRuleStore(":memory:")
    rule = Rule(blueprint_id="morning-status", action_type=action_type, severity=severity, tier=tier)
    store.save_rule(rule)
    return store, rule


def test_auto_tier_simulated_action_executes_and_records_outcome(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    store, rule = make_store_and_rule(Tier.AUTO, "comment_on_payments_ticket", 4)
    action = CandidateAction(action_type="comment_on_payments_ticket", content="ping the customer", severity_hint=4)
    original_alpha = rule.alpha  # record_outcome mutates `rule` in place, so snapshot first

    result = execute_candidate(action, rule, store)

    assert result.executed is True
    assert result.simulated is True  # comment_on_payments_ticket has no real side effect wired up
    outcomes = store.list_outcomes(rule.blueprint_id, rule.action_type)
    assert len(outcomes) == 1
    updated_rule = store.get_rule(rule.blueprint_id, rule.action_type)
    assert updated_rule.alpha > original_alpha  # trust incremented
    assert store.list_approval_queue(status=None) == []


def test_auto_tier_post_slack_message_without_token_falls_back_to_simulated(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    store, rule = make_store_and_rule(Tier.AUTO, "post_slack_message", 1)
    action = CandidateAction(action_type="post_slack_message", content="status update", severity_hint=1)

    result = execute_candidate(action, rule, store, slack_channel="#standup")

    assert result.executed is True
    assert result.simulated is True
    assert "SLACK_BOT_TOKEN" in result.detail
    assert len(store.list_outcomes(rule.blueprint_id, rule.action_type)) == 1


@patch("workflow.executor.requests.post")
def test_auto_tier_post_slack_message_with_token_posts_for_real(mock_post, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "fake-token")
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = None
    mock_response.json.return_value = {"ok": True}
    mock_post.return_value = mock_response

    store, rule = make_store_and_rule(Tier.AUTO, "post_slack_message", 1)
    action = CandidateAction(action_type="post_slack_message", content="status update", severity_hint=1)

    result = execute_candidate(action, rule, store, slack_channel="#standup")

    assert result.executed is True
    assert result.simulated is False  # this action type is real, and a token was available
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["channel"] == "#standup"
    assert kwargs["json"]["text"] == "status update"
    assert len(store.list_outcomes(rule.blueprint_id, rule.action_type)) == 1


@patch("workflow.executor.requests.post")
def test_auto_tier_real_execution_failure_does_not_record_outcome(mock_post, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "fake-token")
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = None
    mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}
    mock_post.return_value = mock_response

    store, rule = make_store_and_rule(Tier.AUTO, "post_slack_message", 1)
    action = CandidateAction(action_type="post_slack_message", content="status update", severity_hint=1)

    result = execute_candidate(action, rule, store, slack_channel="#standup")

    assert result.executed is False
    assert store.list_outcomes(rule.blueprint_id, rule.action_type) == []
    unchanged_rule = store.get_rule(rule.blueprint_id, rule.action_type)
    assert unchanged_rule.alpha == rule.alpha  # nothing recorded — belief untouched


@pytest.mark.parametrize("tier", [Tier.APPROVE, Tier.HUMAN])
def test_approve_and_human_tier_enqueue_without_executing_or_recording(tier):
    store, rule = make_store_and_rule(tier, "reschedule_delivery_date", 5)
    action = CandidateAction(action_type="reschedule_delivery_date", content="push to next week", severity_hint=5)

    result = execute_candidate(action, rule, store)

    assert isinstance(result, ApprovalQueueItem)
    assert result.tier == tier
    assert result.status == "pending"

    queued = store.list_approval_queue(status="pending")
    assert len(queued) == 1
    assert queued[0].action_type == "reschedule_delivery_date"

    assert store.list_outcomes(rule.blueprint_id, rule.action_type) == []
    unchanged_rule = store.get_rule(rule.blueprint_id, rule.action_type)
    assert unchanged_rule.alpha == rule.alpha  # outcome deferred until human resolution
