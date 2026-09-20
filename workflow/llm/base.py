"""The interface the draft agent depends on. It knows nothing about
Anthropic, OpenAI, or any other provider — only this Protocol.
"""

from __future__ import annotations

from typing import Protocol


class LLMBackend(Protocol):
    def generate(self, prompt: str) -> str: ...
