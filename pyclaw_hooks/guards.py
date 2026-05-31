"""PreToolUse guardrail: block destructive operations on protected paths.

This is the MVP-checklist hook ("ตั้ง PreToolUse hook เพื่อป้องกัน destructive
operations"). It is deterministic: it fires on every matching tool call and the
LLM cannot talk its way past it (principle #1).

Blocks when EITHER:
  - the tool is an outright destructive tool (delete_file, deploy_to_production,
    modify_secrets), OR
  - the tool touches a protected path (.env, anything under secrets/, .git/,
    or files matching *.pem / id_rsa).

Argument paths are read from common keys (path, file, target, filename) and from
any string value that looks like a path in `arguments`.
"""

from __future__ import annotations

import re
from typing import Any

from pyclaw.hooks.events import HookAction, HookPayload, HookResult

DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {"delete_file", "deploy_to_production", "modify_secrets", "rmtree"}
)

# Protected path patterns (matched against any string argument).
PROTECTED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)\.env(\.|$)"),        # .env, .env.prod, ...
    re.compile(r"(^|/)secrets?/"),          # secrets/ or secret/
    re.compile(r"(^|/)\.git/"),             # .git internals
    re.compile(r"\.pem$"),                  # private keys
    re.compile(r"(^|/)id_rsa$"),            # ssh keys
)

_PATH_KEYS = ("path", "file", "filename", "target", "dest", "destination")


def _candidate_paths(arguments: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in _PATH_KEYS:
        val = arguments.get(key)
        if isinstance(val, str):
            paths.append(val)
    # also scan any other string values that look pathy
    for val in arguments.values():
        if isinstance(val, str) and ("/" in val or val.startswith(".")):
            paths.append(val)
    return paths


def _is_protected(path: str) -> bool:
    return any(p.search(path) for p in PROTECTED_PATTERNS)


def block_destructive(payload: HookPayload) -> HookResult:
    """Return BLOCK for destructive tools / protected paths; ALLOW otherwise."""
    tool = payload.tool or ""
    args = payload.arguments or {}

    if tool in DESTRUCTIVE_TOOLS:
        # Destructive tools are allowed only on non-protected paths, and even
        # then the runtime HITL gate still asks for approval.
        bad = [p for p in _candidate_paths(args) if _is_protected(p)]
        if bad:
            return HookResult(
                action=HookAction.BLOCK,
                message=f"{tool} on protected path(s) {bad} is not allowed",
                source_hook="block_destructive",
            )

    # Any tool (e.g. write_file) touching a protected path is blocked outright.
    bad = [p for p in _candidate_paths(args) if _is_protected(p)]
    if bad:
        return HookResult(
            action=HookAction.BLOCK,
            message=f"protected path(s) {bad} may not be modified by {tool!r}",
            source_hook="block_destructive",
        )

    return HookResult(action=HookAction.ALLOW)
