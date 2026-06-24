"""Generic citation-grounding hooks (domain-agnostic core).

The grounding *mechanism* is universal: an agent must not cite an identifier it
never actually retrieved this turn. The *vocabulary* of what an identifier looks
like ("ม.39", "§ 230", "ICD-10 J45", a doc id ...) and which tool counts as a
genuine retrieval is DOMAIN-specific and therefore injected, never hard-coded.

So this module exposes two factories — `make_record_grounding` and
`make_enforce_grounding` — that bake in a domain's:

  * `patterns`        : regexes whose group(1) is the raw id token
  * `canon`           : token -> canonical id (e.g. "39" -> "sec_39")
  * `retrieval_tools` : tool names that count as a real retrieval
  * `parse_result`    : tool-result -> (found, canonical_id)   [handles MCP]

PyClaw core ships NO domain pattern. A domain plugin (e.g. pdpa-grounding)
builds its own hook callables from these factories and points its plugin.yaml
`target:` at them. An agent that loads no grounding plugin gets no grounding
behaviour — nothing here fires for it.

Mechanism — two lifecycle events working together:

  1. PostToolUse  -> when a retrieval tool reports found=true, record the
     canonical id as GROUNDED for this turn (state in payload.extra, the
     per-turn dict the loop threads through; module fallback keyed by turn id).

  2. PreResponse  -> extract the ids the draft CITES, diff against GROUNDED.
     If any cited id was never retrieved, BLOCK (engine is fail-closed) with a
     machine-readable list so the loop can retrieve-then-retry.

Invariant:  cited ⊆ grounded   (else BLOCK).  No LLM in the loop (#1: Prompt != Policy).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

from pyclaw.hooks.events import HookAction, HookPayload, HookResult

# Types injected by a domain plugin.
Pattern = re.Pattern[str]
CanonFn = Callable[[str], str]
ResultParser = Callable[[Any], "tuple[bool, str | None]"]

# ---- per-turn grounded-set storage (domain-agnostic) -------------------------

_GROUNDED_KEY = "grounded_sections"
_TURN_KEY = "turn_id"
_fallback_store: dict[str, set[str]] = {}


def _grounded_set(payload: HookPayload) -> set[str]:
    """Return the per-turn grounded set, creating it in place.

    Preferred path: the loop threads a mutable `turn_state` dict as
    payload.extra ([A2] in core/loop.py); we seed our set INTO it so the same
    object is shared by-reference across the PostToolUse and PreResponse
    payloads of one run — that is what makes the invariant turn-scoped.

    Fallback (extra missing/immutable, e.g. a unit test calling the hook outside
    the loop): a module store keyed by turn id. NOT turn-isolated across
    concurrent runs; exists only so the hook degrades instead of crashing.
    """
    extra = payload.extra
    if isinstance(extra, dict):
        existing = extra.get(_GROUNDED_KEY)
        if isinstance(existing, set):
            return existing
        fresh: set[str] = set()
        extra[_GROUNDED_KEY] = fresh
        return fresh
    turn = str((extra or {}).get(_TURN_KEY, "default"))
    return _fallback_store.setdefault(turn, set())


# ---- generic result parsing (MCP-aware) --------------------------------------

def _coerce_payload(obj: Any) -> Any:
    """Best-effort unwrap of an MCP tool result into a plain dict.

    Real MCP results arrive as an envelope, NOT a flat dict:

        {"content": [{"type": "text", "text": "<json string>"}],
         "structuredContent": {"result": "<json string>"}, "isError": false}

    The useful fields (found, section_id, number) live INSIDE that json string,
    so a naive `result.get("found")` always saw None — every retrieval silently
    failed to ground. This unwraps the common shapes:

      * plain dict that already has the fields            -> as-is
      * {"structuredContent": {"result": "<json>"}}       -> parsed inner json
      * {"content": [{"text": "<json>"}, ...]}            -> parsed inner json
      * a bare json string                                -> parsed
    """
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except (ValueError, TypeError):
            return {}
    if not isinstance(obj, dict):
        return {}

    # Already-flat result (e.g. an in-process tool or a test fake).
    if "found" in obj or "section_id" in obj or "number" in obj:
        return obj

    # MCP envelope: structuredContent.result first (most reliable), then content[].text.
    sc = obj.get("structuredContent")
    if isinstance(sc, dict) and "result" in sc:
        inner = _coerce_payload(sc["result"])
        if inner:
            return inner

    content = obj.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                inner = _coerce_payload(part["text"])
                if inner:
                    return inner
    return obj


def default_result_parser(canon: CanonFn) -> ResultParser:
    """A reusable parser: unwrap (MCP-aware), require found=true, read the id.

    A domain may pass its own parser to a factory if its tools differ, but this
    covers the common `{found, section_id|number}` contract used by the PDPA
    tools and most JSON tools behind MCP.
    """
    def parse(result: Any) -> tuple[bool, str | None]:
        data = _coerce_payload(result)
        if not isinstance(data, dict):
            return False, None
        found = bool(data.get("found"))
        sid = data.get("section_id")
        if not sid and data.get("number") is not None:
            sid = canon(str(data["number"]))
        return found, (sid or None)

    return parse


# ---- factories ---------------------------------------------------------------

def make_extract_ids(patterns: Iterable[Pattern], canon: CanonFn) -> Callable[[str], set[str]]:
    """Build a citation extractor for one domain's patterns."""
    pats = tuple(patterns)

    def extract(text: str) -> set[str]:
        found: set[str] = set()
        for pat in pats:
            for m in pat.finditer(text or ""):
                found.add(canon(m.group(1)))
        return found

    return extract


def make_record_grounding(
    *,
    retrieval_tools: Iterable[str],
    canon: CanonFn,
    parse_result: ResultParser | None = None,
    source_hook: str = "record_grounding",
) -> Callable[[HookPayload], HookResult]:
    """Build a PostToolUse hook that grounds an id after a successful retrieval."""
    tools = frozenset(retrieval_tools)
    parser = parse_result or default_result_parser(canon)

    def record_grounding(payload: HookPayload) -> HookResult:
        if (payload.tool or "") not in tools:
            return HookResult(action=HookAction.ALLOW)
        found, section_id = parser(payload.result)
        if not (found and section_id):
            return HookResult(action=HookAction.ALLOW)
        _grounded_set(payload).add(section_id)
        return HookResult(
            action=HookAction.ALLOW,
            message=f"grounded {section_id}",
            source_hook=source_hook,
        )

    return record_grounding


def make_merge_grounding(
    *,
    merge_tools: Iterable[str],
    grounded_key: str = "grounded",
    routes_key: str = "routes",
    source_hook: str = "merge_grounding",
) -> Callable[[HookPayload], HookResult]:
    """Build a PostToolUse hook that UNIONS already-grounded ids from a tool.

    The grounding *mechanism* at the orchestrator level differs from a leaf
    agent: the orchestrator never calls a retrieval tool itself, but a
    delegation tool (e.g. `route_to_agent`) returns the ids the routed agents
    ALREADY grounded. This hook reads those ids straight out of the tool result
    and unions them into the turn's grounded set, so the orchestrator's
    PreResponse `enforce_grounding` can check its COMBINED answer against what
    the agents actually retrieved.

    It is fully generic: WHICH tool to watch (`merge_tools`) and WHERE the ids
    live in the result (`grounded_key`, optionally nested under `routes_key`)
    are injected. No domain pattern, tool name, or vocabulary is hard-coded; the
    ids are treated as opaque strings. A leaf agent never loads this hook (its
    plugin only carries record+enforce), so nothing here fires for it.
    """
    tools = frozenset(merge_tools)

    def _ids_from(obj: Any) -> set[str]:
        """Pull a flat set of id strings out of one result shape."""
        data = _coerce_payload(obj)
        if not isinstance(data, dict):
            return set()
        ids: set[str] = set()
        direct = data.get(grounded_key)
        if isinstance(direct, (list, tuple, set)):
            ids.update(str(x) for x in direct)
        routes = data.get(routes_key)
        if isinstance(routes, (list, tuple)):
            for r in routes:
                if isinstance(r, dict):
                    sub = r.get(grounded_key)
                    if isinstance(sub, (list, tuple, set)):
                        ids.update(str(x) for x in sub)
        return ids

    def merge_grounding(payload: HookPayload) -> HookResult:
        if (payload.tool or "") not in tools:
            return HookResult(action=HookAction.ALLOW)
        ids = _ids_from(payload.result)
        if not ids:
            return HookResult(action=HookAction.ALLOW, source_hook=source_hook)
        _grounded_set(payload).update(ids)
        return HookResult(
            action=HookAction.ALLOW,
            message=f"merged grounded {sorted(ids)}",
            source_hook=source_hook,
        )

    return merge_grounding


def make_enforce_grounding(
    *,
    patterns: Iterable[Pattern],
    canon: CanonFn,
    text_key: str = "text",
    source_hook: str = "enforce_grounding",
) -> Callable[[HookPayload], HookResult]:
    """Build a PreResponse hook that BLOCKs any cited-but-ungrounded id.

    Scoping note: if NOTHING was grounded this turn (the agent never called a
    retrieval tool of this domain) AND the draft cites nothing matching this
    domain's patterns, the hook is a pure no-op ALLOW — so loading the PDPA
    grounding plugin cannot accidentally block an unrelated agent's answer.
    """
    extract = make_extract_ids(patterns, canon)

    def enforce_grounding(payload: HookPayload) -> HookResult:
        draft = (payload.arguments or {}).get(text_key, "")
        cited = extract(draft)
        if not cited:
            # Draft mentions no id in this domain's vocabulary -> not our concern.
            return HookResult(action=HookAction.ALLOW, source_hook=source_hook)
        grounded = _grounded_set(payload)
        missing = sorted(cited - grounded, key=lambda s: int(s.split("_")[1]))
        if missing:
            return HookResult(
                action=HookAction.BLOCK,
                message=(
                    "citation-grounding violation: the answer cites "
                    f"{missing} but the retrieval tool was never called for them. "
                    "Retrieve each missing item, then re-answer. "
                    f"(grounded this turn: {sorted(grounded) or 'none'})"
                ),
                source_hook=source_hook,
            )
        return HookResult(action=HookAction.ALLOW, source_hook=source_hook)

    return enforce_grounding
