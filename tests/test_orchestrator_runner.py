"""Tests for OrchestratorRunner + route_to_agent tool.

No LLM is needed: we inject a fake `run_isolated` into the OrchestratorRunner,
so each "specialized agent" run just records what it was given and returns a
canned summary. That lets us assert routing correctness, tool-group isolation,
parallel interleaving, and sequential chaining deterministically.
"""
from __future__ import annotations

import threading
import time

from pathlib import Path

from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec, load_agents
from pyclaw.orchestrator.runner import OrchestratorRunner
from pyclaw.orchestrator.tool import ROUTE_TOOL_NAME, make_route_to_agent_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.add(AgentSpec(name="db-agent", description="HR DB", tool_prefixes=("db_",)))
    reg.add(AgentSpec(name="pdpa-agent", description="PDPA law", tool_prefixes=("pdpa_",)))
    return reg


AVAILABLE = (
    "db_execute_query_tool", "db_preview_table",
    "pdpa_search_pdpa", "pdpa_get_penalty",
)


def _recording_runner(record: list):
    """A run_isolated that records (allowed_tools, objective) and echoes."""

    def run_isolated(spec, on_tool=None):
        record.append((tuple(spec.allowed_tools), spec.objective))
        return f"answer:{spec.objective}"

    return run_isolated


# -- routing correctness ------------------------------------------------------
def test_route_one_sends_only_that_agents_tool_group():
    record: list = []
    runner = OrchestratorRunner(
        registry=_registry(),
        run_isolated=_recording_runner(record),
        available_tools=AVAILABLE,
    )
    result = runner.route_one("db-agent", "how many employees?")
    assert result.ok and result.summary == "answer:how many employees?"
    allowed, _ = record[0]
    # db-agent gets db_ tools, never pdpa_ tools (isolation).
    assert set(allowed) == {"db_execute_query_tool", "db_preview_table"}


def test_route_to_unknown_agent_fails_cleanly():
    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=_recording_runner([]),
        available_tools=AVAILABLE,
    )
    result = runner.route_one("ghost-agent", "x")
    assert result.ok is False and "unknown agent" in (result.error or "")


def test_route_tool_routes_single():
    record: list = []
    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=_recording_runner(record),
        available_tools=AVAILABLE,
    )
    tool = make_route_to_agent_tool(runner)
    out = tool.fn({"agent": "pdpa-agent", "message": "what is section 79?"})
    assert out["agent"] == "pdpa-agent" and out["ok"] is True
    allowed, _ = record[0]
    assert set(allowed) == {"pdpa_search_pdpa", "pdpa_get_penalty"}


def test_route_tool_requires_agent_or_routes():
    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=_recording_runner([]),
        available_tools=AVAILABLE,
    )
    out = make_route_to_agent_tool(runner).fn({})
    assert isinstance(out, str) and "error" in out


# -- parallel dispatch (Pattern A) -------------------------------------------
def test_route_parallel_preserves_order_and_runs_concurrently():
    def slow(spec, on_tool=None):
        time.sleep(0.2)
        return f"done:{spec.objective}"

    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=slow, available_tools=AVAILABLE,
        max_workers=4,
    )
    routes = [("db-agent", "q1"), ("pdpa-agent", "q2"), ("db-agent", "q3")]
    start = time.time()
    results = runner.route_parallel(routes)
    elapsed = time.time() - start
    assert [r.summary for r in results] == ["done:q1", "done:q2", "done:q3"]
    assert elapsed < 0.5  # 3x0.2s sequential would be 0.6s


def test_route_parallel_interleaves_with_agent_labels():
    """Core concurrency proof (like the PR #2 subagent test): each routed agent
    emits trace lines under its own [agent-name] label, and lines from
    different agents INTERLEAVE rather than appearing strictly grouped."""
    events: list[str] = []
    lock = threading.Lock()

    def run_isolated(spec, on_tool=None):
        for tick in range(2):
            if on_tool is not None:
                on_tool("call", "tick", {"tick": tick})
            time.sleep(0.05)
        return f"done:{spec.objective}"

    def collector(phase, name, info):
        with lock:
            events.append(info.get("_label", "?"))

    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=run_isolated,
        available_tools=AVAILABLE, max_workers=3,
    )
    routes = [("db-agent", "a"), ("pdpa-agent", "b"), ("db-agent", "c")]
    runner.route_parallel(routes, on_tool=collector)

    # Labels are the AGENT NAMES, not [sub#N].
    assert set(events) == {"[db-agent]", "[pdpa-agent]"}
    # Each route fired both ticks: 2 pdpa + 4 db (two db routes).
    assert events.count("[pdpa-agent]") == 2
    assert events.count("[db-agent]") == 4
    # Interleaving: the sequence is NOT strictly grouped by route.
    grouped = ["[db-agent]"] * 2 + ["[pdpa-agent]"] * 2 + ["[db-agent]"] * 2
    assert events != grouped, f"expected interleaving, got serial: {events}"


# -- sequential chain (Pattern B) --------------------------------------------
def test_route_sequential_feeds_prior_result_into_next():
    seen: list = []

    def run_isolated(spec, on_tool=None):
        seen.append(spec.objective)
        return f"RESULT[{spec.objective.splitlines()[0]}]"

    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=run_isolated,
        available_tools=AVAILABLE,
    )
    routes = [("db-agent", "find employee Somchai"), ("pdpa-agent", "is storing this lawful?")]
    results = runner.route_sequential(routes)

    assert results[0].agent == "db-agent"
    assert results[1].agent == "pdpa-agent"
    # Agent B's objective contains agent A's result (the chain).
    second_objective = seen[1]
    assert "is storing this lawful?" in second_objective
    assert "RESULT[find employee Somchai]" in second_objective
    assert "Context from the previous agent" in second_objective


def test_route_tool_sequential_mode():
    seen: list = []

    def run_isolated(spec, on_tool=None):
        seen.append(spec.objective)
        return f"R:{spec.objective.splitlines()[0]}"

    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=run_isolated,
        available_tools=AVAILABLE,
    )
    tool = make_route_to_agent_tool(runner)
    out = tool.fn({
        "mode": "sequential",
        "routes": [
            {"agent": "db-agent", "message": "step1"},
            {"agent": "pdpa-agent", "message": "step2"},
        ],
    })
    assert out["mode"] == "sequential"
    assert [r["agent"] for r in out["routes"]] == ["db-agent", "pdpa-agent"]
    assert "R:step1" in seen[1]  # B saw A's result


def test_route_tool_parallel_mode_default():
    runner = OrchestratorRunner(
        registry=_registry(), run_isolated=_recording_runner([]),
        available_tools=AVAILABLE,
    )
    tool = make_route_to_agent_tool(runner)
    out = tool.fn({
        "routes": [
            {"agent": "db-agent", "message": "x"},
            {"agent": "pdpa-agent", "message": "y"},
        ],
    })
    assert out["mode"] == "parallel"
    assert len(out["routes"]) == 2


# -- the tool's own schema ----------------------------------------------------
def test_route_tool_schema_enumerates_agents():
    runner = OrchestratorRunner(registry=_registry(), available_tools=AVAILABLE)
    spec = make_route_to_agent_tool(runner).to_llm_spec()
    assert spec["function"]["name"] == ROUTE_TOOL_NAME
    props = spec["function"]["parameters"]["properties"]
    assert set(props["agent"]["enum"]) == {"db-agent", "pdpa-agent"}


# -- per-agent SOUL/TOOLS prompt threading (Feature: agent souls) -------------
def _prompt_recording_runner(record: list):
    """A run_isolated that records the spec's system_prompt for each run."""

    def run_isolated(spec, on_tool=None):
        record.append(spec.system_prompt)
        return f"answer:{spec.objective}"

    return run_isolated


def test_route_one_passes_db_agents_own_system_prompt():
    """Routing to db-agent threads db-agent's composed SOUL/TOOLS prompt down."""
    record: list = []
    runner = OrchestratorRunner(
        registry=load_agents(AGENTS_MD),
        run_isolated=_prompt_recording_runner(record),
        available_tools=AVAILABLE,
    )
    runner.route_one("db-agent", "how many employees?")
    prompt = record[0]
    assert prompt is not None
    assert "read-only เท่านั้น" in prompt          # db-agent SOUL boundary
    assert "db_preview_table" in prompt             # db-agent TOOLS rule
    # The anti-hallucination guardrail is still appended (defence in depth).
    assert "do NOT invent data" in prompt
    # No cross-agent persona leakage.
    assert "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล" not in prompt


def test_route_one_passes_pdpa_agents_own_system_prompt():
    record: list = []
    runner = OrchestratorRunner(
        registry=load_agents(AGENTS_MD),
        run_isolated=_prompt_recording_runner(record),
        available_tools=AVAILABLE,
    )
    runner.route_one("pdpa-agent", "what is section 79?")
    prompt = record[0]
    assert prompt is not None
    assert "พ.ร.บ.คุ้มครองข้อมูลส่วนบุคคล" in prompt   # pdpa SOUL identity
    assert "pdpa_get_penalty" in prompt                # pdpa TOOLS rule
    assert "read-only เท่านั้น" not in prompt           # not db-agent's persona


def test_route_one_without_soul_tools_falls_back_to_generic(tmp_path):
    """An agent lacking SOUL/TOOLS gets system_prompt=None -> generic fallback."""
    f = tmp_path / "AGENTS.md"
    f.write_text("---\nname: db-agent\ndescription: d\ntools: db_\n---\n", encoding="utf-8")
    record: list = []
    runner = OrchestratorRunner(
        registry=load_agents(f),
        run_isolated=_prompt_recording_runner(record),
        available_tools=AVAILABLE,
    )
    result = runner.route_one("db-agent", "how many employees?")
    assert result.ok                      # no breakage
    assert record[0] is None              # generic prompt used by the loop
