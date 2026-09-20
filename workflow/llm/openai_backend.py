"""OpenAI-backed LLMBackend. Requires OPENAI_API_KEY."""

from __future__ import annotations

import os

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIBackend:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        resolved_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise RuntimeError("OpenAIBackend requires OPENAI_API_KEY to be set.")
        self._client = OpenAI(api_key=resolved_key)
        self._model = model

    def generate(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
