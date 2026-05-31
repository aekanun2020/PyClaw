"""Tests for the MCP -> ToolRegistry bridge (no network; fake clients)."""
from __future__ import annotations

import pytest

from pyclaw.core.tools import ToolRegistry
from pyclaw.mcp.bridge import discover_configs, mount_mcp_tools
from pyclaw.mcp.client import McpServerConfig, McpTool, Transport


class FakeClient:
    def __init__(self, config, tools, *, fail=False):
        self.config = config
        self._tools = tools
        self._fail = fail
        self.connected = False
        self.calls = []

    def connect(self):
        if self._fail:
            raise RuntimeError("connect failed")
        self.connected = True

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"echo": name, "args": arguments}


def _cfg(name, prefix=""):
    return McpServerConfig(name=name, url="http://h:9000", tool_prefix=prefix)


def test_mount_registers_prefixed_tools():
    cfg = _cfg("mssql", prefix="db_")
    tools = [
        McpTool(server="mssql", name="query", description="run sql",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}}),
        McpTool(server="mssql", name="tables", description="list tables"),
    ]
    fake = FakeClient(cfg, tools)
    reg = ToolRegistry()

    mounted = mount_mcp_tools(reg, [cfg], client_factory=lambda c: fake)

    assert fake.connected is True
    assert reg.names() == ["db_query", "db_tables"]
    assert mounted[0].tool_names == ["db_query", "db_tables"]
    # description carries the server tag; schema is forwarded
    assert reg.get("db_query").description.startswith("[mcp:mssql]")
    assert reg.get("db_query").parameters["properties"] == {"q": {"type": "string"}}


def test_dispatch_routes_to_correct_remote_tool():
    cfg = _cfg("mssql", prefix="db_")
    tools = [McpTool(server="mssql", name="query", description="q")]
    fake = FakeClient(cfg, tools)
    reg = ToolRegistry()
    mount_mcp_tools(reg, [cfg], client_factory=lambda c: fake)

    out = reg.dispatch("db_query", {"q": "select 1"})
    # the LOCAL prefixed name maps back to the REMOTE unprefixed name
    assert fake.calls == [("query", {"q": "select 1"})]
    assert out == {"echo": "query", "args": {"q": "select 1"}}


def test_two_servers_no_name_collision():
    a = _cfg("mssql", prefix="db_")
    b = _cfg("pdpa", prefix="pdpa_")
    fakes = {
        "mssql": FakeClient(a, [McpTool(server="mssql", name="query", description="")]),
        "pdpa": FakeClient(b, [McpTool(server="pdpa", name="query", description="")]),
    }
    reg = ToolRegistry()
    mount_mcp_tools(reg, [a, b], client_factory=lambda c: fakes[c.name])
    # same remote tool name "query" coexists thanks to prefixes
    assert set(reg.names()) == {"db_query", "pdpa_query"}


def test_connect_failure_is_loud_in_strict():
    cfg = _cfg("dead")
    fake = FakeClient(cfg, [], fail=True)
    reg = ToolRegistry()
    with pytest.raises(RuntimeError):
        mount_mcp_tools(reg, [cfg], client_factory=lambda c: fake, strict=True)


def test_discover_merges_env_and_yaml(tmp_path):
    yaml_file = tmp_path / "mcp-servers.yaml"
    yaml_file.write_text(
        "servers:\n  - name: docs\n    url: https://example.com/mcp\n    transport: streamable_http\n",
        encoding="utf-8",
    )
    env = {"MCP_SERVER_1_URL": "http://h:9000", "MCP_SERVER_1_NAME": "mssql"}
    cfgs = discover_configs(yaml_path=yaml_file, env=env)
    names = {c.name for c in cfgs}
    assert names == {"docs", "mssql"}


def test_discover_empty_is_empty(tmp_path):
    assert discover_configs(yaml_path=tmp_path / "none.yaml", env={}) == []


def test_dead_server_skipped_by_default_others_still_mount():
    good = _cfg("ok", prefix="ok_")
    bad = _cfg("dead", prefix="x_")
    fakes = {
        "ok": FakeClient(good, [McpTool(server="ok", name="ping", description="")]),
        "dead": FakeClient(bad, [], fail=True),
    }
    reg = ToolRegistry()
    warnings = []
    mounted = mount_mcp_tools(
        reg, [bad, good],
        client_factory=lambda c: fakes[c.name],
        on_warn=warnings.append,
    )
    # the dead one is skipped, the good one still mounts
    assert reg.names() == ["ok_ping"]
    assert [m.config.name for m in mounted] == ["ok"]
    assert any("dead" in w for w in warnings)


def test_strict_mode_raises_on_dead_server():
    bad = _cfg("dead")
    fake = FakeClient(bad, [], fail=True)
    reg = ToolRegistry()
    with pytest.raises(RuntimeError):
        mount_mcp_tools(reg, [bad], client_factory=lambda c: fake, strict=True)
