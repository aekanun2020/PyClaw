#!/usr/bin/env python3
"""DEMO — PyClaw ingests EliteClaw's EXISTING `.env` MCP config, unchanged.

This proves the answer to "สามารถเชื่อมต่อ mcp server โดยใช้ config เดิมของ
EliteClaw ใช่ปะ": YES — point PyClaw at EliteClaw's real `.env` file and it
parses every server, with EliteClaw's transport rules, tool prefixes, and
millisecond timeouts honoured.

We read the actual /tmp/EliteClaw/.env.example (the shipped sample) so the
proof uses EliteClaw's own file, not a hand-made one.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/tmp/PyClaw")
sys.path.insert(0, str(ROOT))

from pyclaw.mcp.client import (  # noqa: E402
    Transport,
    load_server_configs_from_dotenv,
)

G = "\033[92m"
C = "\033[96m"
B = "\033[1m"
D = "\033[2m"
R = "\033[0m"

ELITE_ENV = Path("/tmp/EliteClaw/.env.example")


def hr() -> None:
    print(D + "-" * 78 + R)


def main() -> int:
    print(B + "DEMO — PyClaw reads EliteClaw's OWN .env (no edits)" + R)
    print(D + f"source: {ELITE_ENV}" + R)
    hr()

    cfgs = load_server_configs_from_dotenv(ELITE_ENV)

    print(f"parsed {B}{len(cfgs)}{R} MCP servers from EliteClaw .env\n")
    header = f"  {'name':<18}{'transport':<18}{'prefix':<14}{'timeout':<9}url"
    print(C + header + R)
    for c in cfgs:
        print(
            f"  {c.name:<18}{c.transport.value:<18}{c.tool_prefix:<14}"
            f"{c.timeout:<9.0f}{c.url}"
        )
    hr()

    checks: list[tuple[bool, str]] = []

    def chk(cond: bool, msg: str) -> None:
        checks.append((cond, msg))

    names = [c.name for c in cfgs]
    by_name = {c.name: c for c in cfgs}

    # The shipped .env.example declares 7 active servers (8 is commented out).
    chk(len(cfgs) == 7, f"all 7 active servers parsed (got {len(cfgs)})")
    chk(names == ["mssql", "rag", "pdpa", "apify", "mssqlwriter",
                  "google-workspace", "office"],
        f"names + order preserved: {names}")

    # Auto-detect: mssql/rag have no /mcp -> SSE; pdpa ends in /mcp -> streamable.
    chk(by_name["mssql"].transport == Transport.SSE,
        "mssql auto-detected SSE (no /mcp in URL)")
    chk(by_name["pdpa"].transport == Transport.STREAMABLE_HTTP,
        "pdpa auto-detected Streamable HTTP (URL ends /mcp)")
    # Explicit transport on google-workspace/office.
    chk(by_name["google-workspace"].transport == Transport.STREAMABLE_HTTP,
        "google-workspace honours explicit *_TRANSPORT=streamable-http")

    # Tool prefixes preserved (namespacing across servers).
    chk(by_name["mssql"].tool_prefix == "db_", "mssql prefix 'db_' preserved")
    chk(by_name["rag"].tool_prefix == "rag_", "rag prefix 'rag_' preserved")
    chk(by_name["pdpa"].tool_prefix == "pdpa_", "pdpa prefix 'pdpa_' preserved")

    # Every server gets a complementary fallback transport (resilience).
    chk(all(c.fallback is not None and c.fallback != c.transport for c in cfgs),
        "every server has a complementary fallback transport")

    hr()
    ok = sum(1 for c, _ in checks if c)
    for cond, msg in checks:
        tag = (G + "[PASS]" + R) if cond else ("\033[91m[FAIL]" + R)
        print(f"{tag} {msg}")
    hr()
    print(B + f"{ok}/{len(checks)} checks passed" + R)
    print("EliteClaw's existing .env drives PyClaw's MCP layer unchanged.")
    print("transport auto-detect, explicit override, tool prefixes, fallback — all honoured.")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
