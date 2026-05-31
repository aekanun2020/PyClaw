"""Global configuration & path discovery for PyClaw.

Discovers the `.agent/` directory (runtime state) and loads `.env`.
Mirrors EliteClaw's dotenv usage, but centralised so every layer reads the
same paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Honour an explicit EliteClaw/OpenClaw .env via PYCLAW_DOTENV first (so
    # pointing PyClaw at an existing deployment's .env brings over not just MCP
    # servers but also OPENROUTER_* settings), then fall back to ./.env.
    _dotenv_override = os.getenv("PYCLAW_DOTENV")
    if _dotenv_override:
        load_dotenv(_dotenv_override)
    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional at import time
    pass


def _find_agent_dir(start: Path | None = None) -> Path:
    """Walk upward from `start` to find the nearest `.agent/` directory.

    Falls back to `<cwd>/.agent`. This walking behaviour is shared with the
    Layer 1 memory loader (directory walking to root).
    """
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        candidate = parent / ".agent"
        if candidate.is_dir():
            return candidate
    return cur / ".agent"


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings. Read once at startup."""

    # LLM (OpenRouter — same backend as EliteClaw)
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    )
    # Model: PyClaw's own override wins, then EliteClaw/OpenClaw's OPENROUTER_MODEL
    # (used verbatim in their .env files), then a hosted default.
    default_model: str = field(
        default_factory=lambda: (
            os.getenv("PYCLAW_DEFAULT_MODEL")
            or os.getenv("OPENROUTER_MODEL")
            or "anthropic/claude-3.7-sonnet"
        )
    )

    # Runtime (Layer 0)
    max_tool_rounds: int = field(
        default_factory=lambda: int(os.getenv("PYCLAW_MAX_TOOL_ROUNDS", "20"))
    )
    hitl_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("PYCLAW_HITL_TIMEOUT_SECONDS", "60"))
    )

    # Paths
    agent_dir: Path = field(default_factory=_find_agent_dir)

    @property
    def audit_log_path(self) -> Path:
        return self.agent_dir / "logs" / "audit.jsonl"

    @property
    def hooks_dir(self) -> Path:
        return self.agent_dir / "hooks"

    @property
    def mcp_servers_path(self) -> Path:
        return self.agent_dir / "mcp-servers.yaml"


SETTINGS = Settings()
