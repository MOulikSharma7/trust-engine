"""Anthropic-backed LLMBackend. Requires ANTHROPIC_API_KEY."""

from __future__ import annotations

import os

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicBackend:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError("AnthropicBackend requires ANTHROPIC_API_KEY to be set.")
        self._client = Anthropic(api_key=resolved_key)
        self._model = model

    def generate(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
