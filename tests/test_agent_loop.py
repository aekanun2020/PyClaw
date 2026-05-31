"""Integration tests for AgentLoop.run — the place where all layers meet.

We inject a scripted fake LLM and fake tools, so the tests assert the
deterministic guarantees of the loop without any network/LLM calls:

  - a tool call is executed and its result fed back, ending in a text answer
  - a permission-blocked tool is never executed and is audited
  - a PreToolUse BLOCK hook stops a tool deterministically
  - HITL denial blocks a dangerous tool
  - every tool decision is written to the audit log (JSONL)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pyclaw.core.llm import LLMResponse, ToolCall
from pyclaw.core.loop import AgentLoop
from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.hooks.engine import HookEngine, HookSpec
from pyclaw.hooks.events import HookAction, HookEvent, HookPayload, HookResult
from pyclaw.hooks.runners import RunnerType
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import ApprovalRequest, HITLGate


# --- scripted fake LLM: returns queued responses in order --------------------
@dataclass
class FakeLLM:
    script: list[LLMResponse]
    seen_messages: list[list[dict]] = field(default_factory=list)
    _i: int = 0

    def complete(self, messages, tools=None, model=None, temperature=0.0):  # noqa: ANN001
        self.seen_messages.append(messages)
        resp = self.script[self._i]
        self._i += 1
        return resp


@dataclass
class FakeRunner:
    table: dict[str, HookResult]

    def run(self, target: str, payload: HookPayload) -> HookResult:  # noqa: ARG002
        return self.table[target]


def _build(tmp_path: Path, *, llm, permissions=None, hooks=None, hitl=None, tools=None):
    audit = AuditLog(path=tmp_path / "audit.jsonl")
    return AgentLoop(
        llm=llm,
        hooks=hooks or HookEngine(),
        context=ContextManager(),
        audit=audit,
        hitl=hitl or HITLGate(prompt_fn=lambda req: True),
        permissions=permissions or PermissionPolicy(),
        tools=tools or ToolRegistry(),
    ), audit


def _echo_registry(calls: list) -> ToolRegistry:
    reg = ToolRegistry()

    def echo(args):
        calls.append(args)
        return {"echoed": args}

    reg.register(Tool(name="echo", description="echo args", fn=echo))
    return reg


def _read_events(path: Path) -> list[str]:
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


# --- happy path: tool runs, then a text answer -------------------------------
def test_tool_then_final_answer(tmp_path: Path) -> None:
    calls: list = []
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo", arguments={"x": 1})]),
        LLMResponse(text="done"),
    ])
    loop, audit = _build(tmp_path, llm=llm, tools=_echo_registry(calls))
    out = loop.run("hello")
    assert out == "done"
    assert calls == [{"x": 1}]                       # tool actually executed
    assert "tool_call" in _read_events(audit.path)   # and audited


# --- permission blocks a tool: never executed, audited -----------------------
def test_permission_blocks_tool(tmp_path: Path) -> None:
    calls: list = []
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo", arguments={})]),
        LLMResponse(text="finished"),
    ])
    policy = PermissionPolicy(blocked_tools=frozenset({"echo"}))
    loop, audit = _build(tmp_path, llm=llm, permissions=policy, tools=_echo_registry(calls))
    out = loop.run("hi")
    assert out == "finished"
    assert calls == []                                       # NEVER executed
    assert "tool_blocked_permission" in _read_events(audit.path)


# --- PreToolUse BLOCK hook stops the tool deterministically ------------------
def test_pretooluse_hook_blocks(tmp_path: Path) -> None:
    calls: list = []
    eng = HookEngine(runners={RunnerType.PYTHON: FakeRunner(
        {"block": HookResult(action=HookAction.BLOCK, message="nope")}
    )})  # type: ignore[arg-type]
    eng.register(HookSpec(name="b", event=HookEvent.PRE_TOOL_USE,
                          runner=RunnerType.PYTHON, target="block"))
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo", arguments={})]),
        LLMResponse(text="ok"),
    ])
    loop, audit = _build(tmp_path, llm=llm, hooks=eng, tools=_echo_registry(calls))
    out = loop.run("hi")
    assert out == "ok"
    assert calls == []                                  # blocked before execution
    assert "tool_blocked_hook" in _read_events(audit.path)


# --- HITL denial blocks a dangerous tool -------------------------------------
def test_hitl_denial_blocks(tmp_path: Path) -> None:
    calls: list = []
    reg = ToolRegistry()

    def deleter(args):
        calls.append(args)
        return "deleted"

    reg.register(Tool(name="delete_file", description="danger", fn=deleter))
    gate = HITLGate(prompt_fn=lambda req: False)  # human says NO
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="delete_file",
                                                  arguments={"path": "/x"})]),
        LLMResponse(text="stopped"),
    ])
    loop, audit = _build(tmp_path, llm=llm, hitl=gate, tools=reg)
    out = loop.run("rm")
    assert out == "stopped"
    assert calls == []                               # denied -> not executed
    assert "hitl_denied" in _read_events(audit.path)


# --- PreToolUse MODIFY rewrites the arguments before execution ---------------
def test_pretooluse_hook_modifies_args(tmp_path: Path) -> None:
    calls: list = []
    safe = HookPayload(event=HookEvent.PRE_TOOL_USE, tool="echo",
                       arguments={"path": "/SAFE"})
    eng = HookEngine(runners={RunnerType.PYTHON: FakeRunner(
        {"mod": HookResult(action=HookAction.MODIFY, modified_payload=safe)}
    )})  # type: ignore[arg-type]
    eng.register(HookSpec(name="m", event=HookEvent.PRE_TOOL_USE,
                          runner=RunnerType.PYTHON, target="mod"))
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo",
                                                  arguments={"path": "/DANGER"})]),
        LLMResponse(text="ok"),
    ])
    loop, _ = _build(tmp_path, llm=llm, hooks=eng, tools=_echo_registry(calls))
    loop.run("hi")
    assert calls == [{"path": "/SAFE"}]              # args were rewritten by the hook


# --- multi-turn (chat) mode: history persists, system prompt is not duplicated
def test_multi_turn_preserves_history_single_system(tmp_path: Path) -> None:
    """Calling run() twice on the same loop must replay the first turn into the
    second (so the agent 'remembers'), and must NOT re-append the system prompt.
    """
    llm = FakeLLM(script=[LLMResponse(text="first answer"),
                          LLMResponse(text="second answer")])
    loop, _ = _build(tmp_path, llm=llm)

    assert loop.run("my name is Aekanun") == "first answer"
    assert loop.run("what is my name?") == "second answer"

    # The second call saw the full prior conversation (multi-turn memory).
    second_seen = llm.seen_messages[1]
    roles = [m["role"] for m in second_seen]
    contents = [m["content"] for m in second_seen]
    assert roles.count("system") == 1                  # system prompt NOT duplicated
    assert roles[0] == "system"                        # and it stays first
    assert "my name is Aekanun" in contents            # turn 1 user msg replayed
    assert "first answer" in contents                  # turn 1 assistant reply replayed
    assert "what is my name?" in contents              # turn 2 user msg present


# --- streaming: on_delta receives chunks; final answer still returned --------
@dataclass
class StreamingFakeLLM:
    """Fake provider that streams a reply in chunks via on_delta."""

    chunks: list[str]

    def complete(self, messages, tools=None, model=None, temperature=0.0):  # noqa: ANN001
        return LLMResponse(text="".join(self.chunks))

    def complete_stream(self, messages, tools=None, model=None, temperature=0.0,
                        on_delta=None):  # noqa: ANN001
        for c in self.chunks:
            if on_delta is not None:
                on_delta(c)
        return LLMResponse(text="".join(self.chunks))


def test_run_streams_via_on_delta(tmp_path: Path) -> None:
    llm = StreamingFakeLLM(chunks=["Hel", "lo ", "world"])
    loop, _ = _build(tmp_path, llm=llm)
    seen: list[str] = []
    out = loop.run("hi", on_delta=seen.append)
    assert out == "Hello world"          # full answer returned to caller
    assert seen == ["Hel", "lo ", "world"]  # and streamed chunk-by-chunk


def test_run_without_on_delta_uses_blocking_complete(tmp_path: Path) -> None:
    # No on_delta -> must NOT stream (falls back to .complete()).
    llm = StreamingFakeLLM(chunks=["a", "b"])
    loop, _ = _build(tmp_path, llm=llm)
    seen: list[str] = []
    out = loop.run("hi")  # no on_delta
    assert out == "ab"
    assert seen == []     # nothing streamed


# --- on_tool observer: fires around every executed tool (live --trace) -------
def test_on_tool_fires_call_and_return_with_args_and_result(tmp_path: Path) -> None:
    """The on_tool observer sees each executed tool: a 'call' with the real
    arguments, then a 'return' with the actual result and an elapsed time."""
    calls: list = []
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo", arguments={"x": 7})]),
        LLMResponse(text="done"),
    ])
    loop, _ = _build(tmp_path, llm=llm, tools=_echo_registry(calls))

    events: list[tuple] = []
    out = loop.run("hi", on_tool=lambda phase, name, info: events.append((phase, name, info)))

    assert out == "done"
    assert calls == [{"x": 7}]                       # tool actually executed
    assert len(events) == 2                          # exactly one call + one return
    call_phase, call_name, call_info = events[0]
    ret_phase, ret_name, ret_info = events[1]
    assert call_phase == "call" and call_name == "echo"
    assert call_info["arguments"] == {"x": 7}        # real arguments surfaced
    assert ret_phase == "return" and ret_name == "echo"
    assert ret_info["result"] == {"echoed": {"x": 7}}  # real result surfaced
    assert isinstance(ret_info["seconds"], float) and ret_info["seconds"] >= 0.0


def test_on_tool_not_fired_for_permission_blocked_tool(tmp_path: Path) -> None:
    """A tool blocked by policy is never dispatched, so the observer that only
    wraps real execution must NOT see it — no PII leaks for refused tools."""
    calls: list = []
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo", arguments={"secret": 1})]),
        LLMResponse(text="finished"),
    ])
    policy = PermissionPolicy(blocked_tools=frozenset({"echo"}))
    loop, _ = _build(tmp_path, llm=llm, permissions=policy, tools=_echo_registry(calls))

    events: list[tuple] = []
    out = loop.run("hi", on_tool=lambda *a: events.append(a))

    assert out == "finished"
    assert calls == []        # never executed
    assert events == []       # observer never saw the blocked tool


def test_on_tool_none_is_safe_default(tmp_path: Path) -> None:
    """Without an on_tool observer (the default), the loop behaves normally."""
    calls: list = []
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="echo", arguments={"x": 1})]),
        LLMResponse(text="ok"),
    ])
    loop, _ = _build(tmp_path, llm=llm, tools=_echo_registry(calls))
    out = loop.run("hi")  # no on_tool
    assert out == "ok"
    assert calls == [{"x": 1}]
