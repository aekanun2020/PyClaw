"""Citation-grounding hook: every cited PDPA section must be retrieved first.

This is the Layer-3 enforcement of the grounding invariant that the 5-round
Qwen3.6 benchmark proved a docstring (soft prompt) cannot guarantee:

    GROUND_OK was only 2/5 for identical input/model/tool — the model is
    *capable* (R5, R6 passed) but *inconsistent* (compliance-instability).
    A "must-happen-every-time" rule belongs in a Hook, not a prompt (#1).

Mechanism — two lifecycle events working together:

  1. PostToolUse  -> whenever `get_section_text(section_id=...)` returns
     found=true, record that section id as GROUNDED for this turn.
     (state lives in `payload.extra["grounded_sections"]`, a per-turn set
     the core loop threads through; we fall back to a module set keyed by
     turn id if the loop does not provide one.)

  2. PreResponse  -> parse the section ids the draft answer CITES, diff them
     against the GROUNDED set. If any cited section was never retrieved,
     BLOCK the response (engine is fail-closed) with a machine-readable list
     so the loop can retry: call get_section_text for the missing ids, then
     re-attempt the response.

The invariant:

    every section in cited_sections  ⊆  grounded_sections   (else BLOCK)

Deterministic, regex-based, no LLM in the loop (principle #1: Prompt != Policy).
"""

from __future__ import annotations

import re
from typing import Any

from pyclaw.hooks.events import HookAction, HookPayload, HookResult

# ---- section-id normalisation -------------------------------------------------

# Accept the forms the agent actually emits: "sec_39", "มาตรา 39", "ม.39",
# "section 39", bare "39" — all normalise to the canonical id "sec_39".
_SEC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsec[_\s]?(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\bsection[_\s]?(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"มาตรา\s*(\d{1,3})"),
    re.compile(r"ม\.?\s*(\d{1,3})"),
)


def _canon(num: str) -> str:
    return f"sec_{int(num)}"


def _extract_section_ids(text: str) -> set[str]:
    """All section ids mentioned anywhere in `text`, canonicalised."""
    found: set[str] = set()
    for pat in _SEC_PATTERNS:
        for m in pat.finditer(text or ""):
            found.add(_canon(m.group(1)))
    return found


# ---- per-turn grounded-set storage -------------------------------------------

# Preferred: the core loop hands us a mutable set on payload.extra so state is
# scoped to the turn. If absent, we keep a fallback keyed by turn id in extra.
_GROUNDED_KEY = "grounded_sections"
_TURN_KEY = "turn_id"
_fallback_store: dict[str, set[str]] = {}


def _grounded_set(payload: HookPayload) -> set[str]:
    """Return the per-turn grounded-section set, creating it in place.

    Preferred path: the core loop threads a mutable `turn_state` dict in as
    `payload.extra` ([A2] in core/loop.py). We lazily seed our set INTO that
    dict the first time we see it, so the same set object is shared across the
    PostToolUse and PreResponse payloads of one run (they carry the same dict
    by reference). This is what makes the invariant turn-scoped and correct.

    Fallback (only when extra is missing/immutable, e.g. a hook invoked outside
    the loop in a unit test): a module store keyed by turn id. This path is
    NOT turn-isolated across concurrent runs and must not be relied on in
    production — it exists purely so the hook degrades instead of crashing.
    """
    extra = payload.extra
    if isinstance(extra, dict):
        existing = extra.get(_GROUNDED_KEY)
        if isinstance(existing, set):
            return existing
        fresh: set[str] = set()
        extra[_GROUNDED_KEY] = fresh  # seed into the shared turn_state dict
        return fresh
    turn = str((extra or {}).get(_TURN_KEY, "default"))
    return _fallback_store.setdefault(turn, set())


# ---- the two hooks -----------------------------------------------------------

def record_grounding(payload: HookPayload) -> HookResult:
    """PostToolUse: mark a section GROUNDED after a successful get_section_text.

    Only a genuine retrieval counts: the tool must be get_section_text and its
    result must report found=true. Anything else is a no-op ALLOW.
    """
    if (payload.tool or "") not in {"get_section_text", "pdpa_get_section_text"}:
        return HookResult(action=HookAction.ALLOW)

    result: Any = payload.result
    # The MCP result may arrive as a dict or a JSON string; read defensively.
    found = False
    section_id: str | None = None
    if isinstance(result, dict):
        found = bool(result.get("found"))
        section_id = result.get("section_id") or (
            _canon(str(result["number"])) if result.get("number") else None
        )
    if not (found and section_id):
        # Tool errored / section not found -> nothing is grounded.
        return HookResult(action=HookAction.ALLOW)

    _grounded_set(payload).add(section_id)
    return HookResult(
        action=HookAction.ALLOW,
        message=f"grounded {section_id}",
        source_hook="record_grounding",
    )


def enforce_grounding(payload: HookPayload) -> HookResult:
    """PreResponse: BLOCK if the draft cites any section it never retrieved.

    cited = section ids in the draft answer (payload.arguments["text"]).
    grounded = ids recorded by record_grounding this turn.
    BLOCK when cited - grounded is non-empty (fail-closed, principle #1).
    """
    draft = (payload.arguments or {}).get("text", "")
    cited = _extract_section_ids(draft)
    grounded = _grounded_set(payload)

    missing = sorted(
        cited - grounded,
        key=lambda s: int(s.split("_")[1]),
    )
    if missing:
        return HookResult(
            action=HookAction.BLOCK,
            message=(
                "citation-grounding violation: the answer cites "
                f"{missing} but get_section_text was never called for them. "
                "Retrieve each missing section, then re-answer. "
                f"(grounded this turn: {sorted(grounded) or 'none'})"
            ),
            source_hook="enforce_grounding",
        )
    return HookResult(action=HookAction.ALLOW, source_hook="enforce_grounding")
