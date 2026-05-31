"""Layer 3 — Hook events, payloads, and result actions.

The 8 lifecycle events from the ADK Spec, and the 4 actions a hook may return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookEvent(str, Enum):
    """The 8 hook events. The core loop fires the matching event at each point."""

    PRE_TOOL_USE = "PreToolUse"            # before a tool runs — can block/modify args
    POST_TOOL_USE = "PostToolUse"          # after a tool returns — can modify result/notify
    POST_EDIT = "PostEdit"                 # after a file edit — e.g. run formatter/linter
    PRE_SESSION = "PreSession"             # session start — load policy, warm caches
    POST_COMPACTION = "PostCompaction"     # after context compaction — re-inject pinned data
    ON_ERROR = "OnError"                   # any tool/loop error — alert, recover, escalate
    PRE_SUBAGENT_SPAWN = "PreSubagentSpawn"  # before spawning a subagent — enforce delegation rules
    PRE_RESPONSE = "PreResponse"           # before final reply to user — redact, watermark


class HookAction(str, Enum):
    """What a hook tells the engine to do with the payload."""

    ALLOW = "allow"      # proceed unchanged
    MODIFY = "modify"    # proceed, but use HookResult.modified_payload
    BLOCK = "block"      # stop; do not run the tool / send the response
    NOTIFY = "notify"    # proceed, but surface HookResult.message to the user/log


@dataclass
class HookPayload:
    """Context handed to a hook. Shape varies a little per event.

    For PRE_TOOL_USE / POST_TOOL_USE: `tool` + `arguments` (+ `result` on POST).
    For PRE_SUBAGENT_SPAWN: `arguments` carries {type, objective, ...}.
    For PRE_RESPONSE: `arguments` carries {text}.
    """

    event: HookEvent
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any | None = None
    user: str = "system"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """A hook's verdict. `modified_payload` is required when action == MODIFY."""

    action: HookAction = HookAction.ALLOW
    modified_payload: HookPayload | None = None
    message: str | None = None       # used by NOTIFY / BLOCK (reason)
    source_hook: str | None = None   # which hook produced this (for audit)
