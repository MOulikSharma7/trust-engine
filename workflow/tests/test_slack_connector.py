"""Slack connector tests — mocked API responses only; the no-token path
exercises the bundled fixture rather than any network call."""

from unittest.mock import Mock, patch

import requests

from workflow.ingest.slack_connector import SlackConnector


def _mock_response(json_data, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status.side_effect = None
    return response


def test_no_token_falls_back_to_fixture_and_flags_it(caplog):
    connector = SlackConnector(channel="#standup", token=None)
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()

    assert len(signals) > 0
    assert all(s.kind == "standup_message" for s in signals)
    assert all(s.payload["fixture"] is True for s in signals)
    assert "FIXTURE" in caplog.text


@patch("workflow.ingest.slack_connector.requests.get")
def test_with_token_calls_real_api_and_does_not_flag_fixture(mock_get):
    mock_get.return_value = _mock_response(
        {"ok": True, "messages": [{"user": "U1", "text": "hello", "ts": "123.456"}]}
    )
    connector = SlackConnector(channel="#standup", token="fake-token")
    signals = connector.fetch_signals()

    assert len(signals) == 1
    assert signals[0].payload["fixture"] is False
    assert signals[0].payload["text"] == "hello"
    mock_get.assert_called_once()


@patch("workflow.ingest.slack_connector.requests.get")
def test_api_error_response_returns_empty(mock_get, caplog):
    mock_get.return_value = _mock_response({"ok": False, "error": "invalid_auth"})
    connector = SlackConnector(channel="#standup", token="fake-token")
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()
    assert signals == []
    assert "invalid_auth" in caplog.text


@patch("workflow.ingest.slack_connector.requests.get")
def test_network_failure_returns_empty(mock_get, caplog):
    mock_get.side_effect = requests.ConnectionError("boom")
    connector = SlackConnector(channel="#standup", token="fake-token")
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()
    assert signals == []
    assert "request failed" in caplog.text.lower()


def test_missing_fixture_file_returns_empty(tmp_path, caplog):
    connector = SlackConnector(channel="#standup", token=None, fixture_path=tmp_path / "nope.json")
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()
    assert signals == []
    assert "not found" in caplog.text.lower()
