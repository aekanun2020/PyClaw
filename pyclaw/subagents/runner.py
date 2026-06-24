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

import inspect
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

# The generic subagent system prompt. Used as-is when a spec carries no
# per-agent prompt, and exported as the anti-hallucination GUARDRAIL that the
# orchestrator appends beneath an agent's SOUL/TOOLS persona (defence in depth:
# "use real tools, don't invent data" survives even with a custom persona).
GENERIC_SUBAGENT_PROMPT = (
    "You are a PyClaw {kind} subagent. "
    "Work ONLY on the objective using the tools provided; do NOT "
    "invent data. If you cannot verify something with a tool, say so. "
    "When done, reply with a concise summary. "
    "You may not spawn other subagents."
)

# The persona-agnostic guardrail clause appended below SOUL/TOOLS personas.
SUBAGENT_GUARDRAIL = (
    "Work ONLY on the objective using the tools provided; do NOT invent data. "
    "If you cannot verify something with a tool, say so. When done, reply with "
    "a concise summary. You may not spawn other subagents."
)


def _call_runner(runner: Callable, spec: SubagentSpec, on_tool=None):
    """Invoke an isolated-run callable, passing `on_tool` only if it accepts it.

    The real `build_isolated_runner` factory accepts `(spec, on_tool=None)` so
    the parent's live trace observer reaches the subagent's loop. But injected
    test runners are written as `lambda spec: ...` / `def run(spec): ...` and
    take only one argument. We inspect the signature and forward `on_tool` only
    when it is supported — keeping backward-compat while enabling tracing.
    """
    if on_tool is not None and _accepts_on_tool(runner):
        return runner(spec, on_tool=on_tool)
    return runner(spec)


def _accepts_on_tool(runner: Callable) -> bool:
    """True if `runner` declares an `on_tool` parameter (or **kwargs)."""
    try:
        params = inspect.signature(runner).parameters
    except (TypeError, ValueError):
        return False
    if "on_tool" in params:
        return True
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _label_on_tool(on_tool, label: str):
    """Wrap `on_tool` so every call carries a `[sub#N]` label.

    The label is injected into the `info` dict under `_label` (the tracer reads
    it as a line prefix). Returning None when there is no observer keeps the
    no-trace path free of any per-call overhead.
    """
    if on_tool is None:
        return None

    def labeled(phase: str, name: str, info: dict) -> None:
        on_tool(phase, name, {**info, "_label": label})

    return labeled


@dataclass
class SubagentResult:
    spec: SubagentSpec
    summary: str            # the ONLY thing returned to the parent (isolated context)
    ok: bool = True
    error: str | None = None
    # Generic turn-scoped grounded-id set the isolated loop recorded (mechanism,
    # not a domain concept — the runner doesn't know what an id means). Empty
    # when the agent loaded no grounding plugin or grounded nothing. Bubbled so
    # the orchestrator can enforce its COMBINED answer against the union of what
    # every routed agent actually retrieved (the subagent already self-enforces
    # its OWN answer inside its own loop).
    grounded: set[str] = field(default_factory=set)


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

    def spawn(self, spec: SubagentSpec, on_tool=None) -> SubagentResult:
        """Run one subagent to completion in an isolated context.

        Order of checks (all deterministic, principle #1):
          1. refuse a nested spawn (principle #3 — no nesting)
          2. fire PreSubagentSpawn hook; honour BLOCK (Layer 3 policy)
          3. resolve the allowed tools (inherit-then-restrict)
          4. run an isolated agent loop (fresh context) via the factory
          5. return SubagentResult(summary=...) — never leak full history

        `on_tool`, when given, is the parent's live trace observer. It is
        forwarded into the isolated loop so the subagent's tool calls show up
        in `--trace` too. It is a pure observer (never changes control flow).
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
            summary = _call_runner(runner, spec, on_tool)
        except Exception as exc:  # noqa: BLE001 - surface as a structured result
            return SubagentResult(spec=spec, summary="", ok=False, error=str(exc))

        # 5. The summary crosses back to the parent (isolated context). We also
        #    bubble the generic grounded-id set the isolated loop recorded, if
        #    the runner exposes it (the real build_isolated_runner does). Injected
        #    test runners that don't carry one degrade to an empty set.
        grounded = getattr(runner, "last_grounded", set())
        grounded = set(grounded) if isinstance(grounded, (set, frozenset)) else set()
        return SubagentResult(spec=spec, summary=summary, ok=True, grounded=grounded)


@dataclass
class ParallelTeam:
    """A lead coordinates several subagents that run in parallel.

    This is one of PyClaw's "more features" over EliteClaw (sequential only).
    All members are non-nested, so the delegation tree stays exactly one level
    deep (principle #3). Results come back in the SAME ORDER as `specs`, so the
    output is deterministic regardless of completion timing.
    """

    runner: SubagentRunner

    def run(self, specs: list[SubagentSpec], on_tool=None) -> list[SubagentResult]:
        """Run `specs` concurrently and collect results (order-preserving).

        `on_tool`, when given, is the parent's live trace observer. Each
        subagent gets a `[sub#N]`-labelled wrapper (N is 1-based, matching the
        input order) so the interleaved stderr lines from the concurrent
        threads are attributable to a specific subagent — direct evidence of
        real parallelism rather than an inference from timing.
        """
        if not specs:
            return []

        # Each spawn is I/O-bound on the LLM, so a thread pool gives real
        # concurrency. Order is restored by indexing, not completion time.
        workers = max(1, min(self.runner.max_workers, len(specs)))
        results: list[SubagentResult | None] = [None] * len(specs)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self.runner.spawn,
                    spec,
                    _label_on_tool(on_tool, f"[sub#{i + 1}]"),
                ): i
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

    def _run(spec: SubagentSpec, on_tool=None) -> str:
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
        # Per-agent hook engine (mechanism, principle: grounding binds to the
        # agent that needs it, NOT a global engine). If the spec carries a
        # plugins_root we load THAT agent's manifests into a fresh engine, so
        # e.g. the pdpa-agent gets its citation-grounding hooks and self-enforces
        # inside this loop's own PreResponse fire-site. An agent with no plugin
        # dir (db-agent, rag-agent) keeps an empty engine — domains stay isolated
        # by SEPARATE engines, not by pattern-scoping luck. No domain vocabulary
        # appears here; we only pass a Path through to the generic PluginLoader.
        engine = _load_isolated_hooks(spec)
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

        # Per-agent persona (SOUL.md + TOOLS.md) takes precedence when the
        # orchestrator supplied one; otherwise fall back to the generic prompt
        # (current behaviour — backward compatible). Either way the permission
        # allowlist above still gates every tool dispatch (defence in depth).
        loop = AgentLoop(
            llm=llm,
            hooks=engine,                # per-agent hooks (empty unless plugins_root set)
            context=ContextManager(),    # FRESH context — isolation
            audit=AuditLog(),
            hitl=HITLGate(),
            permissions=permissions,
            tools=tools,
            system_prompt=spec.system_prompt or GENERIC_SUBAGENT_PROMPT.format(
                kind=spec.type.value
            ),
        )
        summary = loop.run(spec.objective, user="subagent", on_tool=on_tool)
        # Read back the generic grounded-id set the loop recorded this turn and
        # stash it on the runner callable so SubagentRunner.spawn can bubble it.
        # Mechanism-only: we treat it as an opaque set of strings.
        state = loop.last_turn_state or {}
        grounded = state.get("grounded_sections")
        _run.last_grounded = set(grounded) if isinstance(grounded, (set, frozenset)) else set()
        return summary

    _run.last_grounded = set()
    return _run


def _load_isolated_hooks(spec: SubagentSpec) -> HookEngine:
    """Build the isolated loop's HookEngine for one spec (mechanism only).

    No plugins_root -> an EMPTY engine (current behaviour: subagents inherit no
    parent hooks). With a plugins_root that exists -> load that directory's
    plugin manifests into a fresh engine via the GENERIC PluginLoader, so the
    agent runs with exactly the hooks it declares. This is the per-agent
    grounding wiring (option B): the loader discovers `<root>/*/plugin.yaml`
    and binds each hook's `target:` callable. PyClaw core stays domain-agnostic
    — the only thing that varies per agent is WHICH directory is passed in.
    """
    engine = HookEngine()
    root = getattr(spec, "plugins_root", None)
    if root is None:
        return engine
    from pathlib import Path

    root_path = root if isinstance(root, Path) else Path(str(root))
    if not root_path.is_dir():
        return engine
    from pyclaw.plugins.loader import PluginLoader

    PluginLoader(
        plugins_root=root_path,
        installed_versions={"core": "0.1.0"},
    ).load_all(hooks=engine)
    return engine
