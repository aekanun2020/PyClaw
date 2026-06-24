"""Test-support hooks for the in-loop block-retry mechanism (Fix C).

Kept in-package so the PythonRunner can import them by 'module:func' target
(it calls the function with the LIVE HookPayload, so `payload.extra` — the
per-run turn_state — is shared by-reference, exactly as the real grounding
hooks rely on).

These are DELIBERATELY domain-free: they prove core/loop.py's feedback-retry
works for ANY enforce-style hook, not just PDPA. The hook BLOCKs a draft that
contains the literal token "BADID" unless a "retrieve" tool reported it found
this turn. The loop treats the block message as an opaque string.
"""

from __future__ import annotations

from pyclaw.hooks.events import HookAction, HookPayload, HookResult

BLOCK_MESSAGE = "draft cites BADID but it was never retrieved; retrieve it or drop it"


def record(payload: HookPayload) -> HookResult:
    """PostToolUse: when `retrieve` reports found, remember the id this turn."""
    if (payload.tool or "") != "retrieve":
        return HookResult(action=HookAction.ALLOW)
    res = payload.result if isinstance(payload.result, dict) else {}
    if res.get("found"):
        extra = payload.extra
        if isinstance(extra, dict):
            bucket = extra.get("retrieved")
            if not isinstance(bucket, set):
                bucket = set()
                extra["retrieved"] = bucket
            bucket.add(res.get("id"))
    return HookResult(action=HookAction.ALLOW, source_hook="record")


def enforce(payload: HookPayload) -> HookResult:
    """PreResponse: BLOCK a draft citing BADID unless it was retrieved this turn."""
    draft = (payload.arguments or {}).get("text", "")
    if "BADID" not in draft:
        return HookResult(action=HookAction.ALLOW, source_hook="enforce")
    retrieved = (payload.extra or {}).get("retrieved", set())
    if "BADID" in retrieved:
        return HookResult(action=HookAction.ALLOW, source_hook="enforce")
    return HookResult(action=HookAction.BLOCK, message=BLOCK_MESSAGE,
                      source_hook="enforce")
