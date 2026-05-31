"""Core — AgentLoop: the place where all 6 layers meet.

Round structure (bounded by SETTINGS.max_tool_rounds, like EliteClaw):

  1. PreSession hook (once)
  2. load memory (L1), build skill catalog (L2), connect MCP (MCP)
  3. for round in range(max_tool_rounds):
       a. ctx.maybe_compact() (L0)  -> PostCompaction hook if compacted
       b. llm.complete(messages, tools)
       c. if no tool calls: PreResponse hook (L3) -> return text
       d. for each tool call:
            - permission check (L5 PermissionPolicy.is_allowed) else block
            - PreToolUse hook (L3): ALLOW / MODIFY args / BLOCK / NOTIFY
            - HITL gate (L0) if tool in require_approval_for
            - execute tool (local / MCP / subagent)
            - PostToolUse hook (L3)
            - audit.record(...) (L0)
            - append result to context (L0)

CRITICAL: a tool is NEVER executed from raw model output — it always passes
through the HookEngine and PermissionPolicy first (principle #1).
"""

from __future__ import annotations

from dataclasses import dataclass

from pyclaw.config import SETTINGS
from pyclaw.core.llm import OpenRouterProvider
from pyclaw.hooks import HookEngine
from pyclaw.hooks.events import HookEvent, HookPayload
from pyclaw.memory import MemoryLoader
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate
from pyclaw.skills.loader import SkillLoader


@dataclass
class AgentLoop:
    """Wires every layer together. All collaborators are injected for testability."""

    llm: OpenRouterProvider
    hooks: HookEngine
    context: ContextManager
    audit: AuditLog
    hitl: HITLGate
    permissions: PermissionPolicy
    memory: MemoryLoader
    skills: SkillLoader
    max_tool_rounds: int = SETTINGS.max_tool_rounds

    def run(self, user_request: str, user: str = "user") -> str:
        """Run the loop to a final text answer.

        TODO: implement the round structure documented in the module docstring.
        Key invariants to preserve:
          - fire PreSession once before the loop
          - every tool call: permissions.is_allowed -> PreToolUse hook ->
            HITL (if required) -> execute -> PostToolUse hook -> audit
          - BLOCK from any hook stops that tool (fail-closed)
          - PreResponse hook before returning the final answer
          - OnError hook on any exception (then re-raise or recover)
        """
        # Reference collaborators so the scaffold's imports are intentional.
        _ = (self.memory, self.skills, self.context, self.permissions, self.hitl, self.audit)
        _ = (HookEvent, HookPayload)
        raise NotImplementedError("AgentLoop.run: round loop with hook-wrapped tools (scaffold)")
