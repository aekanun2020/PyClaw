"""Test-support hooks for Layer 4 (referenced by tests/test_subagents.py).

Kept in-package so the PythonRunner can import them by 'module:func' target.
"""

from __future__ import annotations

from pyclaw.hooks.events import HookAction, HookPayload, HookResult


def deny(payload: HookPayload) -> HookResult:
    """A PreSubagentSpawn hook that always blocks (for tests)."""
    return HookResult(action=HookAction.BLOCK, message="spawn denied by policy", source_hook="deny")


def deny_explore(payload: HookPayload) -> HookResult:
    """PreSubagentSpawn hook: block any EXPLORE subagent (demo policy)."""
    if payload.arguments.get("type") == "explore":
        return HookResult(action=HookAction.BLOCK, message="explore delegation denied")
    return HookResult(action=HookAction.ALLOW)
