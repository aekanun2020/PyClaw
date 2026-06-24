"""Orchestrator runner — turns a routing decision into specialized-agent runs.

A "specialized agent" is just a subagent whose allowed tool group is fixed by
AGENTS.md instead of by SubagentType. So this layer reuses the EXISTING Layer 4
machinery wholesale:

  - `SubagentRunner.spawn` runs one agent in an ISOLATED AgentLoop (fresh
    context, no nesting, PreSubagentSpawn hook). We give the runner a
    `parent_tools` set equal to exactly the agent's resolved tool group, so
    `resolve_tools` (inherit-then-restrict with GENERAL = deny nothing) hands
    the child precisely that group — no db_ tools leak into pdpa-agent and vice
    versa.
  - `tool_provider` (the same closure the spawn tool uses) hands the child the
    parent's REAL tool callables for those names, so the agent executes instead
    of hallucinating.
  - The contextvars trace bridge (`_label_on_tool`) labels each agent's tool
    calls with its NAME (`[db-agent]` / `[pdpa-agent]`) instead of `[sub#N]`.
  - Parallel routes use the same order-preserving threadpool fan-out as
    ParallelTeam; we don't share one ParallelTeam instance because each route
    may target a different agent (different tool group) and thus needs its own
    per-agent runner.

Routing patterns (the orchestrator LLM chooses which by how it calls the tool):
  - Pattern A (parallel): route to several agents in ONE tool call (a list of
    routes) -> run concurrently, results in input order.
  - Pattern B (sequential): the LLM issues routes across SEPARATE turns, reading
    each result before the next. To support an explicit single-call chain we
    also accept an ordered list with `chain=True`, where each agent's message is
    augmented with the prior agent's result.

Depth stays bounded: Orchestrator(0) -> specialized agent(1) -> tool calls(2).
The specialized agents are ordinary non-nested subagents, so they can never
spawn further agents (the runner strips spawn tools + guards is_nested).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pyclaw.core.loop import RESPONSE_BLOCKED
from pyclaw.hooks import HookEngine
from pyclaw.orchestrator.registry import AgentRegistry, AgentSpec
from pyclaw.subagents.runner import (
    SUBAGENT_GUARDRAIL,
    SubagentRunner,
    _label_on_tool,
)
from pyclaw.subagents.types import SubagentSpec, SubagentType


@dataclass
class RouteResult:
    """The outcome of routing one message to one specialized agent."""

    agent: str
    message: str
    summary: str
    ok: bool = True
    error: str | None = None
    # Mechanism-only: True when the routed agent's loop BLOCKed its answer
    # (summary == core RESPONSE_BLOCKED sentinel). A blocked route is NOT
    # partial content to paraphrase around — it is a failed attempt. We surface
    # this so (a) ok is downgraded to False (LLMs treat ok:false tool results as
    # "change strategy", not "retry reworded"), and (b) the circuit breaker can
    # count consecutive blocked routes per agent. Carries no domain meaning.
    blocked: bool = False
    # The generic grounded-id set the routed agent's isolated loop recorded,
    # bubbled up from SubagentResult.grounded. The orchestrator unions these
    # across all routes and enforces its COMBINED final answer against them
    # (closing the orchestrator-level grounding hole). Empty for an agent that
    # loaded no grounding plugin. Mechanism-only: opaque set of strings here.
    grounded: set[str] = field(default_factory=set)
    # Mechanism-only diagnostic: when this route was blocked, the BLOCKing hook's
    # raw message (e.g. "cites [...] but never retrieved") bubbled up from
    # SubagentResult.block_detail, OR the breaker's refusal reason. Surfaced in
    # the tool result so --trace shows WHY a route failed — without leaking it
    # into the orchestrator LLM's actionable summary (which stays the generic
    # "change strategy" guidance). None for a non-blocked route. Opaque string.
    block_detail: str | None = None


def _agent_label(name: str) -> str:
    """Trace label for a routed agent, e.g. `[db-agent]`."""
    return f"[{name}]"


@dataclass
class OrchestratorRunner:
    """Routes messages to specialized agents, reusing the subagent machinery.

    Collaborators are injected for testability. In production `tool_provider`
    is the same closure the spawn tool builds from the live registry, and
    `run_isolated` is left None so `build_isolated_runner` wires a real loop.
    """

    registry: AgentRegistry
    tool_provider: "object" = None      # Callable[[tuple[str,...]], ToolRegistry] | None
    hooks: HookEngine | None = None
    run_isolated: "object" = None       # injected in tests; None -> real loop
    available_tools: tuple[str, ...] = ()
    max_workers: int = 4
    # Circuit breaker (mechanism-only). Counts CONSECUTIVE blocked routes per
    # agent within this runner's lifetime (one runner == one orchestrator
    # user-turn). After `block_breaker_limit` consecutive blocks to the same
    # agent, the next route to that agent is refused deterministically instead
    # of spawning again — this is the hard ceiling that does not depend on the
    # LLM following the blocked-signal instruction. The counter RESETS to zero
    # on any non-blocked route to that agent (so legitimately different
    # sub-questions that happen to fail are not conflated with a retry storm).
    # Keyed on opaque agent name + a boolean flag — no domain vocabulary.
    block_breaker_limit: int = 2
    _block_streak: dict[str, int] = field(default_factory=dict)
    _breaker_lock: object = field(default_factory=threading.Lock)

    def _runner_for(self, agent: AgentSpec) -> SubagentRunner:
        """Build a SubagentRunner restricted to exactly `agent`'s tool group.

        We set `parent_tools` to the agent's resolved tools and use GENERAL
        (deny nothing), so inherit-then-restrict yields precisely that group.
        """
        allowed = agent.resolve_tools(self.available_tools)
        return SubagentRunner(
            parent_tools=allowed,
            hooks=self.hooks,
            run_isolated=self.run_isolated,
            tool_provider=self.tool_provider,
            max_workers=self.max_workers,
        )

    def _spec_for(self, message: str, agent: AgentSpec) -> SubagentSpec:
        # GENERAL so resolve_tools keeps the whole (already-restricted) group.
        # Compose the agent's OWN system prompt from SOUL.md + TOOLS.md, with
        # the anti-hallucination guardrail appended (defence in depth). When the
        # agent has no SOUL/TOOLS the composer returns None, so the spec carries
        # system_prompt=None and the isolated loop uses the generic prompt
        # (backward-compatible fallback — no breakage).
        system_prompt = agent.compose_system_prompt(guardrail=SUBAGENT_GUARDRAIL)
        return SubagentSpec(
            type=SubagentType.GENERAL,
            objective=message,
            system_prompt=system_prompt,
            # Per-agent grounding (option B): point the isolated loop at this
            # agent's own `<home>/plugins` dir so it loads exactly the hook
            # plugins it declares. None for an agent with no plugins dir, so the
            # isolated loop keeps an empty engine (unchanged behaviour).
            plugins_root=agent.plugins_root(),
        )

    def route_one(self, agent_name: str, message: str, on_tool=None) -> RouteResult:
        """Route a single message to a single named agent (isolated run)."""
        agent = self.registry.get(agent_name)
        if agent is None:
            return RouteResult(
                agent=agent_name, message=message, summary="", ok=False,
                error=f"unknown agent {agent_name!r}; known: {self.registry.names()}",
            )

        # Circuit breaker (mechanism): if this agent has already returned
        # `block_breaker_limit` CONSECUTIVE blocked answers, stop spawning. The
        # orchestrator gets a deterministic ok:false error and must change
        # strategy (decompose, route elsewhere, or report inability) instead of
        # burning another full agent run on the same dead end.
        with self._breaker_lock:
            tripped = self._block_streak.get(agent_name, 0) >= self.block_breaker_limit
        if tripped:
            breaker_msg = (
                f"[blocked-limit] {agent_name!r} returned "
                f"{self.block_breaker_limit} consecutive blocked answers; "
                "not retrying. The agent could not ground this request "
                "after retrieval — do NOT re-ask it reworded. Decompose the "
                "question differently, route to another agent, or report "
                "that it cannot be answered from grounded sources."
            )
            return RouteResult(
                agent=agent_name, message=message, summary="", ok=False,
                blocked=True, error=breaker_msg,
                # The breaker refused before spawning, so there's no per-run
                # enforce message; the refusal reason IS the diagnostic detail.
                block_detail=breaker_msg,
            )

        runner = self._runner_for(agent)
        result = runner.spawn(
            self._spec_for(message, agent),
            on_tool=_label_on_tool(on_tool, _agent_label(agent_name)),
        )
        blocked = result.ok and result.summary == RESPONSE_BLOCKED

        # Update the per-agent consecutive-block streak: increment on a blocked
        # answer, reset to zero on any non-blocked outcome (progress).
        with self._breaker_lock:
            if blocked:
                self._block_streak[agent_name] = (
                    self._block_streak.get(agent_name, 0) + 1
                )
            else:
                self._block_streak[agent_name] = 0

        return RouteResult(
            agent=agent_name, message=message,
            summary=result.summary,
            # A blocked answer is a FAILED attempt, not partial content: report
            # ok=False so the orchestrator LLM treats it as "change strategy"
            # rather than "retry reworded". Non-blocked outcomes keep the loop's
            # own ok flag unchanged.
            ok=(result.ok and not blocked),
            error=result.error,
            blocked=blocked,
            grounded=set(result.grounded),
            # Surface the BLOCKing hook's diagnostic detail only when this route
            # was actually blocked; otherwise leave it None (a non-blocked run
            # carries no block reason). Mechanism-only opaque string.
            block_detail=(result.block_detail if blocked else None),
        )

    def route_parallel(
        self, routes: list[tuple[str, str]], on_tool=None
    ) -> list[RouteResult]:
        """Pattern A — run independent (agent, message) routes CONCURRENTLY.

        Each route may target a DIFFERENT agent (different tool group), so a
        single ParallelTeam (which shares one runner) can't serve them. We
        mirror ParallelTeam's order-preserving threadpool fan-out, but dispatch
        through route_one so each route gets its own per-agent runner and its
        own `[agent-name]` trace label. Order matches `routes` regardless of
        completion timing.
        """
        if not routes:
            return []

        workers = max(1, min(self.max_workers, len(routes)))
        results: list[RouteResult | None] = [None] * len(routes)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for i, (agent_name, message) in enumerate(routes):
                futures[pool.submit(self.route_one, agent_name, message, on_tool)] = i
            for future in futures:
                i = futures[future]
                try:
                    results[i] = future.result()
                except Exception as exc:  # noqa: BLE001 - never lose a slot
                    a, m = routes[i]
                    results[i] = RouteResult(
                        agent=a, message=m, summary="", ok=False, error=str(exc)
                    )
        return [r for r in results if r is not None]

    def route_sequential(
        self, routes: list[tuple[str, str]], on_tool=None
    ) -> list[RouteResult]:
        """Pattern B — run routes IN ORDER, feeding each result into the next.

        Agent B's message is augmented with agent A's summary, so a chain like
        "find X in the DB, then check its PDPA obligations" works in a single
        tool call. Runs one at a time (the ordering IS the dependency).
        """
        results: list[RouteResult] = []
        prior: str | None = None
        for agent_name, message in routes:
            msg = message
            if prior:
                msg = (
                    f"{message}\n\n"
                    f"Context from the previous agent's result:\n{prior}"
                )
            result = self.route_one(agent_name, msg, on_tool=on_tool)
            results.append(result)
            # Only a successful summary is worth chaining forward.
            prior = result.summary if result.ok else prior
        return results
