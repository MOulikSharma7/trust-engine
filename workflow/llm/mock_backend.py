"""MockBackend — NOT one of the two required provider backends. This exists
solely so `python -m workflow.pipeline --once` can be run end-to-end
without any LLM API key configured (useful for local smoke-testing / a
demo environment with no credentials yet). It ignores the prompt and
returns a fixed, schema-valid DraftResult JSON string.

Selecting LLM_BACKEND=mock should be called out explicitly whenever it's
used — anyone reading pipeline output needs to know the "draft" wasn't
actually written by a model.
"""

from __future__ import annotations

import json

_CANNED_RESPONSE = json.dumps(
    {
        "status_summary": (
            "[MOCK BACKEND — not a real LLM response] Synthesized from recent "
            "GitHub and Slack signals for a smoke-test run."
        ),
        "candidate_actions": [
            {
                "action_type": "post_slack_message",
                "content": "Morning status: no blockers reported overnight, one PR opened.",
                "severity_hint": 1,
            }
        ],
    }
)


class MockBackend:
    def generate(self, prompt: str) -> str:
        return _CANNED_RESPONSE
