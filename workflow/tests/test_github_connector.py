"""GitHub connector tests — all against mocked requests.get, never the
real API (slow, flaky, needs live credentials in CI)."""

from unittest.mock import Mock, patch

import requests

from workflow.ingest.github_connector import GitHubConnector, GitHubRateLimitedError

PR_RESPONSE = [
    {
        "number": 42,
        "title": "Fix payments retry bug",
        "html_url": "https://github.com/acme/widgets/pull/42",
        "user": {"login": "alice"},
        "created_at": "2020-01-01T00:00:00Z",  # ancient -> definitely stale
        "draft": False,
        "requested_reviewers": [],
    },
    {
        "number": 43,
        "title": "WIP: checkout redesign",
        "html_url": "https://github.com/acme/widgets/pull/43",
        "user": {"login": "bob"},
        "created_at": "2099-01-01T00:00:00Z",  # far future -> never stale
        "draft": True,
        "requested_reviewers": [],
    },
]

COMMIT_RESPONSE = [
    {
        "sha": "abc123",
        "html_url": "https://github.com/acme/widgets/commit/abc123",
        "commit": {"message": "Fix retry bug\n\nmore detail", "author": {"name": "alice"}},
    }
]


def _mock_response(json_data, status_code=200, headers=None):
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_data
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    else:
        response.raise_for_status.side_effect = None
    return response


def test_no_token_returns_empty_and_logs_warning(caplog):
    connector = GitHubConnector(repo="acme/widgets", token=None)
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()
    assert signals == []
    assert "GITHUB_TOKEN" in caplog.text


@patch("workflow.ingest.github_connector.requests.get")
def test_fetch_signals_produces_pr_opened_and_pr_stale(mock_get):
    mock_get.side_effect = [
        _mock_response(PR_RESPONSE),
        _mock_response(COMMIT_RESPONSE),
    ]
    connector = GitHubConnector(repo="acme/widgets", token="fake-token", stale_days=3)
    signals = connector.fetch_signals()

    kinds = [s.kind for s in signals]
    assert kinds.count("pr_opened") == 2  # both PRs are new on first fetch
    assert kinds.count("pr_stale") == 1  # only PR #42 is old enough
    assert kinds.count("commit_pushed") == 1

    stale = next(s for s in signals if s.kind == "pr_stale")
    assert stale.payload["number"] == 42
    assert stale.payload["age_days"] > 3


@patch("workflow.ingest.github_connector.requests.get")
def test_seen_prs_are_not_reannounced_as_opened(mock_get):
    mock_get.side_effect = [
        _mock_response(PR_RESPONSE),
        _mock_response(COMMIT_RESPONSE),
        _mock_response(PR_RESPONSE),
        _mock_response(COMMIT_RESPONSE),
    ]
    connector = GitHubConnector(repo="acme/widgets", token="fake-token")
    first = connector.fetch_signals()
    second = connector.fetch_signals()

    assert len([s for s in first if s.kind == "pr_opened"]) == 2
    assert len([s for s in second if s.kind == "pr_opened"]) == 0


@patch("workflow.ingest.github_connector.requests.get")
def test_rate_limited_response_returns_empty(mock_get, caplog):
    mock_get.return_value = _mock_response(
        {"message": "rate limited"},
        status_code=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12345"},
    )
    connector = GitHubConnector(repo="acme/widgets", token="fake-token")
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()
    assert signals == []
    assert "rate-limited" in caplog.text.lower()


@patch("workflow.ingest.github_connector.requests.get")
def test_network_failure_returns_empty(mock_get, caplog):
    mock_get.side_effect = requests.ConnectionError("boom")
    connector = GitHubConnector(repo="acme/widgets", token="fake-token")
    with caplog.at_level("WARNING"):
        signals = connector.fetch_signals()
    assert signals == []
    assert "request failed" in caplog.text.lower()
