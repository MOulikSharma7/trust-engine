"""API tests, against an isolated in-memory store via dependency_overrides
— never the real on-disk local.db."""

import pytest
from fastapi.testclient import TestClient

from trust_engine.models import Rule, Tier
from trust_engine.store import ApprovalQueueItem, SqliteRuleStore

from api.dependencies import get_store
from api.main import app


@pytest.fixture
def store():
    s = SqliteRuleStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def client(store):
    def override_get_store():
        yield store

    app.dependency_overrides[get_store] = override_get_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_rules_empty_initially(client):
    response = client.get("/rules")
    assert response.status_code == 200
    assert response.json() == []


def test_list_rules_includes_confidence_bound(client, store):
    store.save_rule(Rule(blueprint_id="morning-status", action_type="post_slack_message", severity=1))

    response = client.get("/rules")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["action_type"] == "post_slack_message"
    assert 0.0 <= body[0]["confidence_bound"] <= 1.0


def test_ingest_signal_seeds_rules_and_routes_via_mock_llm(client, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "mock")

    response = client.post("/signals", json={"source": "slack", "kind": "standup_message", "payload": {"text": "hi"}})
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_actions_proposed"] >= 1
    assert len(body["routed"]) >= 1
    # Fresh store: post_slack_message starts at HUMAN, so it's queued, not executed.
    assert body["routed"][0]["routing"] == "queued"

    queued = client.get("/approval-queue").json()
    assert len(queued) == 1


def test_ingest_signal_without_llm_credentials_returns_clean_503(client, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post("/signals", json={"source": "slack", "kind": "standup_message", "payload": {}})
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_resolve_approve_tier_item_records_outcome_and_increments_trust(client, store):
    store.save_rule(
        Rule(blueprint_id="morning-status", action_type="flag_blocker_to_stakeholder", severity=3, tier=Tier.APPROVE)
    )
    item = ApprovalQueueItem(
        blueprint_id="morning-status",
        action_type="flag_blocker_to_stakeholder",
        content="Heads up, blocker on X",
        tier=Tier.APPROVE,
        severity_hint=3,
    )
    store.enqueue_approval(item)

    response = client.post(f"/approval-queue/{item.id}/resolve", json={"decision": "approved_unchanged"})
    assert response.status_code == 200
    assert response.json()["status"] == "approved_unchanged"

    assert client.get("/approval-queue").json() == []  # no longer pending
    outcomes = store.list_outcomes("morning-status", "flag_blocker_to_stakeholder")
    assert len(outcomes) == 1

    rule = store.get_rule("morning-status", "flag_blocker_to_stakeholder")
    assert rule.alpha > 1.0  # trust incremented only now, on resolution — not at enqueue time


def test_resolve_with_edits_computes_edit_distance(client, store):
    store.save_rule(
        Rule(blueprint_id="morning-status", action_type="post_slack_message", severity=1, tier=Tier.APPROVE)
    )
    item = ApprovalQueueItem(
        blueprint_id="morning-status",
        action_type="post_slack_message",
        content="Original draft content",
        tier=Tier.APPROVE,
        severity_hint=1,
    )
    store.enqueue_approval(item)

    response = client.post(
        f"/approval-queue/{item.id}/resolve",
        json={"decision": "approved_with_edits", "edited_content": "Something totally unrelated here"},
    )
    assert response.status_code == 200

    outcomes = store.list_outcomes("morning-status", "post_slack_message")
    assert outcomes[0].edit_distance > 0.0


def test_resolve_unknown_item_returns_404(client):
    response = client.post("/approval-queue/does-not-exist/resolve", json={"decision": "rejected"})
    assert response.status_code == 404


def test_resolve_already_resolved_item_returns_409(client, store):
    store.save_rule(
        Rule(blueprint_id="morning-status", action_type="post_slack_message", severity=1, tier=Tier.APPROVE)
    )
    item = ApprovalQueueItem(
        blueprint_id="morning-status", action_type="post_slack_message", content="x", tier=Tier.APPROVE, severity_hint=1
    )
    store.enqueue_approval(item)
    client.post(f"/approval-queue/{item.id}/resolve", json={"decision": "rejected"})

    response = client.post(f"/approval-queue/{item.id}/resolve", json={"decision": "rejected"})
    assert response.status_code == 409


def test_resolve_with_edits_requires_edited_content(client, store):
    store.save_rule(
        Rule(blueprint_id="morning-status", action_type="post_slack_message", severity=1, tier=Tier.APPROVE)
    )
    item = ApprovalQueueItem(
        blueprint_id="morning-status", action_type="post_slack_message", content="x", tier=Tier.APPROVE, severity_hint=1
    )
    store.enqueue_approval(item)

    response = client.post(f"/approval-queue/{item.id}/resolve", json={"decision": "approved_with_edits"})
    assert response.status_code == 422


def test_run_simulation_returns_metrics_and_plot_url(client):
    response = client.post("/simulation/run", json={"profile": "late_catch", "days": 90, "seed": 6})
    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["false_promotion_rate"] <= 1.0
    assert body["plot_url"].startswith("/static/simulation-output/")


def test_dashboard_serves_index_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
