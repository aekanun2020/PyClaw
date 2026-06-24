"""[Hole 2] Grounding in orchestrator / subagent mode (option B + รู ก).

Background — the grounding hooks only ever fired in the flat `pyclaw chat` loop.
In `--orchestrator` mode there were TWO unguarded gaps:

  รู ข (subagent): each routed agent ran in an isolated loop with an EMPTY
      HookEngine (`build_isolated_runner` hard-coded `hooks=HookEngine()`), so
      even the pdpa-agent that retrieved correctly was never enforced.

  รู ก (orchestrator): the orchestrator assembles a NEW combined answer from the
      agents' summaries, but its own HookEngine was empty and it had no
      PreResponse enforce — the combined answer (which leaked ม.84 in the CCTV
      case) was unguarded.

The fix is MECHANISM-only and matches the project architecture rule
("grounding ควรผูกกับ agent ที่เกี่ยวข้อง ไม่ยัดเข้า global engine"):

  * รู ข closed by OPTION B — each agent loads its OWN
    `<home>/plugins/*/plugin.yaml` into its own isolated engine
    (`SubagentSpec.plugins_root`). The pdpa-agent self-enforces inside its loop;
    db-agent/rag-agent have no such dir -> empty engine -> NOT affected by PDPA
    (domains isolated by SEPARATE engines, not by pattern luck).

  * รู ก closed by bubbling each agent's grounded ids up
    (SubagentResult.grounded -> RouteResult.grounded -> route_to_agent result),
    plus an orchestrator-level plugin (merge + enforce) loaded into the
    orchestrator engine. merge unions the agents' grounded ids into the turn;
    enforce BLOCKs the combined answer on any cited-but-ungrounded section.

These tests pin all three behaviours end-to-end through the REAL machinery
(real PluginLoader, real AgentLoop, real OrchestratorRunner) with a fake LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyclaw.config import SETTINGS
from pyclaw.core.llm import LLMResponse, ToolCall
from pyclaw.core.loop import AgentLoop
from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.hooks.engine import HookEngine
from pyclaw.hooks.events import HookEvent
from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec
from pyclaw.orchestrator.runner import OrchestratorRunner
from pyclaw.orchestrator.tool import ROUTE_TOOL_NAME, make_route_to_agent_tool
from pyclaw.plugins.loader import PluginLoader
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate
from pyclaw.subagents.runner import build_isolated_runner
from pyclaw.subagents.types import SubagentSpec, SubagentType

REPO_ROOT = Path(__file__).resolve().parent.parent
PDPA_PLUGINS = REPO_ROOT / "agents" / "pdpa-agent" / "plugins"
ORCH_PLUGINS = SETTINGS.agent_dir / "orchestrator-plugins"


@dataclass
class FakeLLM:
    script: list[LLMResponse]
    _i: int = 0
    # build_isolated_runner reads `OpenRouterProvider().model` as the default
    # model name, so the fake needs the attribute even though it is unused.
    model: str = "fake-model"

    def complete(self, messages, tools=None, model=None, temperature=0.0):  # noqa: ANN001
        resp = self.script[self._i]
        self._i += 1
        return resp


def _pdpa_tools(found_ids: set[str]) -> ToolRegistry:
    """A pdpa_get_section_text tool reporting found=true only for found_ids."""
    reg = ToolRegistry()

    def pdpa_get_section_text(args):
        sid = args.get("section_id") or f"sec_{args.get('number')}"
        if sid in found_ids:
            return {"found": True, "section_id": sid, "text": f"<{sid} body>"}
        return {"found": False, "section_id": sid}

    reg.register(Tool(name="pdpa_get_section_text",
                      description="retrieve a PDPA section", fn=pdpa_get_section_text))
    return reg


# =====================================================================
# (0) the per-agent / orchestrator manifests must be discoverable & wired
# =====================================================================
def test_pdpa_agent_plugin_dir_wires_record_and_enforce_only() -> None:
    """The pdpa-agent's per-agent manifest carries ONLY record + enforce
    (no merge — a leaf agent retrieves directly, it never delegates)."""
    eng = HookEngine()
    PluginLoader(plugins_root=PDPA_PLUGINS,
                 installed_versions={"core": "0.1.0"}).load_all(hooks=eng)
    by_name = {s.name: s for s in eng._hooks}
    assert "record-grounding" in by_name
    assert "enforce-grounding" in by_name
    assert "merge-grounding" not in by_name  # leaf agent must not merge
    assert by_name["record-grounding"].event == HookEvent.POST_TOOL_USE
    assert by_name["enforce-grounding"].event == HookEvent.PRE_RESPONSE


def test_orchestrator_plugin_dir_wires_merge_and_enforce_only() -> None:
    """The orchestrator manifest carries merge + enforce (no record — the
    orchestrator owns no domain tool, it never retrieves directly)."""
    eng = HookEngine()
    PluginLoader(plugins_root=ORCH_PLUGINS,
                 installed_versions={"core": "0.1.0"}).load_all(hooks=eng)
    by_name = {s.name: s for s in eng._hooks}
    assert "merge-grounding" in by_name
    assert "enforce-grounding" in by_name
    assert "record-grounding" not in by_name
    assert by_name["merge-grounding"].event == HookEvent.POST_TOOL_USE
    assert by_name["enforce-grounding"].event == HookEvent.PRE_RESPONSE


# =====================================================================
# (1) รู ข — a routed subagent self-enforces inside its OWN isolated loop
# =====================================================================
def _registry() -> AgentRegistry:
    """db-agent has NO plugins dir; pdpa-agent points at its real plugins dir."""
    reg = AgentRegistry()
    reg.add(AgentSpec(name="db-agent", description="HR DB",
                      tool_prefixes=("db_",),
                      home=REPO_ROOT / "agents" / "db-agent"))
    reg.add(AgentSpec(name="pdpa-agent", description="PDPA law",
                      tool_prefixes=("pdpa_",),
                      home=REPO_ROOT / "agents" / "pdpa-agent"))
    return reg


def test_pdpa_agent_spec_carries_its_plugins_root() -> None:
    """_spec_for must set plugins_root for the pdpa-agent (which has a dir)
    and leave it None for the db-agent (which does not)."""
    runner = OrchestratorRunner(registry=_registry(),
                                run_isolated=lambda spec, on_tool=None: "ok",
                                available_tools=("db_q", "pdpa_get_section_text"))
    pdpa_spec = runner._spec_for("x", runner.registry.get("pdpa-agent"))
    db_spec = runner._spec_for("x", runner.registry.get("db-agent"))
    assert pdpa_spec.plugins_root == PDPA_PLUGINS
    assert db_spec.plugins_root is None


def test_subagent_self_enforces_via_per_agent_plugin(monkeypatch) -> None:
    """An isolated pdpa-agent loop (built by the REAL build_isolated_runner with
    plugins_root set) BLOCKs its own answer that cites an unretrieved section."""
    retrieved = {"sec_21", "sec_39"}
    llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="pdpa_get_section_text",
                     arguments={"section_id": "sec_21"}),
            ToolCall(id="2", name="pdpa_get_section_text",
                     arguments={"section_id": "sec_39"})]),
        # cites ม.84 — never retrieved -> the agent's OWN enforce must BLOCK.
        LLMResponse(text="ตาม ม.21 และ ม.39 ... และโทษตาม ม.84"),
    ])
    # Patch the loop's LLM/tools by injecting through the real factory's deps:
    # build_isolated_runner builds an OpenRouterProvider internally, so we drive
    # it via a fake by patching the provider class used inside _run.
    import pyclaw.core.llm as llm_mod
    monkeypatch.setattr(llm_mod, "OpenRouterProvider", lambda *a, **k: llm)

    runner = build_isolated_runner(lambda names: _pdpa_tools(retrieved))
    spec = SubagentSpec(
        type=SubagentType.GENERAL,
        objective="CCTV: HR ขอใช้ภาพสอบสวนวินัย อ้างมาตราใด",
        allowed_tools=("pdpa_get_section_text",),
        plugins_root=PDPA_PLUGINS,
    )
    out = runner(spec)
    assert out == "[response blocked by policy]", (
        "subagent cited ม.84 without retrieving sec_84 — its per-agent "
        "enforce-grounding should have blocked it (รู ข closed)"
    )
    # And the grounded set it DID retrieve is exposed for bubbling.
    assert runner.last_grounded == {"sec_21", "sec_39"}


def test_db_agent_is_not_affected_by_pdpa_grounding(monkeypatch) -> None:
    """db-agent has no plugins dir -> empty engine -> an answer that happens to
    contain a 'ม.NN'-looking string is NOT blocked (domains isolated by
    SEPARATE engines, not by pattern-scoping luck)."""
    llm = FakeLLM(script=[
        # No tools; just answers. A db summary could mention "ม.5" loosely.
        LLMResponse(text="พบพนักงาน 5 คน (อ้างอิงแถวที่ ม.5 ของตาราง)"),
    ])
    import pyclaw.core.llm as llm_mod
    monkeypatch.setattr(llm_mod, "OpenRouterProvider", lambda *a, **k: llm)

    runner = build_isolated_runner(lambda names: ToolRegistry())
    spec = SubagentSpec(
        type=SubagentType.GENERAL,
        objective="how many employees?",
        allowed_tools=(),
        plugins_root=None,  # db-agent: no grounding plugin
    )
    out = runner(spec)
    assert out.startswith("พบพนักงาน"), (
        "db-agent has no PDPA engine — its answer must pass through untouched"
    )
    assert runner.last_grounded == set()


# =====================================================================
# (2) รู ก — the orchestrator's COMBINED answer is enforced on the union
# =====================================================================
def _orchestrator_loop(tmp_path, *, orch_llm, sub_llm, found_ids, hooks):
    """Build a real orchestrator AgentLoop wired to a real OrchestratorRunner.

    The routed subagent runs through the REAL build_isolated_runner so it self-
    enforces AND bubbles its grounded set. `orch_llm` drives the orchestrator;
    `sub_llm` drives the routed agent's isolated loop.
    """
    import pyclaw.core.llm as llm_mod
    # The isolated subagent loop builds its own provider -> give it sub_llm.
    orig = llm_mod.OpenRouterProvider
    llm_mod.OpenRouterProvider = lambda *a, **k: sub_llm

    runner = OrchestratorRunner(
        registry=_registry(),
        tool_provider=lambda names: _pdpa_tools(found_ids),
        available_tools=("pdpa_get_section_text",),
    )
    orch_tools = ToolRegistry()
    orch_tools.register(make_route_to_agent_tool(runner))
    loop = AgentLoop(
        llm=orch_llm, hooks=hooks, context=ContextManager(),
        audit=AuditLog(path=tmp_path / "audit.jsonl"),
        hitl=HITLGate(prompt_fn=lambda req: True),
        permissions=PermissionPolicy(allowed_tools=frozenset({ROUTE_TOOL_NAME})),
        tools=orch_tools,
    )
    return loop, orig, llm_mod


def _orch_hooks() -> HookEngine:
    eng = HookEngine()
    PluginLoader(plugins_root=ORCH_PLUGINS,
                 installed_versions={"core": "0.1.0"}).load_all(hooks=eng)
    return eng


def test_orchestrator_combined_answer_blocked_on_ungrounded_section(tmp_path) -> None:
    """The pdpa-agent grounds sec_21/sec_39; the orchestrator's combined answer
    adds ม.84 (the CCTV leak). With merge+enforce loaded, the combined answer
    must be BLOCKed even though the section came from the orchestrator's own
    synthesis, not the agent (รู ก closed)."""
    found = {"sec_21", "sec_39"}
    # The routed agent retrieves sec_21/sec_39 and answers citing only those.
    sub_llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="pdpa_get_section_text",
                     arguments={"section_id": "sec_21"}),
            ToolCall(id="2", name="pdpa_get_section_text",
                     arguments={"section_id": "sec_39"})]),
        LLMResponse(text="ตาม ม.21 และบันทึกตาม ม.39"),
    ])
    # The orchestrator routes once, then writes a combined answer that LEAKS ม.84.
    orch_llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="r1", name=ROUTE_TOOL_NAME,
                     arguments={"agent": "pdpa-agent",
                                "message": "CCTV HR สอบสวนวินัย อ้างมาตราใด"})]),
        LLMResponse(text="สรุป: ใช้ได้ตาม ม.21 บันทึกตาม ม.39 และมีโทษตาม ม.84"),
    ])
    loop, orig, llm_mod = _orchestrator_loop(
        tmp_path, orch_llm=orch_llm, sub_llm=sub_llm, found_ids=found,
        hooks=_orch_hooks())
    try:
        out = loop.run("CCTV HR สอบสวนวินัย อ้างมาตราใด")
    finally:
        llm_mod.OpenRouterProvider = orig
    assert out == "[response blocked by policy]", (
        "orchestrator combined answer cited ม.84 which no agent grounded — "
        "merge+enforce should have blocked the COMBINED answer (รู ก)"
    )


def test_orchestrator_combined_answer_passes_when_all_cited_are_grounded(tmp_path) -> None:
    """Same wiring, but the combined answer cites only what the agent grounded
    (ม.21, ม.39) -> the merged union covers it -> ALLOW."""
    found = {"sec_21", "sec_39"}
    sub_llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="1", name="pdpa_get_section_text",
                     arguments={"section_id": "sec_21"}),
            ToolCall(id="2", name="pdpa_get_section_text",
                     arguments={"section_id": "sec_39"})]),
        LLMResponse(text="ตาม ม.21 และบันทึกตาม ม.39"),
    ])
    final = "สรุป: ใช้ได้ตาม ม.21 และต้องบันทึกตาม ม.39"
    orch_llm = FakeLLM(script=[
        LLMResponse(text="", tool_calls=[
            ToolCall(id="r1", name=ROUTE_TOOL_NAME,
                     arguments={"agent": "pdpa-agent",
                                "message": "CCTV HR สอบสวนวินัย อ้างมาตราใด"})]),
        LLMResponse(text=final),
    ])
    loop, orig, llm_mod = _orchestrator_loop(
        tmp_path, orch_llm=orch_llm, sub_llm=sub_llm, found_ids=found,
        hooks=_orch_hooks())
    try:
        out = loop.run("CCTV HR สอบสวนวินัย อ้างมาตราใด")
    finally:
        llm_mod.OpenRouterProvider = orig
    assert out == final, (
        "every cited section (ม.21, ม.39) was grounded by the agent and merged "
        "into the orchestrator turn — the combined answer must pass"
    )
