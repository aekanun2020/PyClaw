"""Tests for streaming LLM responses (the 'streaming replies' stage).

We don't hit the network: httpx.MockTransport feeds a canned SSE body so the
SSE parser + delta assembler are tested deterministically.
"""
from __future__ import annotations

import httpx
import pytest

from pyclaw.core.llm import OpenRouterProvider


def _sse(*lines: str) -> bytes:
    return ("".join(f"{ln}\n" for ln in lines)).encode("utf-8")


def _provider_with(transport: httpx.MockTransport) -> OpenRouterProvider:
    p = OpenRouterProvider(api_key="sk-test", base_url="https://x.test", model="m")
    # Route httpx.stream through the mock transport.
    import pyclaw.core.llm as llm_mod

    real_stream = httpx.stream

    def fake_stream(method, url, **kwargs):  # noqa: ANN001
        client = httpx.Client(transport=transport)
        return client.stream(method, url, **kwargs)

    llm_mod.httpx.stream = fake_stream  # type: ignore[attr-defined]
    p._restore = lambda: setattr(llm_mod.httpx, "stream", real_stream)  # type: ignore[attr-defined]
    return p


def test_iter_sse_deltas_skips_done_and_garbage() -> None:
    lines = iter([
        "data: {\"a\": 1}",
        "",                       # blank — skipped
        ": keep-alive",           # comment — skipped
        "data: not-json",         # malformed — skipped, no crash
        "data: {\"b\": 2}",
        "data: [DONE]",           # sentinel — skipped
    ])
    out = list(OpenRouterProvider._iter_sse_deltas(lines))
    assert out == [{"a": 1}, {"b": 2}]


def test_stream_text_deltas_assembled_and_emitted() -> None:
    body = _sse(
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    p = _provider_with(transport)
    seen: list[str] = []
    try:
        resp = p.complete_stream([{"role": "user", "content": "hi"}], on_delta=seen.append)
    finally:
        p._restore()  # type: ignore[attr-defined]

    assert resp.text == "Hello world"
    assert seen == ["Hel", "lo", " world"]          # streamed in order
    assert resp.tool_calls == []


def test_stream_assembles_fragmented_tool_call() -> None:
    body = _sse(
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"db_query"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"sql\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"SELECT 1\\"}"}}]}}]}',
        "data: [DONE]",
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    p = _provider_with(transport)
    try:
        resp = p.complete_stream([{"role": "user", "content": "hi"}])
    finally:
        p._restore()  # type: ignore[attr-defined]

    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "c1"
    assert tc.name == "db_query"
    assert tc.arguments == {"sql": "SELECT 1"}


def test_stream_http_error_fails_loudly() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(500, content=b"boom"))
    p = _provider_with(transport)
    try:
        with pytest.raises(RuntimeError, match="500"):
            p.complete_stream([{"role": "user", "content": "hi"}])
    finally:
        p._restore()  # type: ignore[attr-defined]


def test_stream_missing_key_fails_loudly() -> None:
    p = OpenRouterProvider(api_key="", base_url="https://x.test", model="m")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        p.complete_stream([{"role": "user", "content": "hi"}])
