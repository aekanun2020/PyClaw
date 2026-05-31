"""The Orchestrator's ONLY tool: `route_to_agent`.

The orchestrator AgentLoop is given a registry that contains JUST this one
meta-tool — no db_/pdpa_ tools. Its LLM analyses intent and calls
`route_to_agent` to dispatch work to the specialized agents declared in
AGENTS.md. The call flows through the same deterministic chokepoint in
`AgentLoop._invoke_tool` (permission -> hook -> HITL -> dispatch -> audit) and
shows up in `--trace`.

How the LLM expresses each routing pattern:
  - Single route   : route_to_agent(agent="db-agent", message="...")
  - Parallel (A)   : route_to_agent(routes=[{agent, message}, ...])         # independent
  - Sequential (B) : route_to_agent(routes=[...], mode="sequential")        # chained / ordered

The tool reads the parent loop's live trace observer from the contextvars
bridge (pyclaw.subagents.trace) and forwards it down so each routed agent's
tool calls appear under its NAME label (`[db-agent]` / `[pdpa-agent]`).
"""

from __future__ import annotations

from typing import Any

from pyclaw.core.tools import Tool
from pyclaw.orchestrator.runner import OrchestratorRunner, RouteResult
from pyclaw.subagents.trace import get_active_on_tool

ROUTE_TOOL_NAME = "route_to_agent"


def _route_params(agent_names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": agent_names,
                "description": "The specialized agent to route a SINGLE message to.",
            },
            "message": {
                "type": "string",
                "description": "The self-contained task/question for the single agent.",
            },
            "routes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "enum": agent_names},
                        "message": {"type": "string"},
                    },
                    "required": ["agent", "message"],
                },
                "description": (
                    "Multiple routes. With mode='parallel' (default) they run "
                    "concurrently and must be INDEPENDENT. With mode='sequential' "
                    "they run in order and each agent receives the previous "
                    "agent's result as context (use when one depends on another)."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["parallel", "sequential"],
                "description": (
                    "How to run `routes`: 'parallel' for independent sub-questions, "
                    "'sequential' when a later agent needs an earlier one's result "
                    "or the user specified an explicit order. Ignored for a single "
                    "`agent`/`message` route."
                ),
            },
        },
        "required": [],
    }


def _result_dict(r: RouteResult) -> dict[str, Any]:
    return {
        "agent": r.agent,
        "message": r.message,
        "ok": r.ok,
        "summary": r.summary,
        "error": r.error,
    }


def _coerce_routes(raw: Any) -> list[tuple[str, str]]:
    """Normalise the LLM's `routes` list into (agent, message) tuples."""
    routes: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                agent = item.get("agent")
                message = item.get("message")
                if agent and message is not None:
                    routes.append((str(agent), str(message)))
    return routes


def make_route_to_agent_tool(runner: OrchestratorRunner) -> Tool:
    """Build the `route_to_agent` Tool wired to an OrchestratorRunner."""

    def _route(arguments: dict[str, Any]) -> Any:
        # Pick up the parent loop's live trace observer (None when trace off).
        parent_on_tool = get_active_on_tool()

        raw_routes = arguments.get("routes")
        mode = str(arguments.get("mode") or "parallel").strip().lower()

        if raw_routes:
            routes = _coerce_routes(raw_routes)
            if not routes:
                return "[error] 'routes' must be a list of {agent, message} objects"
            if mode == "sequential":
                results = runner.route_sequential(routes, on_tool=parent_on_tool)
            else:
                results = runner.route_parallel(routes, on_tool=parent_on_tool)
            return {"mode": mode, "routes": [_result_dict(r) for r in results]}

        # Single route.
        agent = arguments.get("agent")
        message = arguments.get("message")
        if not agent or message is None:
            return (
                "[error] provide either a single {agent, message} or a "
                "'routes' list (with optional mode='sequential')"
            )
        result = runner.route_one(str(agent), str(message), on_tool=parent_on_tool)
        return _result_dict(result)

    return Tool(
        name=ROUTE_TOOL_NAME,
        description=(
            "Route a request to one or more specialized agents and get back only "
            "their summaries. Use a single {agent, message} for one route; use "
            "'routes' (a list) with mode='parallel' for independent sub-questions "
            "run concurrently, or mode='sequential' when one agent needs another's "
            "result (the earlier result is passed as context to the later agent). "
            "You have no domain tools yourself — all real work happens via routing."
        ),
        fn=_route,
        parameters=_route_params(runner.registry.names()),
    )
