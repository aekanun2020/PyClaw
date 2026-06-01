"""Orchestrator (Feature #2) — auto-routing layer over the subagent machinery.

The Orchestrator owns ONLY the `route_to_agent` meta-tool. It has no domain
tools of its own. Its LLM reads a small registry (from AGENTS.md) and decides
which specialized agent(s) to route a request to, choosing parallel vs
sequential automatically. Each routed "specialized agent" is a subagent whose
allowed tool group is fixed by AGENTS.md — so this layer reuses
`pyclaw/subagents/{runner,types,tool,trace}.py` wholesale.
"""

from pyclaw.orchestrator.registry import (
    AgentRegistry,
    AgentSpec,
    auto_register_unowned,
    load_agents,
)
from pyclaw.orchestrator.runner import OrchestratorRunner
from pyclaw.orchestrator.tool import ROUTE_TOOL_NAME, make_route_to_agent_tool

__all__ = [
    "AgentRegistry",
    "AgentSpec",
    "auto_register_unowned",
    "load_agents",
    "OrchestratorRunner",
    "ROUTE_TOOL_NAME",
    "make_route_to_agent_tool",
]
