"""Slack connector: pulls recent messages from a configured channel via the
Slack Web API, normalized into `standup_message` Signals.

Requires SLACK_BOT_TOKEN. A failed API call is treated as "nothing to
report this run" — log and return an empty list, never raise.

FIXTURE MODE: if no SLACK_BOT_TOKEN is set, this connector reads from the
bundled fixture file (fixtures/slack_messages.json) of sample standup
messages instead of calling the real Slack API. This is a deliberate,
clearly-flagged stand-in for demoing/testing without a real Slack app
configured yet — every signal produced this way carries
payload["fixture"] = True so downstream code (and anyone reading the
demo output) can tell live data from fixture data. Exporting
SLACK_BOT_TOKEN switches to the real API; no code change needed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from workflow.ingest.normalize import Signal

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_MESSAGE_LIMIT = 20
DEFAULT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "slack_messages.json"


class SlackConnector:
    def __init__(
        self,
        channel: str,
        token: str | None = None,
        message_limit: int = DEFAULT_MESSAGE_LIMIT,
        fixture_path: Path = DEFAULT_FIXTURE_PATH,
    ):
        self._channel = channel
        self._token = token if token is not None else os.environ.get("SLACK_BOT_TOKEN")
        self._message_limit = message_limit
        self._fixture_path = fixture_path

    def fetch_signals(self) -> list[Signal]:
        if not self._token:
            logger.warning(
                "Slack connector: SLACK_BOT_TOKEN is not set — using bundled "
                "FIXTURE messages (%s), NOT the real Slack API. Export "
                "SLACK_BOT_TOKEN to switch to live data.",
                self._fixture_path,
            )
            return self._fetch_from_fixture()

        try:
            messages = self._fetch_from_api()
        except requests.RequestException as exc:
            logger.warning("Slack connector: request failed (%s) — returning no signals.", exc)
            return []

        if messages is None:  # API responded but reported an error (e.g. bad auth, rate-limited)
            return []

        return self._to_signals(messages, is_fixture=False)

    def _fetch_from_api(self) -> list[dict] | None:
        response = requests.get(
            f"{SLACK_API_BASE}/conversations.history",
            headers={"Authorization": f"Bearer {self._token}"},
            params={"channel": self._channel, "limit": self._message_limit},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            logger.warning("Slack connector: API error (%s) — returning no signals.", data.get("error"))
            return None
        return data.get("messages", [])

    def _fetch_from_fixture(self) -> list[Signal]:
        if not self._fixture_path.exists():
            logger.warning(
                "Slack connector: fixture file %s not found — returning no signals.",
                self._fixture_path,
            )
            return []
        with open(self._fixture_path, encoding="utf-8") as f:
            messages = json.load(f)
        return self._to_signals(messages, is_fixture=True)

    def _to_signals(self, messages: list[dict], is_fixture: bool) -> list[Signal]:
        now = datetime.now(timezone.utc)
        return [
            Signal(
                source="slack",
                kind="standup_message",
                payload={
                    "channel": self._channel,
                    "user": message.get("user"),
                    "text": message.get("text", ""),
                    "ts": message.get("ts"),
                    "fixture": is_fixture,
                },
                detected_at=now,
            )
            for message in messages
        ]
