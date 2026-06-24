"""End-to-end tests for the citation-grounding hooks through the real AgentLoop.

These assert the *integration* the 5-round Qwen3.6 benchmark motivated:

  - record_grounding (PostToolUse) and enforce_grounding (PreResponse) share
    per-turn state via the loop's `turn_state` dict ([A2] in core/loop.py);
  - a final answer that cites a section never retrieved this turn is BLOCKed
    deterministically (fail-closed), regardless of what the model "intended".

We use the real PythonRunner (not a fake) so the test exercises the same code
path production does: the loop builds the payloads, threads turn_state, and the
engine fires the shipped hooks by import target.
"""

from __future__ import annotations

import json
from pathlib import Path

from pyclaw.core.llm import LLMResponse, ToolCall
from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.hooks.engine import HookEngine, HookSpec
from pyclaw.hooks.events import HookEvent
from pyclaw.hooks.runners import RunnerType
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.core.loop import AgentLoop

from dataclasses import dataclass, field


@dataclass
class FakeLLM:
    script: list[LLMResponse]
    _i: int = 0

    def complete(self, messages, tools=None, model=None, temperature=0.0):  # noqa: ANN001
        # Clamp at the last scripted response: once the script is exhausted the
        # fake keeps replaying its final answer. This models a STUBBORN model
        # for the in-run block-retry path (loop feeds the block reason back and
        # asks again) — if the model keeps citing the same ungrounded id, the
        # loop must still terminate at RESPONSE_BLOCKED after BLOCK_RETRY_LIMIT.
        resp = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return resp


def _grounding_engine() -> HookEngine:
    """A real engine with the two shipped grounding hooks (real PythonRunner)."""
    eng = HookEngine()
    eng.register(HookSpec(name="record-grounding", event=HookEvent.POST_TOOL_USE,
                          runner=RunnerType.PYTHON,
                          target="pyclaw_hooks_pdpa.grounding:record_grounding", priority=50))
    eng.register(HookSpec(name="enforce-grounding", event=HookEvent.PRE_RESPONSE,
                          runner=RunnerType.PYTHON,
                          target="pyclaw_hooks_pdpa.grounding:enforce_grounding", priority=50))
    return eng


def _section_registry(found_ids: set[str]) -> ToolRegistry:
    """A get_section_text tool that reports found=true only for `found_ids`."""
    reg = ToolRegistry()

    def get_section_text(args):
        sid = args.get("section_id") or f"sec_{args.get('number')}"
        if sid in found_ids:
            return {"found": True, "section_id": sid, "text": f"<{sid} body>"}
        return {"found": False, "section_id": sid}

    reg.register(Tool(name="get_section_text",
                      description="retrieve a PDPA section", fn=get_section_text))
    return reg


def _build(tmp_path: Path, *, llm, hooks, tools):
    audit = AuditLog(path=tmp_path / "audit.jsonl")
    return AgentLoop(
        llm=llm, hooks=hooks, context=ContextManager(), audit=audit,
        hitl=HITLGate(prompt_fn=lambda req: True),
        permissions=PermissionPolicy(), tools=tools,
    )


# --- cited-but-not-grounded -> BLOCK -----------------------------------------
def test_cites_ungrounded_section_is_blocked(tmp_path: Path) -> None:
    """Retrieve sec_39, then answer citing sec_27 (never retrieved) -> BLOCK."""
    llm = FakeLLM(script=[
        # round 1: model retrieves sec_39 only
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="get_section_text", arguments={"section_id": "sec_39"})]),
        # round 2: model answers citing BOTH ม.27 and ม.39 (27 was never grounded)
        LLMResponse(text="ตามมาตรา 27 และ ม.39 ผู้ควบคุมข้อมูลต้องแจ้งเหตุภายใน 72 ชั่วโมง"),
    ])
    loop = _build(tmp_path, llm=llm, hooks=_grounding_engine(),
                  tools=_section_registry({"sec_39"}))
    out = loop.run("ถามเรื่องการแจ้งเหตุละเมิด")
    # fail-closed: the response is blocked because sec_27 was never retrieved.
    assert out == "[response blocked by policy]"


# --- only-grounded citations -> ALLOW ----------------------------------------
def test_cites_only_grounded_sections_passes(tmp_path: Path) -> None:
    """Retrieve sec_39, answer citing only ม.39 -> ALLOW (passes through)."""
    answer = "ตาม ม.39 ผู้ควบคุมข้อมูลต้องจัดทำบันทึกรายการกิจกรรม"
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="get_section_text", arguments={"section_id": "sec_39"})]),
        LLMResponse(text=answer),
    ])
    loop = _build(tmp_path, llm=llm, hooks=_grounding_engine(),
                  tools=_section_registry({"sec_39"}))
    out = loop.run("ถามเรื่อง ROPA")
    assert out == answer  # not blocked


# --- no citations at all -> ALLOW --------------------------------------------
def test_answer_without_citations_passes(tmp_path: Path) -> None:
    llm = FakeLLM(script=[LLMResponse(text="สวัสดีครับ ต้องการสอบถามเรื่องใด")])
    loop = _build(tmp_path, llm=llm, hooks=_grounding_engine(),
                  tools=_section_registry(set()))
    out = loop.run("สวัสดี")
    assert out == "สวัสดีครับ ต้องการสอบถามเรื่องใด"


# --- per-turn isolation: two runs do not leak grounded state -----------------
def test_grounded_state_does_not_leak_between_runs(tmp_path: Path) -> None:
    """Run 1 grounds sec_39. Run 2 (fresh) cites ม.39 WITHOUT retrieving -> BLOCK.

    Proves turn_state is per-run: the second run must not 'inherit' run 1's
    grounded set (the bug the old global fallback would have caused).
    """
    eng = _grounding_engine()
    tools = _section_registry({"sec_39"})

    # Run 1: ground sec_39 and answer cleanly.
    llm1 = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="get_section_text", arguments={"section_id": "sec_39"})]),
        LLMResponse(text="ตาม ม.39 ..."),
    ])
    loop1 = _build(tmp_path, llm=llm1, hooks=eng, tools=tools)
    assert loop1.run("turn 1") == "ตาม ม.39 ..."

    # Run 2: brand-new loop/context, cite ม.39 with NO retrieval this turn.
    llm2 = FakeLLM(script=[LLMResponse(text="ตาม ม.39 อีกครั้งโดยไม่ดึง")])
    loop2 = _build(tmp_path, llm=llm2, hooks=eng, tools=tools)
    out2 = loop2.run("turn 2")
    assert out2 == "[response blocked by policy]"  # must NOT inherit run 1 state


# === gap coverage the 5-round benchmark exposed (the holes that leaked ม.84) ==
#
# These exercise the PDPA wrapper's vocabulary directly (chained citations, Thai
# numerals, the MCP envelope, and the scoping no-op) — the cases the first
# single-number regex / flat-dict parser silently dropped, letting an answer
# cite a section that was never actually grounded.

from pyclaw_hooks_pdpa.grounding import (  # noqa: E402
    canon as _pdpa_canon,
    enforce_grounding as _pdpa_enforce,
    record_grounding as _pdpa_record,
)
from pyclaw_hooks.grounding import make_extract_ids  # noqa: E402
from pyclaw.hooks.events import HookAction, HookPayload  # noqa: E402

_extract = make_extract_ids(
    __import__("pyclaw_hooks_pdpa.grounding", fromlist=["PATTERNS"]).PATTERNS,
    _pdpa_canon,
)


# --- chained "ม.83/84" must expand to BOTH sec_83 and sec_84 ------------------
def test_chained_slash_citation_expands_to_both() -> None:
    """The exact ม.84-leak shape: "ม.83/84" must yield {sec_83, sec_84}.

    The first regex matched only the first number (sec_83), so a draft citing
    "ม.83/84" while only sec_83 was grounded slipped through — sec_84 was
    invisible to the extractor. Now both are extracted."""
    assert _extract("บทลงโทษตาม ม.83/84") == {"sec_83", "sec_84"}


def test_chained_list_citation_expands_to_all() -> None:
    assert _extract("อาศัย มาตรา 30, 31, 32") == {"sec_30", "sec_31", "sec_32"}
    assert _extract("ตาม ม.27 และ 39") == {"sec_27", "sec_39"}


# --- Thai-numeral citations must canonicalise to Arabic ----------------------
def test_thai_numeral_citation_canonicalises() -> None:
    assert _extract("ตามมาตรา ๒๗") == {"sec_27"}
    assert _extract("ม.๓๙") == {"sec_39"}
    assert _extract("บทลงโทษ ม.๘๓/๘๔") == {"sec_83", "sec_84"}


# --- record_grounding must parse a real MCP envelope (Hole 1) ----------------
def test_record_grounding_parses_mcp_envelope() -> None:
    """Real MCP results arrive wrapped, with the useful fields inside a JSON
    string — not as a flat dict. The flat-dict-only parser saw None for every
    field, so the grounded set stayed empty and grounding never fired against a
    live MCP server. The wrapper must unwrap the envelope and ground sec_21."""
    envelope = {
        "content": [{"type": "text",
                     "text": json.dumps({"found": True, "section_id": "sec_21",
                                         "text": "..."})}],
        "structuredContent": {"result": json.dumps(
            {"found": True, "section_id": "sec_21", "text": "..."})},
        "isError": False,
    }
    state: dict = {}
    res = _pdpa_record(HookPayload(
        event=HookEvent.POST_TOOL_USE, tool="pdpa_get_section_text",
        result=envelope, extra=state))
    assert res.action == HookAction.ALLOW
    assert state["grounded_sections"] == {"sec_21"}


# --- scoping: a non-PDPA draft (no ม./มาตรา) must be a pure no-op ALLOW -------
def test_enforce_is_noop_for_non_pdpa_draft() -> None:
    """Loading the PDPA plugin must NOT block an unrelated agent. A draft that
    cites only English "section 3" / "sec_12" matches no Thai keyword, so the
    hook returns ALLOW without inspecting the grounded set — proving (ก) keeps
    the plugin from over-reaching."""
    res = _pdpa_enforce(HookPayload(
        event=HookEvent.PRE_RESPONSE,
        arguments={"text": "See section 3 of the config and the sec_12 variable."},
        extra={}))
    assert res.action == HookAction.ALLOW
