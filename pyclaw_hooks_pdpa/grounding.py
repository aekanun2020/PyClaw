"""PDPA-specific citation grounding — a thin wrapper over the generic core.

All grounding *mechanism* lives in `pyclaw_hooks.grounding` (domain-agnostic).
This module only supplies the PDPA *vocabulary* and wires it into ready-to-use
hook callables that `.agent/plugins/pdpa-grounding/plugin.yaml` targets:

    target: pyclaw_hooks_pdpa.grounding:record_grounding
    target: pyclaw_hooks_pdpa.grounding:enforce_grounding

Nothing about Thai law leaks back into PyClaw core, so the same framework can
host a US-law / medical / finance grounding plugin side by side, each with its
own patterns and retrieval tools.

Scoping decision (ก): PDPA matches ONLY the Thai legal keywords "ม." / "มาตรา".
The earlier English forms ("sec_N", "section N") were deliberately dropped —
they collide with ordinary English prose ("section 3 of the config", a "sec_12"
variable) and would let this plugin block an unrelated agent's answer. Thai
keywords are specific enough that loading this plugin cannot over-reach.

PDPA citation forms the agent actually emits, including the two that the first
single-number regex missed:

  * single        : "มาตรา ๓๙", "ม.39"
  * chained slash : "ม.83/84"            -> sec_83 AND sec_84
  * chained list  : "มาตรา 30, 31, 32"   -> sec_30, sec_31, sec_32
  * Thai numerals : "มาตรา ๒๗"           -> sec_27

All canonicalise to "sec_<arabic-int>".
"""

from __future__ import annotations

import re

from pyclaw_hooks.grounding import (
    default_result_parser,
    make_enforce_grounding,
    make_merge_grounding,
    make_record_grounding,
)

# ---- Thai-numeral handling ----------------------------------------------------

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _to_arabic(token: str) -> str:
    return token.translate(_THAI_DIGITS)


def canon(num: str) -> str:
    """Token (Arabic or Thai numerals) -> canonical 'sec_<int>'."""
    return f"sec_{int(_to_arabic(num))}"


# ---- citation patterns --------------------------------------------------------
# A "section number" is 1-3 digits in either Arabic or Thai numerals.
_NUM = r"[0-9๐-๙]{1,3}"

# Thai keyword forms. To catch CHAINED citations ("ม.83/84", "มาตรา 30, 31, 32")
# we match the keyword once, then capture the whole run of numbers + separators
# that follows, and pull every number out of that run. Separators allowed
# between chained numbers: spaces, ".", ",", "/", "และ" (Thai "and"), "วรรค...".
_CHAIN_TAIL = rf"{_NUM}(?:\s*(?:[,/]|และ)\s*{_NUM})*"
_PAT_MATRA = re.compile(rf"มาตรา\s*({_CHAIN_TAIL})")
_PAT_MO = re.compile(rf"ม\.?\s*({_CHAIN_TAIL})")

_NUM_RE = re.compile(_NUM)


def _expand_chain(canon_fn, m: "re.Match[str]") -> set[str]:
    """Pull every number out of a matched chain run -> set of canonical ids."""
    return {canon_fn(tok) for tok in _NUM_RE.findall(m.group(1))}


class _ChainPattern:
    """Adapter so a keyword+chain regex behaves like the simple patterns the
    generic extractor expects (group(1) -> one token). It instead yields every
    number in the chain. We expose `.finditer` returning objects whose
    `.group(1)` is each individual number token."""

    def __init__(self, regex: "re.Pattern[str]") -> None:
        self._re = regex

    def finditer(self, text: str):
        for m in self._re.finditer(text):
            for tok in _NUM_RE.findall(m.group(1)):
                yield _Tok(tok)


class _Tok:
    __slots__ = ("_t",)

    def __init__(self, t: str) -> None:
        self._t = t

    def group(self, _n: int) -> str:
        return self._t


# (ก) Thai keywords only — no bare English "sec_"/"section" patterns, so this
# plugin never matches ordinary English prose in an unrelated agent's answer.
PATTERNS = (
    _ChainPattern(_PAT_MATRA),
    _ChainPattern(_PAT_MO),
)

# Tool names that count as a genuine PDPA section retrieval.
RETRIEVAL_TOOLS = ("get_section_text", "pdpa_get_section_text")

# The orchestrator's delegation tool. Its result carries the section ids the
# routed agents already grounded; the merge hook unions them into the turn so
# the orchestrator's enforce hook can check the COMBINED answer.
MERGE_TOOLS = ("route_to_agent",)

# ---- ready-to-use hook callables (what plugin.yaml targets) ------------------

record_grounding = make_record_grounding(
    retrieval_tools=RETRIEVAL_TOOLS,
    canon=canon,
    parse_result=default_result_parser(canon),
    source_hook="pdpa.record_grounding",
)

merge_grounding = make_merge_grounding(
    merge_tools=MERGE_TOOLS,
    grounded_key="grounded",
    routes_key="routes",
    source_hook="pdpa.merge_grounding",
)

enforce_grounding = make_enforce_grounding(
    patterns=PATTERNS,
    canon=canon,
    text_key="text",
    source_hook="pdpa.enforce_grounding",
)
