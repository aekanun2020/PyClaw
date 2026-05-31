"""Core — LLM provider (OpenRouter), same backend as EliteClaw.

Note on determinism (principle #1): even with temperature=0 and a fixed seed,
hosted LLM inference is not bit-reproducible (floating-point non-associativity,
dynamic batching changes reduction order, autoregressive decoding amplifies
tiny diffs). Therefore PyClaw never relies on the model for guarantees — those
live in Hooks. For reproducible *self-hosted* inference, configure the server:
  vLLM:   VLLM_BATCH_INVARIANT=1
  SGLang: --enable-deterministic-inference
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyclaw.config import SETTINGS


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpenRouterProvider:
    api_key: str = field(default_factory=lambda: SETTINGS.openrouter_api_key)
    base_url: str = field(default_factory=lambda: SETTINGS.openrouter_base_url)
    model: str = field(default_factory=lambda: SETTINGS.default_model)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """POST to OpenRouter /chat/completions and parse tool calls.

        TODO:
          - httpx.post(f"{base_url}/chat/completions",
              headers={"Authorization": f"Bearer {api_key}"},
              json={"model": model or self.model, "messages": messages,
                    "tools": tools, "temperature": temperature, "seed": 0})
          - parse choices[0].message: content + tool_calls -> LLMResponse
          - fail loudly on non-2xx (principle #6)
        """
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set (fail loudly, principle #6)")
        raise NotImplementedError("OpenRouterProvider.complete (scaffold)")
