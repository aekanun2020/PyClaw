"""[Observability / fix B follow-up] Bubble the BLOCK reason up to --trace.

Background — fix B replaced a blocked route's `summary` with generic
`_BLOCKED_GUIDANCE` so the orchestrator LLM changes strategy instead of retrying
the same question reworded. That was correct for STEERING the LLM, but it also
erased the diagnostic detail: the enforce hook's raw message (e.g. "cites
[sec_24, sec_26] but the retrieval tool was never called for them") never
reached the orchestrator trace, so you couldn't tell WHY a route was blocked.

This adds a MECHANISM-ONLY diagnostic channel that runs ALONGSIDE the steering
summary, never replacing it:

    loop._finalize  --(turn_state['block_detail'])-->  isolated runner
        --(SubagentResult.block_detail)-->  RouteResult.block_detail
        --(_result_dict 'block_detail', block-only)-->  --trace

Invariants pinned here:
  * the user-facing answer is STILL the opaque RESPONSE_BLOCKED sentinel
    (no leak of the cited-but-ungrounded content);
  * the LLM-facing `summary` is STILL `_BLOCKED_GUIDANCE` (steering unchanged);
  * `block_detail` carries the raw reason and appears ONLY on a block;
  * the breaker-refused path carries its own refusal reason as block_detail;
  * a non-blocked route exposes NO block_detail key at all.

The detail is an opaque string keyed in a generic dict at every hop — no domain
vocabulary, no PDPA pattern. The same channel would carry a medical/finance
enforce message identically.
"""

from __future__ import annotations

from pathlib import Path

from pyclaw.core.loop import BLOCK_DETAIL_KEY, RESPONSE_BLOCKED
from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec
from pyclaw.orchestrator.runner import OrchestratorRunner, RouteResult
from pyclaw.orchestrator.tool import _BLOCKED_GUIDANCE, _result_dict
from pyclaw.subagents.runner import SubagentRunner
from pyclaw.subagents.types import SubagentSpec, SubagentType

REPO_ROOT = Path(__file__).resolve().parent.parent

# A realistic enforce-hook message — the exact shape make_enforce_grounding emits.
_ENFORCE_MSG = (
    "citation-grounding violation: the answer cites ['sec_24', 'sec_26'] but the "
    "retrieval tool was never called for them. Retrieve each missing item, then "
    "re-answer. (grounded this turn: ['sec_27'])"
)


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.add(AgentSpec(name="db-agent", description="HR DB",
                      tool_prefixes=("db_",),
                      home=REPO_ROOT / "agents" / "db-agent"))
    reg.add(AgentSpec(name="pdpa-agent", description="PDPA law",
                      tool_prefixes=("pdpa_",),
                      home=REPO_ROOT / "agents" / "pdpa-agent"))
    return reg


def _scripted_runner(summary: str, *, block_detail: str | None = None):
    """An injected run_isolated stub that mimics a REAL isolated runner.

    The real `build_isolated_runner` returns a callable carrying `last_grounded`
    and `last_block_detail` attributes that `SubagentRunner.spawn` reads via
    getattr. We reproduce that contract so the bubble path is exercised end to
    end (loop omitted, but the runner->result->route->dict hops are real).
    """
    def run_isolated(spec, on_tool=None):
        return summary
    run_isolated.last_grounded = set()
    run_isolated.last_block_detail = block_detail
    return run_isolated


def _orch(summary: str, *, block_detail: str | None = None, **kw) -> OrchestratorRunner:
    return OrchestratorRunner(
        registry=_registry(),
        run_isolated=_scripted_runner(summary, block_detail=block_detail),
        available_tools=("pdpa_get_section_text", "db_q"),
        **kw,
    )


# =====================================================================
# Hop 1 — the isolated loop stashes the BLOCKing hook message in turn_state,
# and SubagentRunner.spawn bubbles it onto SubagentResult.block_detail.
# =====================================================================
def test_subagent_result_carries_block_detail_from_runner() -> None:
    runner = SubagentRunner(
        parent_tools=("pdpa_get_section_text",),
        run_isolated=_scripted_runner(RESPONSE_BLOCKED, block_detail=_ENFORCE_MSG),
    )
    res = runner.spawn(SubagentSpec(type=SubagentType.GENERAL, objective="q"))
    assert res.summary == RESPONSE_BLOCKED
    assert res.block_detail == _ENFORCE_MSG


def test_subagent_result_block_detail_none_when_not_blocked() -> None:
    runner = SubagentRunner(
        parent_tools=("pdpa_get_section_text",),
        run_isolated=_scripted_runner("ตาม ม.27 ...", block_detail=None),
    )
    res = runner.spawn(SubagentSpec(type=SubagentType.GENERAL, objective="q"))
    assert res.block_detail is None


# =====================================================================
# Hop 2+3 — RouteResult.block_detail is populated only on a block, and the
# tool dict surfaces it as a SEPARATE key from the steering summary.
# =====================================================================
def test_blocked_route_surfaces_detail_alongside_guidance() -> None:
    runner = _orch(RESPONSE_BLOCKED, block_detail=_ENFORCE_MSG)
    r = runner.route_one("pdpa-agent", "CCTV สอบสวนวินัย อ้างมาตราใด")

    assert r.blocked is True and r.ok is False
    # Raw reason preserved on the RouteResult...
    assert r.block_detail == _ENFORCE_MSG

    d = _result_dict(r)
    # Steering channel UNCHANGED — LLM still sees the generic change-strategy text.
    assert d["summary"] == _BLOCKED_GUIDANCE
    # Diagnostic channel present and SEPARATE, carrying the real reason.
    assert d["block_detail"] == _ENFORCE_MSG
    # No leak: the sentinel and guidance never contain the cited-but-ungrounded
    # content; the detail is the only place the ids appear.
    assert "sec_24" in d["block_detail"] and "sec_24" not in d["summary"]


def test_non_blocked_route_omits_block_detail_key() -> None:
    runner = _orch("ตาม ม.21 และ ม.39 ...", block_detail=None)
    r = runner.route_one("pdpa-agent", "อธิบาย ม.21")

    assert r.blocked is False
    assert r.block_detail is None
    d = _result_dict(r)
    # The key is omitted entirely for a normal answer (no noise in the trace).
    assert "block_detail" not in d
    assert d["summary"] == "ตาม ม.21 และ ม.39 ..."


def test_blocked_without_detail_omits_key_gracefully() -> None:
    """A block whose runner exposed no detail (older/test runner) must still be
    a clean block — the key is simply omitted, never an empty/None entry."""
    runner = _orch(RESPONSE_BLOCKED, block_detail=None)
    r = runner.route_one("pdpa-agent", "q")
    assert r.blocked is True and r.block_detail is None
    d = _result_dict(r)
    assert "block_detail" not in d
    assert d["summary"] == _BLOCKED_GUIDANCE  # steering still intact


# =====================================================================
# Breaker-refused path — the refusal reason IS the diagnostic detail, so a
# tripped breaker is still explainable in --trace (no silent 0.00s block).
# =====================================================================
def test_breaker_refusal_carries_its_reason_as_block_detail() -> None:
    runner = _orch(RESPONSE_BLOCKED, block_detail=_ENFORCE_MSG)  # limit=2 default
    runner.route_one("pdpa-agent", "q1")  # block (streak=1)
    runner.route_one("pdpa-agent", "q2")  # block (streak=2)
    r3 = runner.route_one("pdpa-agent", "q3")  # refused before spawn

    assert r3.blocked is True and r3.ok is False
    assert r3.error is not None and "[blocked-limit]" in r3.error
    # The breaker refusal is self-explaining: detail mirrors the refusal reason.
    assert r3.block_detail is not None and "[blocked-limit]" in r3.block_detail
    d = _result_dict(r3)
    assert "[blocked-limit]" in d["block_detail"]


# =====================================================================
# The detail key constant is the SAME one the core writes — no duplicated
# literal across layers (mechanism contract).
# =====================================================================
def test_block_detail_key_constant_is_shared() -> None:
    assert BLOCK_DETAIL_KEY == "block_detail"
    # RouteResult / tool dict use the same field name, so a turn_state written by
    # core surfaces under the identical key downstream.
    r = RouteResult(agent="pdpa-agent", message="q", summary=RESPONSE_BLOCKED,
                    ok=False, blocked=True, block_detail="x")
    assert "block_detail" in _result_dict(r)
