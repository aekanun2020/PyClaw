"""Tests for HookEngine.fire resolution rules + the deterministic runners.

Covers all 5 resolution rules from the engine docstring, plus PythonRunner and
BashRunner behaviour (including fail-closed on a non-zero bash exit).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pyclaw.hooks.engine import HookEngine, HookSpec
from pyclaw.hooks.events import HookAction, HookEvent, HookPayload, HookResult
from pyclaw.hooks.runners import BashRunner, PythonRunner, RunnerType


# --- a fake runner so engine tests don't depend on subprocess/imports ---------
@dataclass
class FakeRunner:
    """Returns a canned HookResult per `target` key. `target` is the lookup key."""

    table: dict[str, HookResult]

    def run(self, target: str, payload: HookPayload) -> HookResult:  # noqa: ARG002
        return self.table[target]


def _payload(event: HookEvent = HookEvent.PRE_TOOL_USE, **kw) -> HookPayload:
    return HookPayload(event=event, tool=kw.get("tool", "write_file"),
                       arguments=kw.get("arguments", {"path": "/tmp/x"}))


def _engine(table: dict[str, HookResult], **kw) -> HookEngine:
    eng = HookEngine(runners={RunnerType.PYTHON: FakeRunner(table)}, **kw)  # type: ignore[arg-type]
    return eng


def _spec(name: str, target: str, priority: int = 100) -> HookSpec:
    return HookSpec(name=name, event=HookEvent.PRE_TOOL_USE,
                    runner=RunnerType.PYTHON, target=target, priority=priority)


# --- Rule 5: no matching hooks -> ALLOW --------------------------------------
def test_no_hooks_allows() -> None:
    eng = _engine({})
    assert eng.fire(_payload()).action is HookAction.ALLOW


# --- Rule 5 + #6: required event with no hook -> raise (fail loudly) ----------
def test_required_event_without_hook_raises() -> None:
    eng = _engine({}, required_events=frozenset({HookEvent.PRE_TOOL_USE}))
    with pytest.raises(RuntimeError, match="required event"):
        eng.fire(_payload())


# --- Rule 2: first BLOCK short-circuits (fail-closed) -------------------------
def test_block_short_circuits() -> None:
    table = {
        "a": HookResult(action=HookAction.NOTIFY, message="seen"),
        "b": HookResult(action=HookAction.BLOCK, message="denied"),
        "c": HookResult(action=HookAction.ALLOW),  # must never run
    }
    eng = _engine(table)
    eng.register(_spec("a", "a", priority=10))
    eng.register(_spec("b", "b", priority=20))
    eng.register(_spec("c", "c", priority=30))
    res = eng.fire(_payload())
    assert res.action is HookAction.BLOCK
    assert res.message == "denied"
    assert res.source_hook == "b"  # engine fills source_hook from the spec name


# --- Rule 3: MODIFY chains into later hooks + into the final result ----------
def test_modify_chains() -> None:
    modified = _payload()
    modified.arguments = {"path": "/tmp/SAFE"}
    table = {
        "mod": HookResult(action=HookAction.MODIFY, modified_payload=modified),
        "ok": HookResult(action=HookAction.ALLOW),
    }
    eng = _engine(table)
    eng.register(_spec("mod", "mod", priority=10))
    eng.register(_spec("ok", "ok", priority=20))
    res = eng.fire(_payload())
    assert res.action is HookAction.MODIFY
    assert res.modified_payload is not None
    assert res.modified_payload.arguments == {"path": "/tmp/SAFE"}


def test_modify_without_payload_raises() -> None:
    table = {"bad": HookResult(action=HookAction.MODIFY, modified_payload=None)}
    eng = _engine(table)
    eng.register(_spec("bad", "bad"))
    with pytest.raises(RuntimeError, match="MODIFY without"):
        eng.fire(_payload())


# --- Rule 4: NOTIFY messages accumulate --------------------------------------
def test_notify_accumulates() -> None:
    table = {
        "n1": HookResult(action=HookAction.NOTIFY, message="first"),
        "n2": HookResult(action=HookAction.NOTIFY, message="second"),
    }
    eng = _engine(table)
    eng.register(_spec("n1", "n1", priority=10))
    eng.register(_spec("n2", "n2", priority=20))
    res = eng.fire(_payload())
    assert res.action is HookAction.NOTIFY
    assert res.message == "first; second"


# --- Rule 1: priority order is respected (and stable within equal priority) --
def test_priority_ordering() -> None:
    eng = HookEngine()
    eng.register(_spec("late", "late", priority=90))
    eng.register(_spec("early", "early", priority=10))
    eng.register(_spec("mid_a", "mid_a", priority=50))
    eng.register(_spec("mid_b", "mid_b", priority=50))  # same priority as mid_a
    names = [h.name for h in eng.hooks_for(HookEvent.PRE_TOOL_USE)]
    assert names == ["early", "mid_a", "mid_b", "late"]


def test_disabled_hooks_skipped() -> None:
    eng = HookEngine()
    spec = _spec("off", "off")
    spec.enabled = False
    eng.register(spec)
    assert eng.hooks_for(HookEvent.PRE_TOOL_USE) == []


# --- PythonRunner -------------------------------------------------------------
def _py_block(payload: HookPayload) -> HookResult:  # used by target below
    return HookResult(action=HookAction.BLOCK, message="py blocked")


def test_python_runner_calls_function() -> None:
    runner = PythonRunner()
    res = runner.run(f"{__name__}:_py_block", _payload())
    assert res.action is HookAction.BLOCK
    assert res.message == "py blocked"


def test_python_runner_bad_target() -> None:
    with pytest.raises(ValueError, match="module:function"):
        PythonRunner().run("no_colon_here", _payload())


# --- BashRunner ---------------------------------------------------------------
def test_bash_runner_parses_json_block() -> None:
    runner = BashRunner()
    res = runner.run("""echo '{"action":"block","message":"nope"}'""", _payload())
    assert res.action is HookAction.BLOCK
    assert res.message == "nope"


def test_bash_runner_nonzero_exit_is_blocked() -> None:
    runner = BashRunner()
    res = runner.run("echo boom 1>&2; exit 3", _payload())
    assert res.action is HookAction.BLOCK
    assert "boom" in (res.message or "")


def test_bash_runner_empty_stdout_allows() -> None:
    runner = BashRunner()
    res = runner.run("true", _payload())
    assert res.action is HookAction.ALLOW
