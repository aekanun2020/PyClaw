"""Layer 4 — Subagent runner + parallel team.

Spawning always goes through the PreSubagentSpawn hook first (Layer 3), so the
delegation rules are deterministic policy, not a prompt suggestion.

Isolation (principle #2 — context is a resource): every subagent runs in its
OWN AgentLoop with a fresh ContextManager, so the parent's history is never
shared. The ONLY thing that crosses the boundary back to the parent is
`SubagentResult.summary` — never the subagent's full transcript.

No nesting (principle #3 — bounded delegation): a subagent may not spawn
another subagent. `spawn()` refuses any spec already marked `is_nested`, and
every loop it builds is given NO subagent tool, so depth can never exceed 1.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from pyclaw.hooks import HookEngine
from pyclaw.hooks.events import HookAction, HookEvent, HookPayload
from pyclaw.subagents.types import TYPE_TOOL_POLICY, SubagentSpec

# A factory builds an isolated, ready-to-run agent for one subagent spec and
# returns its final text answer. Injected so the runner is testable without a
# live LLM, and so the heavy wiring lives in one place (build_isolated_loop).
LoopFactory = Callable[[SubagentSpec], "object"]


@dataclass
class SubagentResult:
    spec: SubagentSpec
    summary: str            # the ONLY thing returned to the parent (isolated context)
    ok: bool = True
    error: str | None = None


@dataclass
class SubagentRunner:
    parent_tools: tuple[str, ...] = field(default_factory=tuple)
    hooks: HookEngine | None = None
    # Builds + runs an isolated loop for a spec, returning its final text.
    # Defaults to the real wiring in `build_isolated_runner` when None.
    run_isolated: Callable[[SubagentSpec], str] | None = None
    # Given the resolved allowed tool names, return a ToolRegistry holding the
    # REAL tool callables for those names (e.g. the parent's MCP tools). When
    # None, the isolated loop runs with an empty registry (read-only/no tools).
    tool_provider: Callable[[tuple[str, ...]], "object"] | None = None
    max_workers: int = 4

    def resolve_tools(self, spec: SubagentSpec) -> tuple[str, ...]:
        """inherit-then-restrict: parent_tools minus this type's denied tools.

        We also strip any subagent-spawning tool so a child can never delegate
        further (principle #3, defence in depth alongside the is_nested guard).
        """
        denied = set(TYPE_TOOL_POLICY[spec.type].get("deny", ()))
        denied.update({"spawn_subagent", "run_subagent"})
        return tuple(t for t in self.parent_tools if t not in denied)

    def spawn(self, spec: SubagentSpec) -> SubagentResult:
        """Run one subagent to completion in an isolated context.

        Order of checks (all deterministic, principle #1):
          1. refuse a nested spawn (principle #3 — no nesting)
          2. fire PreSubagentSpawn hook; honour BLOCK (Layer 3 policy)
          3. resolve the allowed tools (inherit-then-restrict)
          4. run an isolated agent loop (fresh context) via the factory
          5. return SubagentResult(summary=...) — never leak full history
        """
        # 1. No nesting — a subagent must never have been spawned by a subagent.
        if spec.is_nested:
            return SubagentResult(
                spec=spec, summary="", ok=False,
                error="nested subagent spawning is forbidden (principle #3)",
            )

        # 2. PreSubagentSpawn hook — deterministic delegation policy.
        if self.hooks is not None:
            verdict = self.hooks.fire(
                HookPayload(
                    event=HookEvent.PRE_SUBAGENT_SPAWN,
                    arguments={
                        "type": spec.type.value,
                        "objective": spec.objective,
                        "model_preference": spec.model_preference,
                    },
                )
            )
            if verdict.action is HookAction.BLOCK:
                return SubagentResult(
                    spec=spec, summary="", ok=False,
                    error=f"blocked by PreSubagentSpawn hook: {verdict.message or 'denied'}",
                )

        # 3. Resolve the tool set this subagent is allowed to touch.
        spec.allowed_tools = self.resolve_tools(spec)

        # 4. Run the isolated loop. Any failure is captured, not leaked as a
        #    raw transcript (still fail-*visible* via ok/error, principle #6).
        runner = self.run_isolated or build_isolated_runner(self.tool_provider)
        try:
            summary = runner(spec)
        except Exception as exc:  # noqa: BLE001 - surface as a structured result
            return SubagentResult(spec=spec, summary="", ok=False, error=str(exc))

        # 5. Only the summary crosses back to the parent.
        return SubagentResult(spec=spec, summary=summary, ok=True)


@dataclass
class ParallelTeam:
    """A lead coordinates several subagents that run in parallel.

    This is one of PyClaw's "more features" over EliteClaw (sequential only).
    All members are non-nested, so the delegation tree stays exactly one level
    deep (principle #3). Results come back in the SAME ORDER as `specs`, so the
    output is deterministic regardless of completion timing.
    """

    runner: SubagentRunner

    def run(self, specs: list[SubagentSpec]) -> list[SubagentResult]:
        """Run `specs` concurrently and collect results (order-preserving)."""
        if not specs:
            return []

        # Each spawn is I/O-bound on the LLM, so a thread pool gives real
        # concurrency. Order is restored by indexing, not completion time.
        workers = max(1, min(self.runner.max_workers, len(specs)))
        results: list[SubagentResult | None] = [None] * len(specs)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self.runner.spawn, spec): i
                for i, spec in enumerate(specs)
            }
            for future in futures:
                i = futures[future]
                try:
                    results[i] = future.result()
                except Exception as exc:  # noqa: BLE001 - never lose a slot
                    results[i] = SubagentResult(
                        spec=specs[i], summary="", ok=False, error=str(exc)
                    )
        # All slots are filled by construction.
        return [r for r in results if r is not None]


def build_isolated_runner(
    tool_provider: Callable[[tuple[str, ...]], "object"] | None = None,
) -> Callable[[SubagentSpec], str]:
    """Return a function that builds and runs a REAL isolated AgentLoop.

    Deferred import keeps Layer 4 import-light and avoids a cycle with the core
    loop. The returned loop has:
      - a fresh ContextManager (no parent history — isolation, principle #2)
      - a PermissionPolicy allowlisted to exactly spec.allowed_tools
      - a ToolRegistry holding the REAL callables for those allowed tools, built
        by `tool_provider` (e.g. the parent's MCP tools). Without a provider the
        registry is empty, so the subagent has no tools to call.
      - NO subagent tool (depth stays 1 — principle #3)
    """

    def _run(spec: SubagentSpec) -> str:
        from pyclaw.core.llm import OpenRouterProvider
        from pyclaw.core.loop import AgentLoop
        from pyclaw.core.tools import ToolRegistry
        from pyclaw.plugins.permissions import PermissionPolicy
        from pyclaw.runtime.audit import AuditLog
        from pyclaw.runtime.context import ContextManager
        from pyclaw.runtime.hitl import HITLGate

        llm = OpenRouterProvider(
            model=spec.model_preference or OpenRouterProvider().model
        )
        # Allowlist to the resolved tools (empty -> nothing permitted, which is
        # the safe default for a read-only EXPLORE/PLAN subagent).
        permissions = PermissionPolicy(allowed_tools=frozenset(spec.allowed_tools))

        # Real tools for exactly the allowed names (e.g. parent MCP tools), so
        # the subagent actually executes instead of hallucinating. Belt and
        # braces: the permission allowlist above still gates every dispatch.
        if tool_provider is not None:
            tools = tool_provider(tuple(spec.allowed_tools))
        else:
            tools = ToolRegistry()

        loop = AgentLoop(
            llm=llm,
            hooks=HookEngine(),          # subagents inherit no parent hooks by default
            context=ContextManager(),    # FRESH context — isolation
            audit=AuditLog(),
            hitl=HITLGate(),
            permissions=permissions,
            tools=tools,
            system_prompt=(
                f"You are a PyClaw {spec.type.value} subagent. "
                "Work ONLY on the objective using the tools provided; do NOT "
                "invent data. If you cannot verify something with a tool, say so. "
                "When done, reply with a concise summary. "
                "You may not spawn other subagents."
            ),
        )
        return loop.run(spec.objective, user="subagent")

    return _run
