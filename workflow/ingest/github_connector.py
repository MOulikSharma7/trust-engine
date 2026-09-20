"""GitHub connector: pulls open PRs (age + review status) and recent commits
on the default branch for a configured repo, normalized into Signals.

Requires GITHUB_TOKEN. A missing token or a failed/rate-limited API call are
both treated as "nothing to report this run" — log a clear message and
return an empty list, rather than raising, so the pipeline (and a live
demo) survives imperfect credentials/network conditions.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import requests

from workflow.ingest.normalize import Signal

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_STALE_DAYS = 3
DEFAULT_RECENT_COMMIT_LIMIT = 5
REQUEST_TIMEOUT_SECONDS = 10


class GitHubRateLimitedError(Exception):
    """Raised when GitHub's API responds with a rate-limit error."""


class GitHubConnector:
    def __init__(
        self,
        repo: str,  # "owner/name"
        token: str | None = None,
        stale_days: int = DEFAULT_STALE_DAYS,
        recent_commit_limit: int = DEFAULT_RECENT_COMMIT_LIMIT,
        seen_pr_numbers: set[int] | None = None,
    ):
        self._repo = repo
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._stale_days = stale_days
        self._recent_commit_limit = recent_commit_limit
        # Tracks PR numbers already surfaced as pr_opened, so a repeated
        # poll doesn't re-announce the same PR as "new" every run.
        self._seen_pr_numbers = seen_pr_numbers if seen_pr_numbers is not None else set()

    def fetch_signals(self) -> list[Signal]:
        if not self._token:
            logger.warning(
                "GitHub connector: GITHUB_TOKEN is not set — skipping GitHub "
                "ingest for this run (returning no signals)."
            )
            return []

        try:
            pulls = self._get(f"/repos/{self._repo}/pulls", params={"state": "open", "per_page": 50})
            commits = self._get(
                f"/repos/{self._repo}/commits",
                params={"per_page": self._recent_commit_limit},
            )
        except GitHubRateLimitedError as exc:
            logger.warning("GitHub connector: rate-limited (%s) — returning no signals.", exc)
            return []
        except requests.RequestException as exc:
            logger.warning("GitHub connector: request failed (%s) — returning no signals.", exc)
            return []

        now = datetime.now(timezone.utc)
        signals = self._pr_signals(pulls, now) + self._commit_signals(commits, now)
        return signals

    def _pr_signals(self, pulls: list[dict], now: datetime) -> list[Signal]:
        signals: list[Signal] = []
        for pr in pulls:
            number = pr["number"]
            opened_at = _parse_github_timestamp(pr["created_at"])
            age_days = (now - opened_at).days

            if number not in self._seen_pr_numbers:
                signals.append(
                    Signal(
                        source="github",
                        kind="pr_opened",
                        payload={
                            "repo": self._repo,
                            "number": number,
                            "title": pr["title"],
                            "url": pr["html_url"],
                            "author": pr["user"]["login"],
                        },
                        detected_at=now,
                    )
                )
                self._seen_pr_numbers.add(number)

            if age_days >= self._stale_days:
                signals.append(
                    Signal(
                        source="github",
                        kind="pr_stale",
                        payload={
                            "repo": self._repo,
                            "number": number,
                            "title": pr["title"],
                            "url": pr["html_url"],
                            "age_days": age_days,
                            "review_status": _review_status(pr),
                        },
                        detected_at=now,
                    )
                )
        return signals

    def _commit_signals(self, commits: list[dict], now: datetime) -> list[Signal]:
        signals = []
        for commit in commits:
            info = commit.get("commit", {})
            signals.append(
                Signal(
                    source="github",
                    kind="commit_pushed",
                    payload={
                        "repo": self._repo,
                        "sha": commit.get("sha"),
                        "message": info.get("message", "").splitlines()[0] if info.get("message") else "",
                        "author": info.get("author", {}).get("name"),
                        "url": commit.get("html_url"),
                    },
                    detected_at=now,
                )
            )
        return signals

    def _get(self, path: str, params: dict | None = None) -> list[dict]:
        response = requests.get(
            f"{GITHUB_API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in (403, 429) and response.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubRateLimitedError(
                f"rate limit exceeded, resets at {response.headers.get('X-RateLimit-Reset')}"
            )
        response.raise_for_status()
        return response.json()


def _review_status(pr: dict) -> str:
    # The list-PRs endpoint doesn't include full review state; `draft` and
    # `requested_reviewers` give a cheap approximation without an extra
    # API call per PR.
    if pr.get("draft"):
        return "draft"
    if pr.get("requested_reviewers"):
        return "review_requested"
    return "awaiting_review"


def _parse_github_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
