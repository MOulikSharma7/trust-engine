"""Wires the real end-to-end path together: GitHub + Slack signals ->
draft agent -> Trust Engine routing (via each candidate's own Rule tier,
read straight from the store) -> executor.

Two agent roles only — ingest (the two connectors) and draft. This module
adds no third agent role; it is pure orchestration and contains no
trust-scoring or drafting logic of its own.

Run once:                python -m workflow.pipeline --once
Run on a loop (hourly):   python -m workflow.pipeline --loop --interval=3600
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from trust_engine.models import Rule
from trust_engine.store import RuleStore, SqliteRuleStore

# DEFAULT_RULES is the canonical (action_type, severity) registry for the
# "morning-status" blueprint, established in Milestone 2. Imported
# read-only here rather than duplicated, so the real pipeline and the
# simulation harness can never drift out of sync on which action types
# exist and what their severity is.
from simulation.replay import DEFAULT_RULES

from workflow.draft_agent import DraftAgent, DraftResult
from workflow.executor import execute_candidate
from workflow.ingest.github_connector import GitHubConnector
from workflow.ingest.normalize import Signal
from workflow.ingest.slack_connector import SlackConnector
from workflow.llm import build_backend_from_env

logger = logging.getLogger(__name__)

BLUEPRINT_ID = "morning-status"
DEFAULT_SLACK_CHANNEL = "#standup"


def ensure_default_rules(store: RuleStore, blueprint_id: str = BLUEPRINT_ID) -> None:
    """Seeds the canonical rule set the first time this blueprint is seen.
    Never overwrites an existing Rule — that would wipe accumulated trust
    on every run, which is exactly the bug this guards against.
    """
    for _, action_type, severity in DEFAULT_RULES:
        if store.get_rule(blueprint_id, action_type) is None:
            store.save_rule(Rule(blueprint_id=blueprint_id, action_type=action_type, severity=severity))


def process_signals(
    store: RuleStore,
    signals: list[Signal],
    blueprint_id: str = BLUEPRINT_ID,
    slack_channel: str = DEFAULT_SLACK_CHANNEL,
) -> dict:
    """The draft -> route -> execute stage, decoupled from where the
    signals came from. run_once() below feeds it real connector output;
    the API's POST /signals endpoint (Milestone 4) feeds it a single
    externally-submitted signal — same logic either way, no duplication.
    """
    ensure_default_rules(store, blueprint_id)

    known_rules = {
        rule.action_type: rule.severity
        for rule in store.list_rules()
        if rule.blueprint_id == blueprint_id
    }

    backend = build_backend_from_env()
    draft: DraftResult = DraftAgent(backend).propose(signals, known_rules)

    routed = []
    for action in draft.candidate_actions:
        rule = store.get_rule(blueprint_id, action.action_type)
        outcome = execute_candidate(action, rule, store, slack_channel=slack_channel)
        routed.append({"action": action, "tier": rule.tier, "outcome": outcome})

    logger.info(
        "Pipeline: %d signal(s) -> %d candidate action(s) -> %d routed.",
        len(signals),
        len(draft.candidate_actions),
        len(routed),
    )
    return {"signals": signals, "draft": draft, "routed": routed}


def run_once(
    store: RuleStore,
    github_repo: str | None = None,
    slack_channel: str = DEFAULT_SLACK_CHANNEL,
    blueprint_id: str = BLUEPRINT_ID,
) -> dict:
    signals: list[Signal] = []
    if github_repo:
        signals += GitHubConnector(repo=github_repo).fetch_signals()
    else:
        logger.info("Pipeline: no GitHub repo configured — skipping GitHub ingest.")
    signals += SlackConnector(channel=slack_channel).fetch_signals()

    return process_signals(store, signals, blueprint_id=blueprint_id, slack_channel=slack_channel)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Trust Engine workflow pipeline")
    parser.add_argument("--once", action="store_true", help="Run a single pass (default if --loop omitted)")
    parser.add_argument("--loop", action="store_true", help="Run repeatedly every --interval seconds")
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--github-repo", default=os.environ.get("GITHUB_REPO"), help="owner/name; omit to skip GitHub ingest")
    parser.add_argument("--slack-channel", default=os.environ.get("SLACK_CHANNEL", DEFAULT_SLACK_CHANNEL))
    parser.add_argument("--db-path", default=None, help="Defaults to trust_engine/local.db")
    args = parser.parse_args()

    store = SqliteRuleStore(args.db_path) if args.db_path else SqliteRuleStore()

    if args.loop:
        while True:
            run_once(store, github_repo=args.github_repo, slack_channel=args.slack_channel)
            time.sleep(args.interval)
    else:
        run_once(store, github_repo=args.github_repo, slack_channel=args.slack_channel)


if __name__ == "__main__":
    main()
