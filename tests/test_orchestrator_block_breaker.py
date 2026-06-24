"""[Retry storm / option B] Orchestrator handling of BLOCKED routes.

Background — grounding enforcement BLOCKs an ungrounded agent answer, replacing
it with the core sentinel `RESPONSE_BLOCKED`. Two real traces (identical code)
showed the orchestrator LLM treating that blocked result as an ordinary outcome
and RE-ASKING the same sub-question reworded ~15 times — a non-deterministic
"retry storm" that occasionally happened to leak a combined answer.

The fix is MECHANISM-only (no domain vocabulary), in three layers:

  1. core exports the blocked sentinel as `RESPONSE_BLOCKED` (detect by identity,
     not a duplicated magic string).
  2. a blocked route is reported as a FAILED attempt: `RouteResult.blocked=True`
     and `ok=False` (LLMs treat ok:false tool results as "change strategy", not
     "retry reworded"); the orchestrator-facing `summary` is replaced with
     actionable routing guidance.
  3. a per-agent circuit breaker counts CONSECUTIVE blocked routes and, after
     `block_breaker_limit`, refuses to spawn again — a deterministic ceiling that
     does NOT depend on the LLM following the guidance. It RESETS on any
     non-blocked route so legitimately different sub-questions are never
     conflated with a retry storm.

These tests pin layers 2 + 3 through the REAL OrchestratorRunner / route tool,
using an injected `run_isolated` stub to make each route's outcome deterministic.
"""

from __future__ import annotations

from pathlib import Path

from pyclaw.core.loop import RESPONSE_BLOCKED
from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec
from pyclaw.orchestrator.runner import OrchestratorRunner
from pyclaw.orchestrator.tool import _BLOCKED_GUIDANCE, _result_dict

REPO_ROOT = Path(__file__).resolve().parent.parent


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.add(AgentSpec(name="db-agent", description="HR DB",
                      tool_prefixes=("db_",),
                      home=REPO_ROOT / "agents" / "db-agent"))
    reg.add(AgentSpec(name="pdpa-agent", description="PDPA law",
                      tool_prefixes=("pdpa_",),
                      home=REPO_ROOT / "agents" / "pdpa-agent"))
    return reg


def _runner(scripted, **kw) -> OrchestratorRunner:
    """OrchestratorRunner whose isolated runs return scripted summaries.

    `scripted` is a callable(spec) -> str. Injected as run_isolated so spawn()
    wraps it into a SubagentResult(ok=True, summary=<str>) — exactly the shape a
    real isolated loop returns (including RESPONSE_BLOCKED on a block).
    """
    return OrchestratorRunner(
        registry=_registry(),
        run_isolated=lambda spec, on_tool=None: scripted(spec),
        available_tools=("pdpa_get_section_text", "db_q"),
        **kw,
    )


# =====================================================================
# Layer 2 — a blocked route is a FAILED attempt (ok=False, blocked=True)
# =====================================================================
def test_blocked_route_is_reported_as_failed_not_content() -> None:
    """A route whose isolated loop returns RESPONSE_BLOCKED must surface as
    ok=False + blocked=True (so the LLM changes strategy, not retries)."""
    runner = _runner(lambda spec: RESPONSE_BLOCKED)
    r = runner.route_one("pdpa-agent", "CCTV สอบสวนวินัย อ้างมาตราใด")
    assert r.blocked is True
    assert r.ok is False, "blocked answer must downgrade ok to False"
    # The raw summary is still the sentinel internally...
    assert r.summary == RESPONSE_BLOCKED
    # ...but what the orchestrator LLM sees is actionable guidance, not content.
    d = _result_dict(r)
    assert d["blocked"] is True
    assert d["ok"] is False
    assert d["summary"] == _BLOCKED_GUIDANCE
    assert RESPONSE_BLOCKED not in d["summary"]


def test_non_blocked_route_is_unchanged() -> None:
    """A normal (non-blocked) answer keeps ok=True, blocked=False, real summary."""
    runner = _runner(lambda spec: "ตาม ม.21 และ ม.39 ...")
    r = runner.route_one("pdpa-agent", "อธิบาย ม.21")
    assert r.blocked is False
    assert r.ok is True
    d = _result_dict(r)
    assert d["summary"] == "ตาม ม.21 และ ม.39 ..."
    assert d["blocked"] is False


# =====================================================================
# Layer 3 — circuit breaker: consecutive blocked routes per agent
# =====================================================================
def test_breaker_trips_after_limit_consecutive_blocks() -> None:
    """With the default limit of 2, the 1st and 2nd blocked routes still spawn
    (and block); the 3rd is REFUSED deterministically without spawning."""
    spawns = {"n": 0}

    def scripted(spec):
        spawns["n"] += 1
        return RESPONSE_BLOCKED

    runner = _runner(scripted)  # default block_breaker_limit == 2
    r1 = runner.route_one("pdpa-agent", "q1")
    r2 = runner.route_one("pdpa-agent", "q2 reworded")
    r3 = runner.route_one("pdpa-agent", "q3 reworded again")

    assert spawns["n"] == 2, "breaker must stop the 3rd spawn (only 2 ran)"
    assert r1.blocked and r2.blocked and r3.blocked
    # The refused route carries the deterministic blocked-limit error.
    assert r3.ok is False
    assert r3.error is not None and "[blocked-limit]" in r3.error
    assert r3.summary == ""  # no agent ran -> no summary


def test_breaker_resets_on_a_non_blocked_route() -> None:
    """A non-blocked outcome resets the streak, so a later block starts the
    count fresh — distinct successful work is never penalised."""
    outcomes = iter([
        RESPONSE_BLOCKED,      # block #1
        "ตาม ม.21 ...",        # success -> reset
        RESPONSE_BLOCKED,      # block #1 again (streak restarted)
        RESPONSE_BLOCKED,      # block #2
    ])
    spawns = {"n": 0}

    def scripted(spec):
        spawns["n"] += 1
        return next(outcomes)

    runner = _runner(scripted)
    runner.route_one("pdpa-agent", "q1")   # block
    runner.route_one("pdpa-agent", "q2")   # success (reset)
    runner.route_one("pdpa-agent", "q3")   # block (streak=1)
    r4 = runner.route_one("pdpa-agent", "q4")  # block (streak=2)

    # All four spawned — the breaker never tripped because the success reset it.
    assert spawns["n"] == 4
    assert r4.blocked and not r4.error  # blocked but NOT a blocked-limit refusal


def test_breaker_is_per_agent_not_global() -> None:
    """Blocks against one agent must not trip the breaker for a DIFFERENT agent
    (the count is keyed on the opaque agent name)."""
    def scripted(spec):
        return RESPONSE_BLOCKED

    runner = _runner(scripted)
    runner.route_one("pdpa-agent", "q1")  # pdpa streak=1
    runner.route_one("pdpa-agent", "q2")  # pdpa streak=2
    # db-agent has its own counter -> this still spawns and blocks normally.
    r = runner.route_one("db-agent", "different agent question")
    assert r.blocked is True
    assert r.error is None, "db-agent has its own streak; must not be refused"


def test_breaker_limit_is_configurable() -> None:
    """The ceiling is a plain field on the runner (mechanism, not a magic
    constant) — setting it to 1 refuses the 2nd consecutive block."""
    spawns = {"n": 0}

    def scripted(spec):
        spawns["n"] += 1
        return RESPONSE_BLOCKED

    runner = _runner(scripted, block_breaker_limit=1)
    runner.route_one("pdpa-agent", "q1")       # spawns, blocks (streak=1)
    r2 = runner.route_one("pdpa-agent", "q2")  # refused (streak already 1)
    assert spawns["n"] == 1
    assert "[blocked-limit]" in (r2.error or "")


# =====================================================================
# Sequential routing must not chain a blocked summary forward
# =====================================================================
def test_sequential_does_not_chain_blocked_summary_forward() -> None:
    """route_sequential feeds a prior summary into the next agent's message only
    when the prior route succeeded. A blocked route (ok=False) must NOT leak its
    sentinel into the downstream prompt."""
    seen_messages: list[str] = []

    def scripted(spec):
        seen_messages.append(spec.objective)
        # First (pdpa) blocks; second (db) succeeds.
        return RESPONSE_BLOCKED if "first" in spec.objective else "db ok"

    runner = _runner(scripted)
    results = runner.route_sequential([
        ("pdpa-agent", "first question"),
        ("db-agent", "second question"),
    ])
    assert results[0].blocked and results[0].ok is False
    # The db-agent's message must be the raw second question, NOT augmented with
    # the blocked sentinel from the first route.
    assert RESPONSE_BLOCKED not in seen_messages[1]
    assert "Context from the previous agent" not in seen_messages[1]
