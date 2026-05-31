"""Tests for the MCP client (JSON-RPC over an injected transport) + config loader."""
from __future__ import annotations

import pytest

from pyclaw.mcp.client import (
    McpClient,
    McpError,
    McpServerConfig,
    Transport,
    load_server_configs,
)


class FakePoster:
    """Records requests and replies from a scripted method->result table."""

    def __init__(self, table, fail_transports=()):
        self.table = table
        self.fail_transports = set(fail_transports)
        self.calls = []

    def __call__(self, url, request, headers, transport):
        self.calls.append((request["method"], transport))
        if transport in self.fail_transports:
            raise RuntimeError(f"transport {transport} down")
        method = request["method"]
        if "id" not in request:  # notification — no response body needed
            return {}
        if method not in self.table:
            return {"jsonrpc": "2.0", "id": request["id"], "result": {}}
        return {"jsonrpc": "2.0", "id": request["id"], "result": self.table[method]}


def _client(table, **kw):
    cfg = McpServerConfig(name="t", url="http://x/mcp", **kw)
    return McpClient(config=cfg, poster=FakePoster(table, kw.pop("fail_transports", ())))


def test_connect_and_list_tools():
    poster = FakePoster({
        "initialize": {"protocolVersion": "2024-11-05", "capabilities": {}},
        "tools/list": {"tools": [
            {"name": "search", "description": "search docs", "inputSchema": {"type": "object"}},
        ]},
    })
    client = McpClient(config=McpServerConfig(name="docs", url="http://x"), poster=poster)
    client.connect()
    tools = client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "search" and tools[0].server == "docs"
    # initialize happened before list
    assert poster.calls[0][0] == "initialize"


def test_call_tool_returns_result():
    poster = FakePoster({
        "initialize": {"capabilities": {}},
        "tools/call": {"content": [{"type": "text", "text": "42"}]},
    })
    client = McpClient(config=McpServerConfig(name="t", url="http://x"), poster=poster)
    client.connect()
    out = client.call_tool("calc", {"q": "6*7"})
    assert out["content"][0]["text"] == "42"


def test_call_tool_iserror_raises():
    poster = FakePoster({
        "initialize": {"capabilities": {}},
        "tools/call": {"isError": True, "content": "boom"},
    })
    client = McpClient(config=McpServerConfig(name="t", url="http://x"), poster=poster)
    client.connect()
    with pytest.raises(McpError):
        client.call_tool("bad", {})


def test_list_tools_before_connect_fails_loud():
    poster = FakePoster({})
    client = McpClient(config=McpServerConfig(name="t", url="http://x"), poster=poster)
    with pytest.raises(McpError):
        client.list_tools()


def test_connect_falls_back_to_secondary_transport():
    # primary (streamable_http) fails; fallback (sse) succeeds
    poster = FakePoster(
        {"initialize": {"capabilities": {}}},
        fail_transports=(Transport.STREAMABLE_HTTP,),
    )
    cfg = McpServerConfig(
        name="t", url="http://x",
        transport=Transport.STREAMABLE_HTTP, fallback=Transport.SSE,
    )
    client = McpClient(config=cfg, poster=poster)
    client.connect()  # should not raise
    assert client._connected is True
    # tried primary first, then fallback
    assert poster.calls[0][1] == Transport.STREAMABLE_HTTP
    assert any(t == Transport.SSE for _, t in poster.calls)


def test_connect_both_transports_fail_raises():
    poster = FakePoster(
        {"initialize": {"capabilities": {}}},
        fail_transports=(Transport.STREAMABLE_HTTP, Transport.SSE),
    )
    cfg = McpServerConfig(name="t", url="http://x", fallback=Transport.SSE)
    client = McpClient(config=cfg, poster=poster)
    with pytest.raises(McpError):
        client.connect()


# -- config loader ------------------------------------------------------------
def test_load_server_configs(tmp_path):
    p = tmp_path / "mcp-servers.yaml"
    p.write_text(
        "servers:\n"
        "  - name: docs\n"
        "    url: https://example.com/mcp\n"
        "    transport: sse\n"
        "    fallback: streamable_http\n",
        encoding="utf-8",
    )
    configs = load_server_configs(p)
    assert len(configs) == 1
    assert configs[0].transport == Transport.SSE
    assert configs[0].fallback == Transport.STREAMABLE_HTTP


def test_load_server_configs_missing_file(tmp_path):
    assert load_server_configs(tmp_path / "nope.yaml") == []


def test_load_server_configs_bad_entry_fails_loud(tmp_path):
    p = tmp_path / "mcp-servers.yaml"
    p.write_text("servers:\n  - name: x\n", encoding="utf-8")  # missing url
    with pytest.raises(ValueError):
        load_server_configs(p)
