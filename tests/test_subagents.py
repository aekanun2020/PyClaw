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


def test_call_runner_passes_on_tool_only_when_accepted():
    """Backward-compat: a 1-arg test runner must NOT receive on_tool; a runner
    that declares on_tool must receive it."""
    from pyclaw.subagents.runner import _call_runner

    seen = {}

    def legacy(spec):                       # only takes spec
        seen["legacy"] = True
        return "ok"

    def aware(spec, on_tool=None):          # accepts on_tool
        seen["aware_on_tool"] = on_tool
        return "ok"

    sentinel = object()
    assert _call_runner(legacy, _spec(), on_tool=sentinel) == "ok"
    assert seen["legacy"] is True           # did not raise -> on_tool dropped
    assert _call_runner(aware, _spec(), on_tool=sentinel) == "ok"
    assert seen["aware_on_tool"] is sentinel


def test_spawn_forwards_on_tool_to_isolated_runner():
    """spawn() must thread the parent observer into the isolated run."""
    got = {}

    def run_isolated(spec, on_tool=None):
        got["on_tool"] = on_tool
        return "done"

    runner = SubagentRunner(parent_tools=(), run_isolated=run_isolated)
    sentinel = object()
    runner.spawn(_spec(), on_tool=sentinel)
    assert got["on_tool"] is sentinel


def test_parallel_team_labels_each_subagent_and_interleaves():
    """The core proof of concurrency: each subagent emits trace lines under its
    own [sub#N] label, and the lines from different subagents INTERLEAVE rather
    than appearing strictly grouped — direct evidence of parallel execution."""
    events: list[str] = []
    events_lock = __import__("threading").Lock()

    def run_isolated(spec, on_tool=None):
        # Two ticks per subagent, with a pause between, so a serial execution
        # would group both ticks of one subagent before the next starts.
        for tick in range(2):
            if on_tool is not None:
                on_tool("call", "tick", {"tick": tick})
            time.sleep(0.05)
        return f"done:{spec.objective}"

    def collector(phase, name, info):
        with events_lock:
            events.append(info.get("_label", "?"))

    runner = SubagentRunner(parent_tools=(), run_isolated=run_isolated, max_workers=3)
    specs = [_spec(objective=f"t{i}") for i in range(3)]
    ParallelTeam(runner=runner).run(specs, on_tool=collector)

    # All three labels appeared.
    labels = set(events)
    assert labels == {"[sub#1]", "[sub#2]", "[sub#3]"}, labels
    # Each subagent emitted both of its ticks.
    assert events.count("[sub#1]") == 2
    assert events.count("[sub#2]") == 2
    assert events.count("[sub#3]") == 2
    # Interleaving: the sequence is NOT strictly grouped (e.g. not
    # [s1,s1,s2,s2,s3,s3]). With a pause between ticks, true parallelism makes
    # distinct labels appear before any single subagent finishes both ticks.
    grouped = ["[sub#1]"] * 2 + ["[sub#2]"] * 2 + ["[sub#3]"] * 2
    assert events != grouped, f"expected interleaving, got serial order: {events}"
    # Within the first 3 events, all three subagents should be represented if
    # they truly ran at once (each fired its first tick before sleeping).
    assert set(events[:3]) == {"[sub#1]", "[sub#2]", "[sub#3]"}, events


def test_parallel_team_no_on_tool_is_silent():
    """No observer -> no labels, no overhead, results still correct."""
    runner = SubagentRunner(parent_tools=(), run_isolated=lambda s: f"r:{s.objective}")
    specs = [_spec(objective=f"t{i}") for i in range(3)]
    results = ParallelTeam(runner=runner).run(specs)  # on_tool defaults to None
    assert [r.summary for r in results] == ["r:t0", "r:t1", "r:t2"]
