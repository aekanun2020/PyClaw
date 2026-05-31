"""Layer 0 — Human-In-The-Loop (HITL) approval gate.

Some actions are too dangerous to let the LLM perform unsupervised. The
runtime requires explicit human approval for any tool in `require_approval_for`
(default: delete_file, deploy_to_production, modify_secrets), with a timeout
(default 60s) after which the request is auto-denied.

This is enforced in code from the core loop — NOT a prompt instruction
(principle #1). It pairs naturally with the Hook engine: a PreToolUse hook
can route to this gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from pyclaw.config import SETTINGS

DEFAULT_REQUIRE_APPROVAL_FOR: tuple[str, ...] = (
    "delete_file",
    "deploy_to_production",
    "modify_secrets",
)


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


@dataclass
class ApprovalRequest:
    tool: str
    arguments: dict[str, Any]
    reason: str = ""


@dataclass
class HITLGate:
    """Approval gate.

    `prompt_fn` is injected so the gate is testable and transport-agnostic
    (CLI prompt, web UI, Slack, etc.). It receives an ApprovalRequest and must
    return True (approve) / False (deny) within the timeout.
    """

    require_approval_for: tuple[str, ...] = DEFAULT_REQUIRE_APPROVAL_FOR
    timeout_seconds: int = field(default_factory=lambda: SETTINGS.hitl_timeout_seconds)
    prompt_fn: Callable[[ApprovalRequest], bool] | None = None

    def needs_approval(self, tool: str) -> bool:
        return tool in self.require_approval_for

    def request_approval(self, req: ApprovalRequest) -> ApprovalDecision:
        """Block until the human responds or the timeout elapses.

        TODO:
          - run `prompt_fn` under a timeout (e.g. concurrent.futures / asyncio)
          - on timeout -> ApprovalDecision.TIMED_OUT (fail-closed = deny)
          - emit an AuditLog record for the decision
        """
        if self.prompt_fn is None:
            raise NotImplementedError("HITLGate.prompt_fn not configured (scaffold)")
        raise NotImplementedError("HITLGate.request_approval: enforce timeout (scaffold)")
