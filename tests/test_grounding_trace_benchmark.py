"""Grounding regression benchmark over captured --trace transcripts.

These fixtures are verified-passing production runs (2026-06-24) that prove the
3-layer grounding fix works end-to-end:
  - 45dc09b  observability (block_detail)
  - e961d09  SKILL.md grounding discipline (vocabulary layer)
  - 0974608  Fix C in-run feedback-retry (mechanism layer)

The runs hit the live PDPA MCP server on the user's Mac and CANNOT be replayed in CI.
Instead we parse the stored transcript text and assert the grounding INVARIANTS that
prove the fix. No MCP dependency, no network — pure text analysis.

ARCHITECTURE NOTE (per PyClaw layering rule):
  - trace_parser.py = MECHANISM: extracts routes/tools/flags, zero domain knowledge.
  - The PDPA vocabulary needed to cite-check (ม./มาตรา patterns, Thai numerals,
    sec_NN canonical ids, GDPR stop-words) is fenced into the `pdpa_vocab` section
    BELOW. It lives in this test asset, never in PyClaw core.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import sys

_FIXTURES = Path(__file__).parent / "fixtures" / "grounding_traces"
sys.path.insert(0, str(_FIXTURES))

from trace_parser import ParsedTrace, parse_trace  # noqa: E402


# ======================================================================================
# pdpa_vocab — DOMAIN VOCABULARY (PDPA-specific). Test-asset only; never in core.
# ======================================================================================

# Thai digit -> arabic, for canonicalising "ม.๒๔" -> 24.
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# A cited section in the answer prose: "ม.๒๔", "ม.24", "มาตรา ๒๗", "มาตรา 39".
# We capture the leading section number only (sub-paragraphs like (๕)/วรรค are ignored
# for grounding — enforce keys on the section number).
_RE_CITED_SECTION = re.compile(r"(?:ม\.|มาตรา\s*)([0-9๐-๙]{1,3})")

# A grounded section: pdpa_get_section_text({"section_id": "sec_24"}) raw args.
_RE_GROUNDED_SECTION = re.compile(r'"section_id":\s*"sec_(\d{1,3})"')

# GDPR-vocabulary stop-words the PDPA agent must NOT leak into a Thai-law answer.
# (The SKILL.md no-GDPR rule.) Word-boundary matched, case-insensitive.
#
# NOTE: several English words are deliberately NOT here because they appear
# routinely in the verified-passing answers as accepted Thai-PDPA bilingual usage,
# NOT as GDPR-framework leakage:
#   - "consent"  -> gloss of "ความยินยอม"
#   - "legitimate interest" -> gloss of "ประโยชน์โดยชอบด้วยกฎหมาย"
#   - "controller" / "processor" -> glosses of "ผู้ควบคุมข้อมูล" / "ผู้ประมวลผลข้อมูล"
#     (e.g. trace 142459 writes "ผู้ให้บริการระบบลงเวลา (processor)" tied to ม.40).
# Only GDPR-FRAMEWORK-specific markers signal a true leak: the GDPR name itself,
# GDPR-style "Article N" citations (PDPA cites "มาตรา"), and the GDPR-only term
# "data subject" (PDPA Thai answers say "เจ้าของข้อมูล").
_GDPR_STOPWORDS = [
    r"\bGDPR\b",
    r"\bArticle\s+\d+",     # GDPR cites "Article 6"; PDPA cites "มาตรา"
    r"\bdata subject\b",
]

# Sections that are principles / penalty-index sections, NOT lawful bases. If the answer
# frames these as a "ฐานกฎหมาย / ฐานในการเก็บ" it's the GATE ม.21 class of error.
# We only assert the specific ม.21 GATE: ม.21 must not be called a lawful basis.
_RE_M21_AS_BASIS = re.compile(r"ม\.๒๑[^\n|]{0,40}(?:ฐานกฎหมาย|ฐานในการเก็บ|ฐานการเก็บ)")


def _canon_section_numbers(text: str, pattern: re.Pattern) -> set[int]:
    out: set[int] = set()
    for m in pattern.finditer(text):
        raw = m.group(1).translate(_THAI_DIGITS)
        if raw.isdigit():
            out.add(int(raw))
    return out


# ======================================================================================
# benchmark fixtures
# ======================================================================================

FIXTURE_FILES = sorted(_FIXTURES.glob("trace_*.txt"))


def _load(path: Path) -> ParsedTrace:
    return parse_trace(path.read_text(encoding="utf-8"))


def _all_grounded(trace: ParsedTrace) -> set[int]:
    """Sections grounded anywhere in the turn (aggregated across all routes).

    By the time the final answer is written, every section retrieved via
    get_section_text in that turn is grounded context — including across the
    self-correct second route (Fix C). So grounding aggregates over all routes.
    """
    grounded: set[int] = set()
    for r in trace.routes:
        for c in r.tools_named("pdpa_get_section_text"):
            grounded |= _canon_section_numbers(c.raw_args, _RE_GROUNDED_SECTION)
    return grounded


def _answer_text(trace: ParsedTrace) -> str:
    return "\n".join(r.cite_source for r in trace.routes)


def _idify(path: Path) -> str:
    return path.stem


# ======================================================================================
# tests
# ======================================================================================


def test_fixtures_present():
    """All 5 verified traces must be checked in."""
    assert len(FIXTURE_FILES) == 5, (
        f"expected 5 grounding trace fixtures, found {len(FIXTURE_FILES)}: "
        f"{[p.name for p in FIXTURE_FILES]}"
    )


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=_idify)
def test_cited_subset_of_grounded(path: Path):
    """CORE INVARIANT: every PDPA section cited in the answer was retrieved via
    pdpa_get_section_text in the same turn (cited ⊆ grounded)."""
    trace = _load(path)
    grounded = _all_grounded(trace)
    cited = _canon_section_numbers(_answer_text(trace), _RE_CITED_SECTION)
    ungrounded = cited - grounded
    assert not ungrounded, (
        f"{path.name}: answer cites sections with no get_section_text grounding: "
        f"{sorted(ungrounded)} (grounded={sorted(grounded)})"
    )


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=_idify)
def test_route_ok_and_not_blocked(path: Path):
    """Every route returned ok:true / blocked:false — no PreResponse block survived
    to the user, and no breaker-collapsed empty answer."""
    trace = _load(path)
    assert trace.routes, f"{path.name}: no route_to_agent found"
    for i, r in enumerate(trace.routes):
        assert r.ok is True, f"{path.name} route{i}: ok={r.ok!r} (expected True)"
        assert r.blocked is False, f"{path.name} route{i}: blocked={r.blocked!r}"


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=_idify)
def test_no_rag_agent_fallback(path: Path):
    """The PDPA question was answered by pdpa-agent — no breaker fallback to rag-agent."""
    trace = _load(path)
    assert "pdpa-agent" in trace.agents_routed, (
        f"{path.name}: pdpa-agent never handled the turn (routed={trace.agents_routed})"
    )
    assert "rag-agent" not in trace.agents_routed, (
        f"{path.name}: fell back to rag-agent (routed={trace.agents_routed})"
    )


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=_idify)
def test_no_gdpr_vocabulary_leak(path: Path):
    """The Thai-law answer must not leak GDPR-specific English vocabulary."""
    trace = _load(path)
    answer = _answer_text(trace)
    leaks = [w for w in _GDPR_STOPWORDS if re.search(w, answer, re.IGNORECASE)]
    assert not leaks, f"{path.name}: GDPR vocabulary leaked into answer: {leaks}"


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=_idify)
def test_gate_m21_not_a_lawful_basis(path: Path):
    """GATE ม.21: section 21 is a purpose-limitation PRINCIPLE, never a lawful basis."""
    trace = _load(path)
    answer = _answer_text(trace)
    m = _RE_M21_AS_BASIS.search(answer)
    assert m is None, (
        f"{path.name}: ม.๒๑ framed as a lawful basis: ...{m.group(0)!r}..."
    )
