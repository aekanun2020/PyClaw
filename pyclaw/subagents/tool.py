"""Layer 4 — expose subagent delegation as a single `spawn_subagent` tool.

This is the glue that lets the *parent* AgentLoop delegate work: the LLM calls
`spawn_subagent` like any other tool, so the call flows through the exact same
deterministic chokepoint in `AgentLoop._invoke_tool` (permission -> hook -> HITL
-> dispatch -> audit) and shows up in `--trace`.

What the tool does on dispatch:
  - builds a `SubagentSpec` from the LLM's arguments (type/objective/model)
  - runs it through `SubagentRunner.spawn` (isolated context, no nesting,
    inherit-then-restrict tools, PreSubagentSpawn hook) — or, when given a list
    of objectives, runs them concurrently via `ParallelTeam`
  - returns ONLY the subagent summary(ies) to the parent — never the child's
    full transcript (principle #2, isolation)

It is OFF by default in the CLI (opt-in via `--subagents`): spawning extra
agents multiplies LLM cost, so it must be a deliberate choice.
"""

from __future__ import annotations

from typing import Any

from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.hooks import HookEngine
from pyclaw.subagents.runner import ParallelTeam, SubagentRunner, _label_on_tool
from pyclaw.subagents.trace import get_active_on_tool
from pyclaw.subagents.types import SubagentSpec, SubagentType

# The tool name is also the thing the runner strips from a child's tool set, so
# a subagent can never spawn another subagent (defence in depth, principle #3).
SPAWN_TOOL_NAME = "spawn_subagent"

_SPAWN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [t.value for t in SubagentType],
            "description": (
                "Subagent kind: 'explore' (read-only investigation), "
                "'plan' (planning, no execution), 'review' (critique/verify), "
                "or 'general' (general-purpose)."
            ),
        },
        "objective": {
            "type": "string",
            "description": "A single, self-contained task for ONE subagent.",
        },
        "objectives": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Multiple independent objectives to run as a parallel team. "
                "Provide EITHER 'objective' (one) OR 'objectives' (many)."
            ),
        },
        "model_preference": {
            "type": "string",
            "description": "Optional model override for the subagent(s).",
        },
    },
    "required": ["type"],
}


def _coerce_type(value: Any) -> SubagentType:
    """Map the LLM's string to a SubagentType, defaulting to GENERAL."""
    try:
        return SubagentType(str(value).strip().lower())
    except ValueError:
        return SubagentType.GENERAL


def _make_tool_provider(parent_registry: ToolRegistry | None):
    """Return a provider that builds a child ToolRegistry holding the parent's
    REAL tool callables for exactly the allowed names. This is what lets a
    subagent actually execute parent/MCP tools instead of hallucinating.

    Returns None when there is no parent registry (tests inject their own).
    """
    if parent_registry is None:
        return None

    def provider(allowed_names: tuple[str, ...]) -> ToolRegistry:
        child = ToolRegistry()
        for name in allowed_names:
            tool = parent_registry.get(name)
            if tool is not None:                 # skip unknown / spawn tool
                child.register(tool)
        return child

    return provider


def make_spawn_subagent_tool(
    *,
    parent_tools: tuple[str, ...] = (),
    parent_registry: ToolRegistry | None = None,
    hooks: HookEngine | None = None,
    runner: SubagentRunner | None = None,
) -> Tool:
    """Build the `spawn_subagent` Tool wired to a real (or injected) runner.

    `parent_tools` is the tool set the child may inherit from (then narrowed by
    type). `parent_registry`, when given, lets subagents execute the parent's
    REAL tool callables (e.g. MCP tools) for their allowed names. Pass both
    AFTER MCP is mounted. `runner` is injectable for tests.
    """
    _runner = runner or SubagentRunner(
        parent_tools=tuple(parent_tools),
        hooks=hooks,
        tool_provider=_make_tool_provider(parent_registry),
    )

    def _spawn(arguments: dict[str, Any]) -> Any:
        kind = _coerce_type(arguments.get("type"))
        model = arguments.get("model_preference") or None
        objectives = arguments.get("objectives")
        single = arguments.get("objective")

        # The parent loop publishes its live trace observer around dispatch
        # (see pyclaw.subagents.trace); pick it up so subagent tool calls show
        # in --trace too. None when tracing is off — zero overhead then.
        parent_on_tool = get_active_on_tool()

        # Parallel team when a list of objectives is given. ParallelTeam labels
        # each member [sub#N] so the interleaved stderr lines prove concurrency.
        if objectives:
            specs = [
                SubagentSpec(type=kind, objective=str(o), model_preference=model)
                for o in objectives
            ]
            results = ParallelTeam(runner=_runner).run(specs, on_tool=parent_on_tool)
            return {
                "subagents": [
                    {
                        "type": r.spec.type.value,
                        "objective": r.spec.objective,
                        "ok": r.ok,
                        "summary": r.summary,
                        "error": r.error,
                    }
                    for r in results
                ]
            }

        # Single subagent — still labelled [sub#1] so trace lines are tagged.
        if not single:
            return "[error] provide 'objective' (one) or 'objectives' (many)"
        result = _runner.spawn(
            SubagentSpec(type=kind, objective=str(single), model_preference=model),
            on_tool=_label_on_tool(parent_on_tool, "[sub#1]"),
        )
        return {
            "type": result.spec.type.value,
            "objective": result.spec.objective,
            "ok": result.ok,
            "summary": result.summary,
            "error": result.error,
        }

    return Tool(
        name=SPAWN_TOOL_NAME,
        description=(
            "Delegate a self-contained task to an isolated subagent and get back "
            "only its summary. Use 'explore' for read-only research, 'plan' for "
            "planning, 'review' to verify an artifact, 'general' otherwise. Pass "
            "'objectives' (a list) to run several subagents in parallel. Subagents "
            "cannot spawn further subagents."
        ),
        fn=_spawn,
        parameters=_SPAWN_PARAMETERS,
    )


def register_spawn_subagent_tool(
    registry: ToolRegistry,
    *,
    hooks: HookEngine | None = None,
    runner: SubagentRunner | None = None,
) -> tuple[str, ...]:
    """Register `spawn_subagent` into `registry`, inheriting its current tools.

    Returns the parent tool names the subagents may inherit (for introspection).
    Call this AFTER MCP tools are mounted so children can use them too. The
    spawn tool itself is excluded from what children inherit.
    """
    parent_tools = tuple(n for n in registry.names() if n != SPAWN_TOOL_NAME)
    tool = make_spawn_subagent_tool(
        parent_tools=parent_tools,
        parent_registry=registry,
        hooks=hooks,
        runner=runner,
    )
    registry.register(tool)
    return parent_tools
