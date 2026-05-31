"""Tests for orchestrator mode wired into the CLI.

We monkeypatch MCP mounting to register fake db_/pdpa_ tools so the orchestrator
loop can be assembled offline (no live servers, no LLM). The key assertions:
the orchestrator's OWN tool registry holds ONLY route_to_agent (no domain
tools), AGENTS.md drives the agent registry, and the flag is OFF by default
(backward compat).
"""
from __future__ import annotations

from pyclaw import cli
from pyclaw.core.tools import Tool
from pyclaw.orchestrator.tool import ROUTE_TOOL_NAME


def _register_fake_mcp(registry):
    """Stand-in for _mount_mcp: register the real-named db_/pdpa_ tools."""
    for name in (
        "db_execute_query_tool", "db_preview_table",
        "db_get_database_info_tool", "db_refresh_db_cache",
        "pdpa_search_pdpa", "pdpa_get_related_sections",
        "pdpa_get_penalty", "pdpa_get_pdpa_summary",
    ):
        registry.register(Tool(name=name, description="", fn=lambda a: {"ok": True}))
    return []


def test_orchestrator_loop_owns_only_route_tool(monkeypatch):
    monkeypatch.setattr(cli, "_mount_mcp", _register_fake_mcp)
    loop = cli._build_orchestrator_loop()
    # The orchestrator's OWN registry: just the meta-tool.
    assert loop.tools.names() == [ROUTE_TOOL_NAME]
    assert "db_execute_query_tool" not in loop.tools.names()
    assert "pdpa_search_pdpa" not in loop.tools.names()
    # Permission policy allows only the meta-tool.
    assert loop.permissions.is_allowed(ROUTE_TOOL_NAME)
    assert not loop.permissions.is_allowed("db_execute_query_tool")


def test_orchestrator_loop_registry_from_agents_md(monkeypatch):
    monkeypatch.setattr(cli, "_mount_mcp", _register_fake_mcp)
    loop = cli._build_orchestrator_loop()
    agents = loop._orchestrator_agents
    assert set(agents.names()) == {"db-agent", "pdpa-agent"}
    # The system prompt embeds the routing list from AGENTS.md.
    assert "db-agent" in loop.system_prompt and "pdpa-agent" in loop.system_prompt
    assert "route_to_agent" in loop.system_prompt


def test_orchestrator_available_tools_seed_the_runner(monkeypatch):
    """The orchestrator's runner is seeded with the live (MCP) tool names, so
    db-agent resolves to db_ tools and pdpa-agent to pdpa_ tools — proving the
    real tools back the routed agents (not an empty registry)."""
    monkeypatch.setattr(cli, "_mount_mcp", _register_fake_mcp)
    loop = cli._build_orchestrator_loop()
    agents = loop._orchestrator_agents
    available = (
        "db_execute_query_tool", "db_preview_table",
        "pdpa_search_pdpa", "pdpa_get_penalty",
    )
    db_resolved = agents.get("db-agent").resolve_tools(available)
    pdpa_resolved = agents.get("pdpa-agent").resolve_tools(available)
    assert "db_execute_query_tool" in db_resolved
    assert "pdpa_search_pdpa" not in db_resolved      # no cross-agent leakage
    assert "pdpa_search_pdpa" in pdpa_resolved
    assert "db_execute_query_tool" not in pdpa_resolved


def test_orchestrator_missing_agents_md_is_hard_error(monkeypatch):
    """With no AGENTS.md discoverable, orchestrator mode fails loudly (#6).

    cli imports `load_agents` from pyclaw.orchestrator inside the builder, so we
    patch it on that package namespace.
    """
    import pyclaw.orchestrator as orch
    from pyclaw.orchestrator.registry import AgentRegistry

    monkeypatch.setattr(cli, "_mount_mcp", _register_fake_mcp)
    monkeypatch.setattr(orch, "load_agents", lambda *a, **k: AgentRegistry())

    try:
        cli._build_orchestrator_loop()
        assert False, "expected RuntimeError for missing/empty AGENTS.md"
    except RuntimeError as exc:
        assert "AGENTS.md" in str(exc)


def test_chat_flag_orchestrator_off_by_default(monkeypatch):
    """Backward compat: `chat` without --orchestrator builds the FLAT loop and
    never touches the orchestrator builder."""
    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")

    from pyclaw.runtime.context import ContextManager

    built = {"flat": 0, "orch": 0}

    class FakeLoop:
        _mcp_mounted: list = []
        def __init__(self):
            self.context = ContextManager()
        def run(self, *a, **k):
            return "x"

    monkeypatch.setattr(cli, "_build_loop", lambda **kw: (built.__setitem__("flat", built["flat"] + 1) or FakeLoop()))
    monkeypatch.setattr(cli, "_build_orchestrator_loop", lambda: (built.__setitem__("orch", built["orch"] + 1) or FakeLoop()))

    lines = iter(["quit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(lines))
    rc = cli.main(["chat"])
    assert rc == 0
    assert built == {"flat": 1, "orch": 0}


def test_chat_flag_orchestrator_on_builds_orchestrator(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")
    from pyclaw.runtime.context import ContextManager
    from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec

    built = {"flat": 0, "orch": 0}

    class FakeLoop:
        def __init__(self):
            self.context = ContextManager()
            self._mcp_mounted = []
            reg = AgentRegistry()
            reg.add(AgentSpec(name="db-agent", description="d", tool_prefixes=("db_",)))
            self._orchestrator_agents = reg
        def run(self, *a, **k):
            return "routed"

    monkeypatch.setattr(cli, "_build_loop", lambda **kw: (built.__setitem__("flat", built["flat"] + 1) or FakeLoop()))
    monkeypatch.setattr(cli, "_build_orchestrator_loop", lambda: (built.__setitem__("orch", built["orch"] + 1) or FakeLoop()))

    lines = iter(["quit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(lines))
    rc = cli.main(["chat", "--orchestrator"])
    err = capsys.readouterr().err
    assert rc == 0
    assert built == {"flat": 0, "orch": 1}
    assert "orchestrator: on" in err
    assert "[orchestrator]" in err  # the agent-count banner
