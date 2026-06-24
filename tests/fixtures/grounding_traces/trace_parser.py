"""Domain-agnostic parser for `pyclaw chat --orchestrator --trace` transcripts.

MECHANISM LAYER ONLY. This parser extracts the structural skeleton of a trace:
  - which agents were routed to (route_to_agent calls + returns)
  - which tools each agent called, with raw argument strings
  - the ok/blocked flags and final answer text carried on each route return

It contains NO domain vocabulary (no PDPA section regex, no ม./มาตรา, no GDPR terms).
Anything domain-specific (e.g. mapping cited "ม.๒๔" -> "sec_24") lives in the
benchmark test module, not here. This mirrors the PyClaw core rule: mechanism is
domain-agnostic; vocabulary lives in wrappers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# --- structural markers emitted by the trace renderer (mechanism, not vocabulary) ---
_RE_ROUTE_CALL = re.compile(r"→ call\s+route_to_agent\((\{.*)\)\s*$")
_RE_ROUTE_RETURN = re.compile(r"← return route_to_agent\s+\[[^\]]*\]\s+(\{.*)$")
_RE_AGENT_TOOL_CALL = re.compile(r"\[([\w-]+)\]\s+→ call\s+([\w_]+)\((\{.*)\)\s*$")
# ok/blocked appear before the long (often truncated) summary string, so pull them
# out directly when the full object won't json-parse.
_RE_OK = re.compile(r'"ok":\s*(true|false)')
_RE_BLOCKED = re.compile(r'"blocked":\s*(true|false)')


def _loads_prefix(blob: str) -> dict | None:
    """Best-effort parse of a JSON object that may be truncated mid-line.

    The trace renderer truncates long returns (e.g. "...(+7522 chars)"), so a raw
    json.loads will fail. We use raw_decode to read the leading well-formed object.
    """
    blob = blob.strip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(blob)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


@dataclass
class ToolCall:
    agent: str
    tool: str
    raw_args: str


@dataclass
class Route:
    """One route_to_agent invocation and everything that happened inside it."""

    agent: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    ok: bool | None = None
    blocked: bool | None = None
    summary: str = ""  # final answer text from the route return (may be truncated)
    answer_text: str = ""  # full plaintext answer printed after the route return

    def tools_named(self, name: str) -> list[ToolCall]:
        return [t for t in self.tool_calls if t.tool == name]

    @property
    def cite_source(self) -> str:
        """Best available text to cite-check against (full answer preferred)."""
        return self.answer_text or self.summary


@dataclass
class ParsedTrace:
    session: str | None
    user_question: str
    routes: list[Route]
    agents_available: list[str]

    @property
    def agents_routed(self) -> list[str]:
        return [r.agent for r in self.routes if r.agent]


def _attach_final_answer(text: str, routes: list[Route]) -> None:
    """Capture the user-visible plaintext answer block(s).

    Strategy: split the transcript on `you>` turns. Within each turn, the agent's
    plaintext answer is everything after the LAST `← return route_to_agent` line up to
    the end of that turn. We attach it to the last route of that turn.
    """
    if not routes:
        return
    lines = text.splitlines()
    # find indices of all route-return markers
    return_idxs = [
        i for i, ln in enumerate(lines) if _RE_ROUTE_RETURN.search(ln)
    ]
    if not return_idxs:
        return
    last_return = return_idxs[-1]
    # collect plaintext lines after the last return until the next `you>` prompt
    answer: list[str] = []
    for ln in lines[last_return + 1 :]:
        if re.match(r"^you>\s*", ln):
            break
        # skip trace-internal lines (tool calls/returns) if any slipped through
        if "→ call" in ln or "← return" in ln:
            continue
        answer.append(ln)
    routes[-1].answer_text = "\n".join(answer).strip()


def parse_trace(text: str) -> ParsedTrace:
    """Parse a raw --trace transcript into structured routes + tool calls."""
    session = None
    m = re.search(r"\[session\] new (\S+)", text)
    if m:
        session = m.group(1)

    agents_available: list[str] = []
    m = re.search(r"\[orchestrator\]\s+\d+ agent\(s\):\s+(.+)", text)
    if m:
        agents_available = [a.strip() for a in m.group(1).split(",")]

    user_question = ""
    m = re.search(r"^you>\s+(.*)$", text, re.MULTILINE)
    if m:
        user_question = m.group(1).strip()

    routes: list[Route] = []
    current: Route | None = None

    for line in text.splitlines():
        m = _RE_ROUTE_CALL.search(line)
        if m:
            current = Route()
            args = _loads_prefix(m.group(1))
            if args:
                current.agent = args.get("agent")
            routes.append(current)
            continue

        m = _RE_AGENT_TOOL_CALL.search(line)
        if m and current is not None:
            current.tool_calls.append(
                ToolCall(agent=m.group(1), tool=m.group(2), raw_args=m.group(3))
            )
            continue

        m = _RE_ROUTE_RETURN.search(line)
        if m and current is not None:
            blob = m.group(1)
            ret = _loads_prefix(blob)
            if ret:
                current.ok = ret.get("ok")
                current.blocked = ret.get("blocked")
                current.summary = ret.get("summary", "") or ""
            else:
                # full object truncated mid-summary: recover the scalar flags only
                mo = _RE_OK.search(blob)
                mb = _RE_BLOCKED.search(blob)
                if mo:
                    current.ok = mo.group(1) == "true"
                if mb:
                    current.blocked = mb.group(1) == "true"
            # the full final answer is also printed in plaintext after this marker;
            # _attach_final_answer (below) captures it for cite-checking.
            current = None
            continue

    # Attach the plaintext answer that follows the LAST route return (the user-visible
    # final answer). The renderer prints it verbatim between the route return and the
    # next `you>` prompt.
    _attach_final_answer(text, routes)

    return ParsedTrace(
        session=session,
        user_question=user_question,
        routes=routes,
        agents_available=agents_available,
    )
