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
    assert set(reg.names()) == {"db-agent", "pdpa-agent", "rag-agent"}


def test_rag_agent_owns_rag_prefix_only():
    reg = load_agents(AGENTS_MD)
    rag = reg.get("rag-agent")
    assert rag is not None
    assert rag.tool_prefixes == ("rag_",)
    available = (
        "rag_add_documentation", "rag_search_documentation",
        "rag_list_sources", "rag_add_directory",
        "db_preview_table", "pdpa_search_pdpa",
    )
    resolved = rag.resolve_tools(available)
    assert resolved == (
        "rag_add_documentation", "rag_search_documentation",
        "rag_list_sources", "rag_add_directory",
    )
    assert "db_preview_table" not in resolved  # no cross-agent leakage


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


# -- SOUL.md / TOOLS.md per-agent persona (Feature: agent souls) --------------
def test_repo_db_agent_resolves_soul_and_tools():
    """The real repo agents/db-agent/{SOUL,TOOLS}.md are discovered name-based."""
    reg = load_agents(AGENTS_MD)
    db = reg.get("db-agent")
    soul = db.load_soul()
    tools_doc = db.load_tools_doc()
    assert soul is not None and "read-only เท่านั้น" in soul
    assert tools_doc is not None and "db_get_database_info_tool" in tools_doc


def test_repo_pdpa_agent_resolves_soul_and_tools():
    reg = load_agents(AGENTS_MD)
    pdpa = reg.get("pdpa-agent")
    soul = pdpa.load_soul()
    tools_doc = pdpa.load_tools_doc()
    assert soul is not None and "PDPA" in soul
    assert tools_doc is not None and "pdpa_get_penalty" in tools_doc


def test_repo_rag_agent_resolves_soul_and_tools():
    reg = load_agents(AGENTS_MD)
    rag = reg.get("rag-agent")
    soul = rag.load_soul()
    tools_doc = rag.load_tools_doc()
    assert soul is not None and "RAG" in soul
    assert tools_doc is not None and "rag_list_sources" in tools_doc


def test_compose_system_prompt_contains_soul_tools_and_guardrail():
    reg = load_agents(AGENTS_MD)
    db = reg.get("db-agent")
    prompt = db.compose_system_prompt(guardrail="DO NOT invent data.")
    assert prompt is not None
    # db-agent persona (SOUL) + tool rules (TOOLS) + appended guardrail.
    assert "read-only เท่านั้น" in prompt          # SOUL boundary
    assert "ห้าม INSERT, UPDATE, DELETE" in prompt  # SOUL boundary
    assert "db_preview_table" in prompt             # TOOLS rule
    assert "DO NOT invent data." in prompt          # guardrail preserved


def test_compose_system_prompt_distinct_per_agent():
    reg = load_agents(AGENTS_MD)
    db_prompt = reg.get("db-agent").compose_system_prompt()
    pdpa_prompt = reg.get("pdpa-agent").compose_system_prompt()
    assert db_prompt != pdpa_prompt
    assert "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล" in pdpa_prompt
    assert "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล" not in db_prompt


def test_missing_soul_tools_falls_back_to_none(tmp_path):
    """An agent with no SOUL/TOOLS composes to None -> generic-prompt fallback."""
    f = tmp_path / "AGENTS.md"
    f.write_text("---\nname: bare\ndescription: d\ntools: x_\n---\n", encoding="utf-8")
    reg = load_agents(f)
    bare = reg.get("bare")
    assert bare.load_soul() is None
    assert bare.load_tools_doc() is None
    assert bare.compose_system_prompt(guardrail="g") is None


def test_only_soul_present_still_composes(tmp_path):
    """SOUL without TOOLS (or vice versa) still yields a prompt (optional pair)."""
    f = tmp_path / "AGENTS.md"
    f.write_text("---\nname: solo\ndescription: d\ntools: x_\n---\n", encoding="utf-8")
    home = tmp_path / "agents" / "solo"
    home.mkdir(parents=True)
    (home / "SOUL.md").write_text("# persona only", encoding="utf-8")
    reg = load_agents(f)
    prompt = reg.get("solo").compose_system_prompt(guardrail="GUARD")
    assert prompt is not None
    assert "persona only" in prompt and "GUARD" in prompt


def test_home_override_via_frontmatter(tmp_path):
    """An explicit `home:` key overrides name-based discovery."""
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "---\nname: agent-x\ndescription: d\ntools: x_\nhome: custom_home\n---\n",
        encoding="utf-8",
    )
    home = tmp_path / "custom_home"
    home.mkdir(parents=True)
    (home / "SOUL.md").write_text("# custom-home persona", encoding="utf-8")
    reg = load_agents(f)
    agent = reg.get("agent-x")
    assert agent.home == home
    assert "custom-home persona" in (agent.compose_system_prompt() or "")
