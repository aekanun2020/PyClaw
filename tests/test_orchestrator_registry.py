"""Tests for the Orchestrator agent registry (AGENTS.md parsing — Feature #5).

These assert that AGENTS.md is ACTUALLY parsed and drives the registry: the
agent names, their when-to-use descriptions, and their tool-prefix groups all
come from the file, and the registry resolves those prefixes against a live
tool name list.
"""
from __future__ import annotations

from pathlib import Path

from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec, load_agents

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def test_repo_agents_md_exists_and_parses():
    assert AGENTS_MD.is_file(), "AGENTS.md must exist at repo root"
    reg = load_agents(AGENTS_MD)
    assert set(reg.names()) == {"db-agent", "pdpa-agent"}


def test_db_agent_owns_db_prefix_only():
    reg = load_agents(AGENTS_MD)
    db = reg.get("db-agent")
    assert db is not None
    assert db.tool_prefixes == ("db_",)
    available = (
        "db_execute_query_tool", "db_preview_table",
        "db_get_database_info_tool", "db_refresh_db_cache",
        "pdpa_search_pdpa", "spawn_subagent",
    )
    resolved = db.resolve_tools(available)
    assert resolved == (
        "db_execute_query_tool", "db_preview_table",
        "db_get_database_info_tool", "db_refresh_db_cache",
    )
    assert "pdpa_search_pdpa" not in resolved  # no cross-agent leakage


def test_pdpa_agent_owns_pdpa_prefix_only():
    reg = load_agents(AGENTS_MD)
    pdpa = reg.get("pdpa-agent")
    assert pdpa is not None
    assert pdpa.tool_prefixes == ("pdpa_",)
    available = ("pdpa_search_pdpa", "pdpa_get_penalty", "db_preview_table")
    assert pdpa.resolve_tools(available) == ("pdpa_search_pdpa", "pdpa_get_penalty")


def test_routing_prompt_lists_each_agent_with_description():
    reg = load_agents(AGENTS_MD)
    prompt = reg.build_routing_prompt()
    assert "db-agent:" in prompt and "pdpa-agent:" in prompt
    assert "HR" in prompt           # from db-agent description
    assert "PDPA" in prompt          # from pdpa-agent description
    # The orchestrator prompt is SMALL — no tool schemas, just name + when-to-use.
    assert "db_execute_query_tool" not in prompt


def test_load_agents_missing_file_is_empty(tmp_path):
    reg = load_agents(tmp_path / "nope.md")
    assert reg.all() == []


def test_multi_block_parsing(tmp_path):
    """The parser reads MANY frontmatter blocks from one file (not just the
    first, which is all the SKILL.md parser handles)."""
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# header prose ignored\n\n"
        "---\nname: a\ndescription: first\ntools: a_, x_\n---\n\n"
        "some prose between\n\n"
        "---\nname: b\ndescription: second\ntools: b_\n---\n",
        encoding="utf-8",
    )
    reg = load_agents(f)
    assert reg.names() == ["a", "b"]
    assert reg.get("a").tool_prefixes == ("a_", "x_")
    assert reg.get("b").tool_prefixes == ("b_",)


def test_agent_spec_owns():
    spec = AgentSpec(name="x", description="", tool_prefixes=("db_",))
    assert spec.owns("db_query") is True
    assert spec.owns("pdpa_x") is False
