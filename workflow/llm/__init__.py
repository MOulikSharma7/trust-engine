"""Backend selection lives here, not in draft_agent.py — the draft agent
takes an already-constructed LLMBackend and never reads env vars itself.
"""

from __future__ import annotations

import os

from workflow.llm.base import LLMBackend

_KNOWN_BACKENDS = ("anthropic", "openai", "mock")


def build_backend_from_env() -> LLMBackend:
    """Reads LLM_BACKEND once and constructs the corresponding backend.
    Defaults to "anthropic" if unset. "mock" is a demo/test convenience,
    not a real provider — see workflow/llm/mock_backend.py.
    """
    backend_name = os.environ.get("LLM_BACKEND", "anthropic").strip().lower()

    if backend_name == "anthropic":
        from workflow.llm.anthropic_backend import AnthropicBackend

        return AnthropicBackend()
    if backend_name == "openai":
        from workflow.llm.openai_backend import OpenAIBackend

        return OpenAIBackend()
    if backend_name == "mock":
        from workflow.llm.mock_backend import MockBackend

        return MockBackend()

    raise ValueError(
        f"Unknown LLM_BACKEND={backend_name!r}; expected one of {_KNOWN_BACKENDS}"
    )


__all__ = ["LLMBackend", "build_backend_from_env"]
