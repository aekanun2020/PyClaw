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

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

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
        )

    def route_one(self, agent_name: str, message: str, on_tool=None) -> RouteResult:
        """Route a single message to a single named agent (isolated run)."""
        agent = self.registry.get(agent_name)
        if agent is None:
            return RouteResult(
                agent=agent_name, message=message, summary="", ok=False,
                error=f"unknown agent {agent_name!r}; known: {self.registry.names()}",
            )
        runner = self._runner_for(agent)
        result = runner.spawn(
            self._spec_for(message, agent),
            on_tool=_label_on_tool(on_tool, _agent_label(agent_name)),
        )
        return RouteResult(
            agent=agent_name, message=message,
            summary=result.summary, ok=result.ok, error=result.error,
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
