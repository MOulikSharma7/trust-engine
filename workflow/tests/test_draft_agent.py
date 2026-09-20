"""Draft agent tests, against a mocked LLMBackend — no real LLM call."""

import json

from workflow.draft_agent import DraftAgent
from workflow.ingest.normalize import Signal

KNOWN_RULES = {
    "post_slack_message": 1,
    "flag_blocker_to_stakeholder": 3,
    "comment_on_payments_ticket": 4,
}


class FixedBackend:
    """Mocked LLMBackend that returns a scripted response, valid or not."""

    def __init__(self, response: str):
        self._response = response
        self.last_prompt = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._response


def _signal() -> Signal:
    from datetime import datetime, timezone

    return Signal(source="github", kind="pr_stale", payload={"number": 1}, detected_at=datetime.now(timezone.utc))


def test_valid_response_parses_into_candidate_actions():
    response = json.dumps(
        {
            "status_summary": "One PR is stale.",
            "candidate_actions": [
                {"action_type": "post_slack_message", "content": "PR #1 is stale.", "severity_hint": 1}
            ],
        }
    )
    agent = DraftAgent(FixedBackend(response))
    draft = agent.propose([_signal()], KNOWN_RULES)

    assert draft.status_summary == "One PR is stale."
    assert len(draft.candidate_actions) == 1
    assert draft.candidate_actions[0].action_type == "post_slack_message"


def test_unknown_action_type_is_dropped_not_routed(caplog):
    response = json.dumps(
        {
            "status_summary": "summary",
            "candidate_actions": [
                {"action_type": "delete_production_database", "content": "oops", "severity_hint": 1},
                {"action_type": "post_slack_message", "content": "fine", "severity_hint": 1},
            ],
        }
    )
    agent = DraftAgent(FixedBackend(response))
    with caplog.at_level("WARNING"):
        draft = agent.propose([_signal()], KNOWN_RULES)

    action_types = [a.action_type for a in draft.candidate_actions]
    assert "delete_production_database" not in action_types
    assert "post_slack_message" in action_types
    assert "no matching Rule" in caplog.text


def test_invalid_json_is_handled_gracefully_not_raised():
    agent = DraftAgent(FixedBackend("this is not json"))
    draft = agent.propose([_signal()], KNOWN_RULES)
    assert draft.candidate_actions == []


def test_schema_violation_is_handled_gracefully():
    # severity_hint out of the 1-5 range should fail pydantic validation.
    response = json.dumps(
        {
            "status_summary": "summary",
            "candidate_actions": [
                {"action_type": "post_slack_message", "content": "x", "severity_hint": 99}
            ],
        }
    )
    agent = DraftAgent(FixedBackend(response))
    draft = agent.propose([_signal()], KNOWN_RULES)
    assert draft.candidate_actions == []


def test_severity_hint_mismatch_is_logged_but_action_still_included(caplog):
    # configured severity for comment_on_payments_ticket is 4; hint of 1 is a
    # meaningful disagreement (>= SEVERITY_HINT_MISMATCH_THRESHOLD).
    response = json.dumps(
        {
            "status_summary": "summary",
            "candidate_actions": [
                {"action_type": "comment_on_payments_ticket", "content": "x", "severity_hint": 1}
            ],
        }
    )
    agent = DraftAgent(FixedBackend(response))
    with caplog.at_level("WARNING"):
        draft = agent.propose([_signal()], KNOWN_RULES)

    assert len(draft.candidate_actions) == 1  # not dropped — hint is informational only
    assert draft.candidate_actions[0].severity_hint == 1  # hint itself is unchanged
    assert "disagrees with its configured severity" in caplog.text


def test_prompt_lists_only_known_action_types():
    agent = DraftAgent(FixedBackend(json.dumps({"status_summary": "", "candidate_actions": []})))
    backend = agent._backend
    agent.propose([_signal()], KNOWN_RULES)
    for action_type in KNOWN_RULES:
        assert action_type in backend.last_prompt
    assert "delete_production_database" not in backend.last_prompt
