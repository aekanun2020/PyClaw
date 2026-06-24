"""[A4] Tests that the shipped hooks register through the LIVE startup path.

Root cause of the CCTV regression: the hook specs lived in
`.agent/hooks/default_hooks.yaml`, but the only path that registers hooks at
startup is `PluginLoader.load_all()` (cli.py:100), which scans
`.agent/plugins/*/plugin.yaml` (loader.py:108,176). With no plugin manifest,
HookEngine started empty and every answer passed ungated — so an answer citing
ม.23 (sec_23) without ever retrieving it was NOT blocked.

These tests pin two invariants:
  1. Loading the real `.agent/plugins/` tree registers the grounding hooks on
     the correct events (so this never silently regresses to an empty engine).
  2. With those hooks live, the exact CCTV failure (cite sec_23 without
     retrieval) is BLOCKed fail-closed end-to-end through the real AgentLoop.
"""

from __future__ import annotations

from pathlib import Path

from pyclaw.config import SETTINGS
from pyclaw.core.llm import LLMResponse, ToolCall
from pyclaw.core.loop import AgentLoop
from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.hooks.engine import HookEngine
from pyclaw.hooks.events import HookEvent
from pyclaw.plugins.loader import PluginLoader
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate

from dataclasses import dataclass


@dataclass
class FakeLLM:
    script: list[LLMResponse]
    _i: int = 0

    def complete(self, messages, tools=None, model=None, temperature=0.0):  # noqa: ANN001
        resp = self.script[self._i]
        self._i += 1
        return resp


def _load_via_plugin_path() -> HookEngine:
    """Register hooks exactly the way cli.py does at startup (A4 live path)."""
    eng = HookEngine()
    root = SETTINGS.agent_dir / "plugins"
    loader = PluginLoader(plugins_root=root, installed_versions={"core": "0.1.0"})
    loader.load_all(hooks=eng)
    return eng


# --- (1) the live path must register the grounding hooks on the right events --
def test_plugin_path_registers_grounding_hooks() -> None:
    eng = _load_via_plugin_path()
    by_name = {s.name: s for s in eng._hooks}

    # The A4 manifest must be discoverable and wire BOTH grounding hooks live.
    assert "record-grounding" in by_name, (
        "record-grounding not registered — .agent/plugins/*/plugin.yaml missing "
        "or not on the load path (this is the CCTV root cause)"
    )
    assert "enforce-grounding" in by_name

    assert by_name["record-grounding"].event == HookEvent.POST_TOOL_USE
    assert by_name["enforce-grounding"].event == HookEvent.PRE_RESPONSE
    assert by_name["record-grounding"].target == "pyclaw_hooks.grounding:record_grounding"
    assert by_name["enforce-grounding"].target == "pyclaw_hooks.grounding:enforce_grounding"

    # And the engine must actually surface them per-event (not just hold specs).
    assert any(s.name == "enforce-grounding"
               for s in eng.hooks_for(HookEvent.PRE_RESPONSE))


def _pdpa_registry(found_ids: set[str]) -> ToolRegistry:
    """A pdpa_get_section_text tool (user's real MCP tool name) reporting
    found=true only for `found_ids`."""
    reg = ToolRegistry()

    def pdpa_get_section_text(args):
        sid = args.get("section_id") or f"sec_{args.get('number')}"
        if sid in found_ids:
            return {"found": True, "section_id": sid, "text": f"<{sid} body>"}
        return {"found": False, "section_id": sid}

    reg.register(Tool(name="pdpa_get_section_text",
                      description="retrieve a PDPA section", fn=pdpa_get_section_text))
    return reg


def _build(tmp_path: Path, *, llm, hooks, tools):
    return AgentLoop(
        llm=llm, hooks=hooks, context=ContextManager(),
        audit=AuditLog(path=tmp_path / "audit.jsonl"),
        hitl=HITLGate(prompt_fn=lambda req: True),
        permissions=PermissionPolicy(), tools=tools,
    )


# --- (2) reproduce the exact CCTV failure end-to-end through the live path ----
def test_cctv_trace_cites_sec23_without_retrieval_is_blocked(tmp_path: Path) -> None:
    """Mirrors the pasted CCTV trace: retrieve sec_21/24/27/37/39 via
    pdpa_get_section_text, then answer citing ม.23 (never retrieved) -> BLOCK.

    Before A4 this answer passed (engine empty). With the plugin loaded it must
    be blocked fail-closed."""
    retrieved = {"sec_21", "sec_24", "sec_27", "sec_37", "sec_39"}
    llm = FakeLLM(script=[
        # round 1: retrieve the sections the agent actually looked up
        LLMResponse(text="", tool_calls=[
            ToolCall(id=str(i), name="pdpa_get_section_text",
                     arguments={"section_id": sid})
            for i, sid in enumerate(sorted(retrieved), start=1)]),
        # round 2: final answer cites ม.21, ม.24, ม.27, ม.37, ม.39 AND ม.23.
        # ม.23 (sec_23) was never retrieved -> must trip enforce-grounding.
        LLMResponse(text=(
            "ตาม ม.21 ต้องใช้ตามวัตถุประสงค์ที่แจ้ง โดยอาศัยฐาน ม.24(5); "
            "การใช้/เปิดเผยอยู่ภายใต้ ม.27 และต้องมีมาตรการตาม ม.37 "
            "พร้อมบันทึกตาม ม.39 และต้องแจ้งรายละเอียดตาม ม.23 ด้วย")),
    ])
    loop = _build(tmp_path, llm=llm, hooks=_load_via_plugin_path(),
                  tools=_pdpa_registry(retrieved))
    out = loop.run("CCTV ที่ทำงาน HR ขอใช้ภาพสอบสวนวินัย ทำได้ไหม อ้างมาตราใด")
    assert out == "[response blocked by policy]", (
        "answer cited ม.23 without retrieving sec_23 — enforce-grounding should "
        "have blocked it fail-closed"
    )


# --- (3) sanity: an answer citing only retrieved sections passes the live path
def test_cctv_only_grounded_citations_pass(tmp_path: Path) -> None:
    retrieved = {"sec_21", "sec_24", "sec_27", "sec_37", "sec_39"}
    answer = ("ตาม ม.21 ใช้ตามวัตถุประสงค์ที่แจ้ง อาศัย ม.24(5); "
              "ภายใต้ ม.27 มีมาตรการตาม ม.37 และบันทึกตาม ม.39")
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id=str(i), name="pdpa_get_section_text",
                     arguments={"section_id": sid})
            for i, sid in enumerate(sorted(retrieved), start=1)]),
        LLMResponse(text=answer),
    ])
    loop = _build(tmp_path, llm=llm, hooks=_load_via_plugin_path(),
                  tools=_pdpa_registry(retrieved))
    out = loop.run("CCTV ที่ทำงาน อ้างมาตราใด")
    assert out == answer  # every cited section was retrieved -> ALLOW
