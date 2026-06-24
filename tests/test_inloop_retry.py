"""Fix C — in-loop feedback-retry on a PreResponse BLOCK (core mechanism).

When a PreResponse hook BLOCKs a draft, the loop no longer fails the whole run.
It feeds the hook's RAW message back into the conversation and lets the model
try again WITHIN THE SAME RUN, bounded by BLOCK_RETRY_LIMIT. This is the root
fix for the "agent retrieved the right sources but cited an extra ungrounded
one -> whole answer thrown away -> orchestrator breaker trips" failure.

The hooks under test (pyclaw.core._test_retry_hooks) are DOMAIN-FREE — they
key off a generic "BADID" token, not PDPA section numbers — proving the loop
only ever sees `HookAction.BLOCK` + an opaque `message` and never parses it.
Any domain's enforce hook therefore gets this self-correction loop for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pyclaw.core._test_retry_hooks import BLOCK_MESSAGE
from pyclaw.core.llm import LLMResponse, ToolCall
from pyclaw.core.loop import AgentLoop, BLOCK_RETRY_LIMIT, RESPONSE_BLOCKED
from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.hooks.engine import HookEngine, HookSpec
from pyclaw.hooks.events import HookEvent
from pyclaw.hooks.runners import RunnerType
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate

_HOOKS = "pyclaw.core._test_retry_hooks"


@dataclass
class FakeLLM:
    """Scriptable model. Clamps at its last response when the script runs out,
    modelling a STUBBORN model so the retry-guard can be exercised. Records the
    messages it was sent so a test can assert the feedback was injected."""
    script: list[LLMResponse]
    _i: int = 0
    calls: list[list[dict]] = field(default_factory=list)

    def complete(self, messages, tools=None, model=None, temperature=0.0):  # noqa: ANN001
        self.calls.append(list(messages))
        resp = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return resp


def _engine() -> HookEngine:
    eng = HookEngine()
    eng.register(HookSpec(name="rec", event=HookEvent.POST_TOOL_USE,
                          runner=RunnerType.PYTHON, target=f"{_HOOKS}:record", priority=50))
    eng.register(HookSpec(name="enf", event=HookEvent.PRE_RESPONSE,
                          runner=RunnerType.PYTHON, target=f"{_HOOKS}:enforce", priority=50))
    return eng


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(name="retrieve", description="retrieve an id",
                      fn=lambda args: {"found": True, "id": args.get("id")}))
    return reg


def _build(tmp_path: Path, *, llm):
    return AgentLoop(
        llm=llm, hooks=_engine(), context=ContextManager(),
        audit=AuditLog(path=tmp_path / "audit.jsonl"),
        hitl=HITLGate(prompt_fn=lambda req: True),
        permissions=PermissionPolicy(), tools=_registry(),
    )


# --- 1. headline win: block -> feedback -> self-correct -> PASS --------------
def test_block_then_retrieve_then_pass_within_one_run(tmp_path: Path) -> None:
    good = "answer grounded on BADID now"
    llm = FakeLLM(script=[
        LLMResponse(text="answer about BADID"),                      # cite, not retrieved -> BLOCK
        LLMResponse(text="", tool_calls=[                            # post-feedback: retrieve it
            ToolCall(id="1", name="retrieve", arguments={"id": "BADID"})]),
        LLMResponse(text=good),                                      # re-answer grounded -> ALLOW
    ])
    out = _build(tmp_path, llm=llm).run("ask")
    assert out == good  # the run RECOVERED instead of failing


def test_feedback_message_is_injected_verbatim(tmp_path: Path) -> None:
    llm = FakeLLM(script=[
        LLMResponse(text="answer about BADID"),
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="retrieve", arguments={"id": "BADID"})]),
        LLMResponse(text="grounded BADID"),
    ])
    _build(tmp_path, llm=llm).run("ask")
    # The 2nd model call (the retry) must carry the exact opaque block message.
    assert any(BLOCK_MESSAGE in str(m.get("content", "")) for m in llm.calls[1])


# --- 2. guard: a stubborn model is bounded by BLOCK_RETRY_LIMIT --------------
def test_stubborn_model_returns_sentinel_after_limit(tmp_path: Path) -> None:
    llm = FakeLLM(script=[LLMResponse(text="still about BADID")])  # clamps -> repeats
    loop = _build(tmp_path, llm=llm)
    assert loop.run("ask") == RESPONSE_BLOCKED
    # initial attempt + BLOCK_RETRY_LIMIT feedback attempts
    assert llm._i == BLOCK_RETRY_LIMIT + 1


def test_block_detail_preserved_when_budget_exhausted(tmp_path: Path) -> None:
    llm = FakeLLM(script=[LLMResponse(text="about BADID forever")])
    loop = _build(tmp_path, llm=llm)
    loop.run("ask")
    assert loop.last_turn_state is not None
    assert loop.last_turn_state.get("block_detail") == BLOCK_MESSAGE


# --- 3. no regression on the happy paths -------------------------------------
def test_clean_answer_passes_without_retry(tmp_path: Path) -> None:
    llm = FakeLLM(script=[LLMResponse(text="a perfectly fine answer")])
    loop = _build(tmp_path, llm=llm)
    assert loop.run("ask") == "a perfectly fine answer"
    assert llm._i == 1  # one model call, no retry


def test_successful_retry_does_not_leak_block_detail(tmp_path: Path) -> None:
    llm = FakeLLM(script=[
        LLMResponse(text="answer about BADID"),
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="retrieve", arguments={"id": "BADID"})]),
        LLMResponse(text="grounded BADID"),
    ])
    loop = _build(tmp_path, llm=llm)
    loop.run("ask")
    assert loop.last_turn_state is not None
    assert "block_detail" not in loop.last_turn_state
