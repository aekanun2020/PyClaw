"""Core — LLM provider (OpenRouter), same backend as EliteClaw.

Ported from EliteClaw's `openrouter-client.ts`: OpenAI-compatible
`/chat/completions`, tool calling, retry + exponential backoff, 429 handling,
request timeout, and local/Ollama mode (api_key in {"ollama","none","local"}
sends no Authorization header).

Note on determinism (principle #1): even with temperature=0 and a fixed seed,
hosted LLM inference is not bit-reproducible (floating-point non-associativity,
dynamic batching changes reduction order, autoregressive decoding amplifies
tiny diffs). Therefore PyClaw never relies on the model for guarantees — those
live in Hooks. For reproducible *self-hosted* inference, configure the server:
  vLLM:   VLLM_BATCH_INVARIANT=1
  SGLang: --enable-deterministic-inference
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from pyclaw.config import SETTINGS

# api_key values that mean "local server, no auth header" (EliteClaw behaviour).
_LOCAL_KEYS = frozenset({"ollama", "none", "local", ""})


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
    max_tokens: int = 4096
    timeout: float = 60.0
    max_retries: int = 3
    app_name: str = "pyclaw-agent"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """POST to OpenRouter /chat/completions and parse tool calls.

        Fails loudly (principle #6) on missing key or non-2xx (after retries).
        """
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set (fail loudly, principle #6)")

        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "seed": 0,  # best-effort determinism; see module docstring
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        data = self._make_request("/chat/completions", body)
        return self._parse(data)

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Like `complete`, but streams text deltas via Server-Sent Events.

        Each text chunk is passed to `on_delta` as it arrives (this is the
        "streaming replies" stage of the agentic loop). Tool-call deltas are
        accumulated and assembled into the final LLMResponse, so the agent loop
        behaves identically to the non-streaming path once the turn completes.

        Fails loudly on missing key / non-2xx, same as `complete`.
        """
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set (fail loudly, principle #6)")

        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "seed": 0,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        return self._stream_request("/chat/completions", body, on_delta)

    # -- internals ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Send Authorization only for real keys (mirrors EliteClaw local mode).
        if self.api_key not in _LOCAL_KEYS:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["HTTP-Referer"] = "https://github.com/aekanun2020/PyClaw"
            headers["X-Title"] = self.app_name
        return headers

    def _make_request(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{endpoint}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = httpx.post(
                    url, headers=self._headers(), json=body, timeout=self.timeout
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    f"OpenRouter request timed out after {self.timeout}s"
                ) from exc
            except httpx.HTTPError as exc:  # network error -> retry with backoff
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 10))
                    continue
                raise RuntimeError(f"OpenRouter network error: {exc}") from exc

            # Rate limited -> back off and retry (honour Retry-After).
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "5") or "5")
                if attempt < self.max_retries:
                    time.sleep(min(retry_after, 30))
                    continue

            if resp.status_code >= 400:
                # fail loudly (principle #6)
                raise RuntimeError(
                    f"OpenRouter API error {resp.status_code}: {resp.text[:500]}"
                )

            return resp.json()

        # Exhausted retries on network errors.
        raise RuntimeError(f"OpenRouter request failed: {last_error}")

    def _stream_request(
        self,
        endpoint: str,
        body: dict[str, Any],
        on_delta: Callable[[str], None] | None,
    ) -> LLMResponse:
        """POST with stream=True and assemble deltas into one LLMResponse."""
        url = f"{self.base_url.rstrip('/')}{endpoint}"
        text_parts: list[str] = []
        # tool calls arrive in fragments keyed by index; accumulate per index.
        tool_acc: dict[int, dict[str, Any]] = {}

        try:
            with httpx.stream(
                "POST", url, headers=self._headers(), json=body, timeout=self.timeout
            ) as resp:
                if resp.status_code >= 400:
                    detail = resp.read().decode("utf-8", "replace")[:500]
                    raise RuntimeError(
                        f"OpenRouter API error {resp.status_code}: {detail}"
                    )
                for chunk in self._iter_sse_deltas(resp.iter_lines()):
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    piece = delta.get("content")
                    if piece:
                        text_parts.append(piece)
                        if on_delta is not None:
                            on_delta(piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"OpenRouter request timed out after {self.timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenRouter network error: {exc}") from exc

        tool_calls: list[ToolCall] = []
        for _idx in sorted(tool_acc):
            slot = tool_acc[_idx]
            raw_args = slot["arguments"] or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {"_raw": raw_args}
            tool_calls.append(
                ToolCall(id=slot["id"], name=slot["name"], arguments=parsed_args)
            )

        return LLMResponse(text="".join(text_parts), tool_calls=tool_calls)

    @staticmethod
    def _iter_sse_deltas(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
        """Yield parsed JSON objects from an OpenAI-style SSE `data:` stream.

        Skips comments/keep-alives and the terminal `data: [DONE]` sentinel.
        Malformed fragments are ignored (a partial line never crashes the chat).
        """
        for line in lines:
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]" or not payload:
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue

    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMResponse:
        """Parse an OpenAI-compatible response into LLMResponse."""
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        text = message.get("content") or ""

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {"_raw": raw_args}
            tool_calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=parsed_args)
            )

        return LLMResponse(text=text, tool_calls=tool_calls, raw=data)
