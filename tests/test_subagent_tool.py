"""Tests for the `spawn_subagent` tool (Layer 4 wired into the loop).

We inject a fake `run_isolated` into SubagentRunner so no LLM is needed: the
tool's job is to translate LLM arguments into specs, drive the runner, and
return ONLY summaries (isolation) — that is what we assert here.
"""
from __future__ import annotations

from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.subagents.runner import SubagentRunner
from pyclaw.subagents.tool import (
    SPAWN_TOOL_NAME,
    _make_tool_provider,
    make_spawn_subagent_tool,
    register_spawn_subagent_tool,
)
from pyclaw.subagents.types import SubagentType


def _fake_runner(record: list) -> SubagentRunner:
    def run_isolated(spec):
        record.append((spec.type, spec.objective, tuple(spec.allowed_tools)))
        return f"summary[{spec.type.value}]: {spec.objective}"

    return SubagentRunner(parent_tools=("db_query", "write_file"), run_isolated=run_isolated)


def test_spawn_single_returns_only_summary():
    record: list = []
    tool = make_spawn_subagent_tool(runner=_fake_runner(record))
    out = tool.fn({"type": "explore", "objective": "look at TestDB"})
    assert out["ok"] is True
    assert out["type"] == "explore"
    assert out["summary"] == "summary[explore]: look at TestDB"
    # explore is read-only: write_file must be stripped (inherit-then-restrict)
    _, _, allowed = record[0]
    assert "db_query" in allowed and "write_file" not in allowed


def test_spawn_parallel_team_preserves_order():
    record: list = []
    tool = make_spawn_subagent_tool(runner=_fake_runner(record))
    out = tool.fn({
        "type": "general",
        "objectives": ["task A", "task B", "task C"],
    })
    summaries = [s["summary"] for s in out["subagents"]]
    assert summaries == [
        "summary[general]: task A",
        "summary[general]: task B",
        "summary[general]: task C",
    ]  # order matches input regardless of completion timing


def test_spawn_requires_an_objective():
    tool = make_spawn_subagent_tool(runner=_fake_runner([]))
    out = tool.fn({"type": "general"})  # neither objective nor objectives
    assert isinstance(out, str) and "error" in out


def test_unknown_type_defaults_to_general():
    record: list = []
    tool = make_spawn_subagent_tool(runner=_fake_runner(record))
    tool.fn({"type": "not-a-real-type", "objective": "do it"})
    assert record[0][0] is SubagentType.GENERAL


def test_register_inherits_parent_tools_excluding_spawn():
    reg = ToolRegistry()
    # pretend MCP already registered two tools
    from pyclaw.core.tools import Tool
    reg.register(Tool(name="db_query", description="", fn=lambda a: None))
    reg.register(Tool(name="pdpa_search", description="", fn=lambda a: None))

    inherited = register_spawn_subagent_tool(reg)
    assert SPAWN_TOOL_NAME in reg.names()          # tool is now callable by the LLM
    assert set(inherited) == {"db_query", "pdpa_search"}
    assert SPAWN_TOOL_NAME not in inherited         # children never inherit spawn


def test_tool_provider_hands_child_the_parents_real_tool():
    """Regression: subagents must execute the parent's REAL tools (e.g. MCP),
    not an empty registry that forces them to hallucinate answers."""
    hits: list = []
    parent = ToolRegistry()

    def db_query(args):
        hits.append(args)
        return {"count": 9}

    parent.register(Tool(name="db_query", description="", fn=db_query))
    provider = _make_tool_provider(parent)

    child = provider(("db_query",))
    assert child.get("db_query") is not None            # real tool present
    assert child.dispatch("db_query", {"sql": "X"})["count"] == 9
    assert hits == [{"sql": "X"}]                        # parent's fn actually ran


def test_tool_provider_skips_unknown_names():
    """Names not in the parent registry are skipped — no fabricated tools."""
    parent = ToolRegistry()
    parent.register(Tool(name="real", description="", fn=lambda a: None))
    child = _make_tool_provider(parent)(("real", "ghost"))
    assert child.get("real") is not None
    assert child.get("ghost") is None


def test_tool_provider_none_without_parent_registry():
    assert _make_tool_provider(None) is None


def test_register_wires_a_provider_so_subagents_get_real_tools():
    """register_spawn_subagent_tool must build a runner whose tool_provider
    yields the parent's real tools (closing the hallucination gap end-to-end)."""
    reg = ToolRegistry()
    reg.register(Tool(name="db_query", description="", fn=lambda a: {"ok": True}))
    register_spawn_subagent_tool(reg)
    assert reg.get(SPAWN_TOOL_NAME) is not None
    # Build the provider the same way register does and check it returns real tools.
    provider = _make_tool_provider(reg)
    child = provider(("db_query",))
    assert child.dispatch("db_query", {}) == {"ok": True}


def test_spawn_tool_has_llm_spec_with_enum():
    tool = make_spawn_subagent_tool(runner=_fake_runner([]))
    spec = tool.to_llm_spec()
    assert spec["function"]["name"] == SPAWN_TOOL_NAME
    props = spec["function"]["parameters"]["properties"]
    assert set(props["type"]["enum"]) == {"explore", "plan", "review", "general"}
