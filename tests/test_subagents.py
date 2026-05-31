"""Tests for Layer 4: SubagentRunner (isolation, no-nesting, hook block) + ParallelTeam."""
from __future__ import annotations

import time

from pyclaw.hooks import HookEngine
from pyclaw.hooks.engine import HookSpec
from pyclaw.hooks.events import HookAction, HookEvent, HookResult, HookPayload
from pyclaw.hooks.runners import RunnerType
from pyclaw.subagents.runner import ParallelTeam, SubagentResult, SubagentRunner
from pyclaw.subagents.types import SubagentSpec, SubagentType


def _spec(objective="do the thing", type=SubagentType.GENERAL, **kw):
    return SubagentSpec(type=type, objective=objective, **kw)


def test_resolve_tools_inherit_then_restrict():
    runner = SubagentRunner(parent_tools=("read_file", "write_file", "delete_file"))
    # EXPLORE denies write_file + delete_file
    explore = runner.resolve_tools(_spec(type=SubagentType.EXPLORE))
    assert explore == ("read_file",)
    # GENERAL keeps everything (but always strips spawn tools)
    general = runner.resolve_tools(_spec(type=SubagentType.GENERAL))
    assert general == ("read_file", "write_file", "delete_file")


def test_resolve_tools_always_strips_spawn_tools():
    runner = SubagentRunner(parent_tools=("read_file", "spawn_subagent", "run_subagent"))
    assert runner.resolve_tools(_spec()) == ("read_file",)


def test_spawn_runs_isolated_and_returns_only_summary():
    captured = {}

    def fake_run(spec: SubagentSpec) -> str:
        captured["allowed_tools"] = spec.allowed_tools
        return "SUMMARY_ONLY"

    runner = SubagentRunner(parent_tools=("read_file", "write_file"), run_isolated=fake_run)
    result = runner.spawn(_spec(type=SubagentType.EXPLORE))
    assert result.ok is True
    assert result.summary == "SUMMARY_ONLY"
    # tools were resolved (write_file denied for EXPLORE)
    assert captured["allowed_tools"] == ("read_file",)


def test_spawn_refuses_nested():
    runner = SubagentRunner(parent_tools=(), run_isolated=lambda s: "x")
    result = runner.spawn(_spec(is_nested=True))
    assert result.ok is False
    assert "nested" in (result.error or "").lower()


def test_spawn_honours_presubagentspawn_block():
    hooks = HookEngine()
    # a python hook that always blocks subagent spawns
    import pyclaw.subagents._test_hooks as _th  # registered below via module injection
    hooks.register(
        HookSpec(
            name="deny_spawn",
            event=HookEvent.PRE_SUBAGENT_SPAWN,
            runner=RunnerType.PYTHON,
            target="pyclaw.subagents._test_hooks:deny",
            priority=10,
        )
    )
    runner = SubagentRunner(parent_tools=(), hooks=hooks, run_isolated=lambda s: "ran")
    result = runner.spawn(_spec())
    assert result.ok is False
    assert "blocked" in (result.error or "").lower()


def test_spawn_captures_isolated_failure():
    def boom(spec):
        raise RuntimeError("llm exploded")

    runner = SubagentRunner(parent_tools=(), run_isolated=boom)
    result = runner.spawn(_spec())
    assert result.ok is False and "llm exploded" in (result.error or "")


def test_parallel_team_preserves_order_and_runs_concurrently():
    def slow(spec: SubagentSpec) -> str:
        time.sleep(0.2)
        return f"done:{spec.objective}"

    runner = SubagentRunner(parent_tools=(), run_isolated=slow, max_workers=4)
    team = ParallelTeam(runner=runner)
    specs = [_spec(objective=f"task{i}") for i in range(4)]

    start = time.time()
    results = team.run(specs)
    elapsed = time.time() - start

    # order preserved
    assert [r.summary for r in results] == [f"done:task{i}" for i in range(4)]
    # concurrency: 4x0.2s would be 0.8s sequential; parallel should be well under
    assert elapsed < 0.6


def test_parallel_team_empty():
    runner = SubagentRunner(parent_tools=(), run_isolated=lambda s: "x")
    assert ParallelTeam(runner=runner).run([]) == []
