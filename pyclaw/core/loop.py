"""Core — AgentLoop: the place where all 6 layers meet.

Round structure (bounded by SETTINGS.max_tool_rounds, like EliteClaw):

  1. PreSession hook (once)
  2. load memory (L1) -> system message; build skill catalog (L2) [optional]
  3. for round in range(max_tool_rounds):
       a. ctx.maybe_compact() (L0)  -> PostCompaction hook if compacted
       b. llm.complete(messages, tools)
       c. if no tool calls: PreResponse hook (L3) -> return (possibly redacted) text
       d. for each tool call -> _invoke_tool():
            - permission check (L5 PermissionPolicy.is_allowed) else block
            - PreToolUse hook (L3): ALLOW / MODIFY args / BLOCK / NOTIFY
            - HITL gate (L0) if tool in require_approval_for
            - execute tool via ToolRegistry
            - PostToolUse hook (L3)
            - audit.record(...) (L0)
            - append result to context (L0)

CRITICAL: a tool is NEVER executed from raw model output — _invoke_tool() always
runs the permission check and the PreToolUse hook first (principle #1). Any
exception fires the OnError hook before propagating.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pyclaw.config import SETTINGS
from pyclaw.core.llm import OpenRouterProvider, ToolCall
from pyclaw.core.tools import ToolRegistry
from pyclaw.hooks import HookEngine
from pyclaw.hooks.events import HookAction, HookEvent, HookPayload
from pyclaw.memory import MemoryLoader
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager, Message, Role
from pyclaw.runtime.hitl import ApprovalDecision, ApprovalRequest, HITLGate
from pyclaw.skills.loader import SkillLoader


class ToolBlocked(Exception):
    """Raised internally when policy/hook/HITL blocks a tool. Carries a reason."""

    def __init__(self, tool: str, reason: str) -> None:
        super().__init__(f"{tool}: {reason}")
        self.tool = tool
        self.reason = reason


@dataclass
class AgentLoop:
    """Wires every layer together. All collaborators are injected for testability."""

    llm: OpenRouterProvider
    hooks: HookEngine
    context: ContextManager
    audit: AuditLog
    hitl: HITLGate
    permissions: PermissionPolicy
    tools: ToolRegistry
    memory: MemoryLoader | None = None
    skills: SkillLoader | None = None
    max_tool_rounds: int = SETTINGS.max_tool_rounds
    system_prompt: str = "You are PyClaw, a deterministic-first agent."

    def run(self, user_request: str, user: str = "user") -> str:
        """Run the loop to a final text answer."""
        try:
            return self._run(user_request, user)
        except Exception as exc:  # OnError hook, then re-raise (fail loudly, #6)
            self.hooks.fire(
                HookPayload(event=HookEvent.ON_ERROR, user=user, extra={"error": str(exc)})
            )
            raise

    # -- internals ------------------------------------------------------------
    def _run(self, user_request: str, user: str) -> str:
        self.hooks.fire(HookPayload(event=HookEvent.PRE_SESSION, user=user))

        self.context.append(Message(role=Role.SYSTEM, content=self._build_system(user)))
        self.context.append(Message(role=Role.USER, content=user_request))

        tool_specs = self.tools.llm_specs()

        for _round in range(self.max_tool_rounds):
            if self.context.maybe_compact():
                self.hooks.fire(HookPayload(event=HookEvent.POST_COMPACTION, user=user))

            response = self.llm.complete(self._as_llm_messages(), tools=tool_specs)

            if not response.tool_calls:
                return self._finalize(response.text, user)

            # Record the assistant's tool-call intent in context. We keep the
            # raw tool_calls in meta so _as_llm_messages can replay them to the
            # provider in OpenAI format (each tool result must reference its id).
            self.context.append(
                Message(
                    role=Role.ASSISTANT,
                    content=response.text,
                    meta={"tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name,
                                      "arguments": json.dumps(c.arguments)}}
                        for c in response.tool_calls
                    ]},
                )
            )
            for call in response.tool_calls:
                output = self._invoke_tool(call, user)
                self.context.append(
                    Message(
                        role=Role.TOOL,
                        content=str(output),
                        meta={"tool": call.name, "tool_call_id": call.id},
                    )
                )

        # Ran out of rounds without a plain-text answer.
        return self._finalize(
            "Reached max_tool_rounds without a final answer.", user
        )

    def _invoke_tool(self, call: ToolCall, user: str) -> object:
        """The single deterministic chokepoint for every tool call."""
        name, args = call.name, dict(call.arguments)

        # L5 — permission policy (fail-closed).
        if not self.permissions.is_allowed(name):
            self.audit.record(
                event="tool_blocked_permission", tool=name,
                input_payload=args, output_payload=None, user=user,
            )
            return f"[blocked] tool {name!r} is not permitted by policy"

        # L3 — PreToolUse hook (may modify args / block / notify).
        pre = self.hooks.fire(
            HookPayload(event=HookEvent.PRE_TOOL_USE, tool=name, arguments=args, user=user)
        )
        if pre.action is HookAction.BLOCK:
            self.audit.record(
                event="tool_blocked_hook", tool=name,
                input_payload=args, output_payload=pre.message, user=user,
            )
            return f"[blocked] {pre.message or 'denied by hook'}"
        if pre.action is HookAction.MODIFY and pre.modified_payload is not None:
            args = dict(pre.modified_payload.arguments)

        # L0 — HITL approval for dangerous tools (fail-closed on deny/timeout).
        if self.hitl.needs_approval(name):
            decision = self.hitl.request_approval(
                ApprovalRequest(tool=name, arguments=args, reason="requires approval")
            )
            self.audit.record(
                event=f"hitl_{decision.value}", tool=name,
                input_payload=args, output_payload=None, user=user,
            )
            if decision is not ApprovalDecision.APPROVED:
                return f"[blocked] approval {decision.value} for {name!r}"

        # Execute the tool.
        result = self.tools.dispatch(name, args)

        # L3 — PostToolUse hook (may modify the result / notify).
        post = self.hooks.fire(
            HookPayload(
                event=HookEvent.POST_TOOL_USE, tool=name, arguments=args,
                result=result, user=user,
            )
        )
        if post.action is HookAction.MODIFY and post.modified_payload is not None:
            result = post.modified_payload.result

        # L0 — audit (always, deterministically).
        self.audit.record(
            event="tool_call", tool=name,
            input_payload=args, output_payload=result, user=user,
        )
        return result

    def _finalize(self, text: str, user: str) -> str:
        """PreResponse hook — last chance to redact/modify before the user sees it."""
        res = self.hooks.fire(
            HookPayload(event=HookEvent.PRE_RESPONSE, arguments={"text": text}, user=user)
        )
        if res.action is HookAction.BLOCK:
            return "[response blocked by policy]"
        if res.action is HookAction.MODIFY and res.modified_payload is not None:
            return str(res.modified_payload.arguments.get("text", text))
        return text

    def _build_system(self, user: str) -> str:
        parts = [self.system_prompt]
        if self.memory is not None:
            bundle = self.memory.load()
            if bundle.text:
                parts.append("# Memory\n" + bundle.text)
        if self.skills is not None:
            catalog = self.skills.registry.build_prompt_catalog()
            if catalog:
                parts.append("# Skills\n" + catalog)
        return "\n\n".join(parts)

    def _as_llm_messages(self) -> list[dict[str, object]]:
        """Render context into OpenAI-compatible chat messages.

        Assistant turns that issued tool calls carry their `tool_calls`; tool
        results carry the matching `tool_call_id` — both required by the API
        when tools are in play (discovered by running the loop for real).
        """
        out: list[dict[str, object]] = []
        for m in self.context.messages:
            msg: dict[str, object] = {"role": m.role.value, "content": m.content}
            if m.role is Role.ASSISTANT and m.meta.get("tool_calls"):
                msg["tool_calls"] = m.meta["tool_calls"]
            if m.role is Role.TOOL and m.meta.get("tool_call_id"):
                msg["tool_call_id"] = m.meta["tool_call_id"]
            out.append(msg)
        return out
