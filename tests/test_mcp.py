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


# --- EliteClaw .env compatibility -------------------------------------------
from pyclaw.mcp.client import (  # noqa: E402
    detect_transport,
    load_server_configs_from_dotenv,
    load_server_configs_from_env,
    parse_transport,
)


def test_detect_transport_from_url():
    assert detect_transport("http://h:8100/mcp") == Transport.STREAMABLE_HTTP
    assert detect_transport("http://h:8100/mcp/") == Transport.STREAMABLE_HTTP
    assert detect_transport("http://h:9000") == Transport.SSE
    assert detect_transport("http://h:8000/sse") == Transport.SSE


def test_parse_transport_spellings():
    assert parse_transport("sse") == Transport.SSE
    assert parse_transport("streamable-http") == Transport.STREAMABLE_HTTP
    assert parse_transport("streamable_http") == Transport.STREAMABLE_HTTP
    assert parse_transport("http") == Transport.STREAMABLE_HTTP
    assert parse_transport("") is None
    assert parse_transport(None) is None
    assert parse_transport("weird") is None


def test_env_numbered_servers_with_prefix_and_autodetect():
    env = {
        "MCP_SERVER_1_URL": "http://10.211.55.2:9000",
        "MCP_SERVER_1_NAME": "mssql",
        "MCP_SERVER_1_PREFIX": "db_",
        "MCP_SERVER_2_URL": "http://10.211.55.2:8100/mcp",
        "MCP_SERVER_2_NAME": "pdpa",
        "MCP_SERVER_2_PREFIX": "pdpa_",
    }
    cfgs = load_server_configs_from_env(env)
    assert [c.name for c in cfgs] == ["mssql", "pdpa"]
    # server 1: no /mcp -> SSE auto-detected; server 2: /mcp -> streamable
    assert cfgs[0].transport == Transport.SSE
    assert cfgs[0].fallback == Transport.STREAMABLE_HTTP
    assert cfgs[0].tool_prefix == "db_"
    assert cfgs[1].transport == Transport.STREAMABLE_HTTP
    assert cfgs[1].fallback == Transport.SSE
    assert cfgs[1].tool_prefix == "pdpa_"


def test_env_explicit_transport_overrides_autodetect():
    env = {
        "MCP_SERVER_1_URL": "http://h:8080/mcp",  # would auto-detect streamable
        "MCP_SERVER_1_TRANSPORT": "sse",            # but explicit wins
    }
    cfgs = load_server_configs_from_env(env)
    assert cfgs[0].transport == Transport.SSE


def test_env_timeout_ms_to_seconds_and_default():
    env = {
        "REQUEST_TIMEOUT": "45000",
        "MCP_SERVER_1_URL": "http://h:9000",
        "MCP_SERVER_2_URL": "http://h:9001",
        "MCP_SERVER_2_TIMEOUT": "60000",
    }
    cfgs = load_server_configs_from_env(env)
    assert cfgs[0].timeout == 45.0   # falls back to REQUEST_TIMEOUT
    assert cfgs[1].timeout == 60.0   # per-server override


def test_env_host_header():
    env = {"MCP_SERVER_1_URL": "http://h:9000", "MCP_SERVER_1_HOST": "api.example.com"}
    cfgs = load_server_configs_from_env(env)
    assert cfgs[0].headers == {"Host": "api.example.com"}


def test_env_gap_tolerance_then_stop():
    # server 1 present, 2 missing, 3 present, then gap -> both 1 and 3 parsed
    env = {
        "MCP_SERVER_1_URL": "http://h:1",
        "MCP_SERVER_3_URL": "http://h:3",
    }
    cfgs = load_server_configs_from_env(env)
    assert [c.url for c in cfgs] == ["http://h:1", "http://h:3"]


def test_env_legacy_single_server():
    env = {
        "MCP_SERVER_URL": "http://legacy:9000",
        "MCP_SERVER_NAME": "mssql",
        "MCP_TOOL_PREFIX": "db_",
    }
    cfgs = load_server_configs_from_env(env)
    assert len(cfgs) == 1
    assert cfgs[0].name == "mssql"
    assert cfgs[0].tool_prefix == "db_"


def test_env_empty_yields_empty():
    assert load_server_configs_from_env({}) == []


def test_env_bad_timeout_fails_loud():
    env = {"MCP_SERVER_1_URL": "http://h:1", "MCP_SERVER_1_TIMEOUT": "abc"}
    with pytest.raises(ValueError):
        load_server_configs_from_env(env)


def test_dotenv_file_roundtrip(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# comment line\n"
        "export MCP_SERVER_1_URL=http://h:9000\n"
        'MCP_SERVER_1_NAME="mssql"\n'
        "MCP_SERVER_1_PREFIX=db_\n"
        "\n"
        "MCP_SERVER_2_URL=http://h:8100/mcp\n"
        "MCP_SERVER_2_NAME=pdpa\n",
        encoding="utf-8",
    )
    cfgs = load_server_configs_from_dotenv(p)
    assert [c.name for c in cfgs] == ["mssql", "pdpa"]
    assert cfgs[0].tool_prefix == "db_"
    assert cfgs[1].transport == Transport.STREAMABLE_HTTP


def test_dotenv_missing_file(tmp_path):
    assert load_server_configs_from_dotenv(tmp_path / "nope.env") == []
