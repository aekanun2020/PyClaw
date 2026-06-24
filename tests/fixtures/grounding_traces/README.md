# Grounding regression trace fixtures

Raw `--trace` transcripts captured 2026-06-24 from live `pyclaw chat --orchestrator --trace`
runs against the PDPA MCP server. Each fixture is a verified-passing run that proves the
3-layer grounding fix shipped on 2026-06-24 works end-to-end in production:

| commit | layer | what it does |
|--------|-------|--------------|
| `45dc09b` | observability | bubbles BLOCK reason to `--trace` via `block_detail` |
| `e961d09` | vocabulary (SKILL.md) | "grounded" = ONLY `pdpa_get_section_text` of that section this turn |
| `0974608` | mechanism (loop.py) | in-run feedback-retry on PreResponse BLOCK (Fix C) |

## Why these are fixtures, not live replays

These runs hit the MCP server on the user's Mac (`pdpa_get_section_text`, etc.).
CI/sandbox cannot reach that server, so the runs are **not reproducible** here.
Instead, `tests/test_grounding_trace_benchmark.py` parses the stored transcript text and
asserts the grounding **invariants** that prove the fix — no MCP dependency.

## Invariants asserted (per trace)

1. **cited ⊆ grounded** — every PDPA section number cited in the final answer has a
   matching `pdpa_get_section_text(sec_NN)` call in the same route. This is the core
   enforce-grounding invariant (`enforce_grounding` PreResponse hook).
2. **ok / not blocked** — the `route_to_agent` return carries `"ok": true, "blocked": false`.
3. **no breaker fallback** — pdpa-agent answered; no collapse / fallback to `rag-agent`.
4. **no GDPR-vocabulary leak** — the answer does not use GDPR-specific English terms
   (the SKILL.md no-GDPR rule).

## Fixtures

| file | session | scenario | proves |
|------|---------|----------|--------|
| `trace_135819_cctv_new_purpose.txt` | 20260624-135819 | CCTV→marketing new-purpose | per-case reasoning; collect vs use/disclose split |
| `trace_141452_biometric_m26.txt` | 20260624-141452 | biometric ม.26 sensitive | **first prod sighting of Fix C self-correct** |
| `trace_142224_cctv_rerun.txt` | 20260624-142224 | CCTV→marketing re-run | reproducible; ม.84 ungrounded gap closed |
| `trace_142459_biometric_rerun.txt` | 20260624-142459 | biometric re-run | deepest yet, still fully grounded |
| `trace_142929_cctv_lifecycle.txt` | 20260624-142929 | full CCTV lifecycle sweep | 18 sections, all cited⊆grounded |

## Architecture note

The transcript parser (`trace_parser.py`) is **mechanism**: it extracts agent routes,
tool calls, and flags with no domain knowledge. The PDPA section-id vocabulary
(`ม./มาตรา` patterns, Thai numerals, `sec_NN` canonical form) lives in the benchmark
test module's `pdpa_vocab` block, clearly fenced off — never in PyClaw core.
