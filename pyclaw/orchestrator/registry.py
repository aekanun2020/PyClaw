"""Orchestrator agent registry — parses AGENTS.md (the #5 config file).

AGENTS.md is the SOURCE OF TRUTH for the specialized agents. We parse it with
the same `---` frontmatter convention used by SKILL.md (`parse_frontmatter` in
pyclaw/skills/registry.py), extended to read MANY blocks from one file: each
`---`...`---` block is one agent. This keeps the loader consistent with the
existing skills/memory loaders rather than introducing a new config format.

Each agent declares:
  - name        : id used in route_to_agent(agent=...)
  - description : when-to-use text, copied verbatim into the routing prompt
  - tools       : comma-separated tool-name PREFIXES the agent may use

The registry resolves those prefixes against the live tool registry at routing
time, so a specialized agent only ever receives the REAL tool callables whose
names start with one of its prefixes (e.g. `db_` -> db_execute_query_tool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pyclaw.skills.registry import parse_frontmatter

# Canonical filename + the directory-walk root behaviour mirrors the memory
# loader: AGENTS.md is looked up from the working dir upward so a repo-root file
# is found regardless of the process cwd within the tree.
AGENTS_FILENAME = "AGENTS.md"

# Per-agent persona files (ported 1:1 from EliteClaw). They live under
# `agents/<name>/` next to AGENTS.md. Loading is OPTIONAL: a missing file simply
# contributes nothing and the routed agent falls back to the generic subagent
# prompt — no breakage (principle: additive, defence in depth).
SOUL_FILENAME = "SOUL.md"
TOOLS_FILENAME = "TOOLS.md"
# The directory holding the per-agent folders, relative to AGENTS.md's location.
AGENTS_HOME_DIRNAME = "agents"


@dataclass
class AgentSpec:
    """One specialized agent declared in AGENTS.md."""

    name: str
    description: str
    tool_prefixes: tuple[str, ...] = field(default_factory=tuple)
    # The agent's home dir (`agents/<name>/`), resolved at load time. None when
    # AGENTS.md was loaded from a path we couldn't anchor a home against (e.g. a
    # synthetic registry in tests). May be overridden by a `home:` frontmatter
    # key; otherwise it is derived from the agent name (name-based discovery).
    home: Path | None = None

    def owns(self, tool_name: str) -> bool:
        """True when `tool_name` belongs to this agent's tool group."""
        return any(tool_name.startswith(p) for p in self.tool_prefixes)

    def resolve_tools(self, available: tuple[str, ...]) -> tuple[str, ...]:
        """The subset of `available` tool names this agent is allowed to use."""
        return tuple(n for n in available if self.owns(n))

    def _read_home_file(self, filename: str) -> str | None:
        """Read `home/<filename>` if it exists, else None (optional loading)."""
        if self.home is None:
            return None
        candidate = self.home / filename
        if not candidate.is_file():
            return None
        text = candidate.read_text(encoding="utf-8").strip()
        return text or None

    def load_soul(self) -> str | None:
        """The agent's SOUL.md body, or None when absent."""
        return self._read_home_file(SOUL_FILENAME)

    def load_tools_doc(self) -> str | None:
        """The agent's TOOLS.md body, or None when absent."""
        return self._read_home_file(TOOLS_FILENAME)

    def compose_system_prompt(self, guardrail: str | None = None) -> str | None:
        """Assemble this agent's per-agent system prompt from SOUL + TOOLS.

        Returns the composed persona (SOUL.md, then TOOLS.md), optionally with
        `guardrail` appended so the generic anti-hallucination protection stays
        in force as defence in depth (SOUL/TOOLS are ADDITIVE persona, never a
        replacement for the guardrail or the tool allowlist).

        Returns None when NEITHER SOUL.md nor TOOLS.md exists, so the caller can
        fall back to the current generic subagent prompt with no breakage.
        """
        soul = self.load_soul()
        tools_doc = self.load_tools_doc()
        if soul is None and tools_doc is None:
            return None
        parts: list[str] = [p for p in (soul, tools_doc) if p]
        if guardrail:
            parts.append(guardrail.strip())
        return "\n\n".join(parts)


def _split_blocks(text: str) -> list[str]:
    """Split AGENTS.md into the raw `---`...`---` frontmatter blocks.

    A block is the text from a line that is exactly `---` up to the next such
    line. Prose between blocks (the human-readable header at the top of the
    file) is ignored — only fenced blocks define agents. We re-wrap each block
    in `---` fences so `parse_frontmatter` can read it unchanged.
    """
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].strip() == "---":
            body: list[str] = []
            i += 1
            while i < n and lines[i].strip() != "---":
                body.append(lines[i])
                i += 1
            # i is now at the closing fence (or EOF); skip it.
            i += 1
            if body:
                blocks.append("---\n" + "\n".join(body) + "\n---\n")
        else:
            i += 1
    return blocks


def _parse_prefixes(value: str) -> tuple[str, ...]:
    """Parse the comma-separated `tools:` value into a tuple of prefixes."""
    return tuple(p.strip() for p in value.split(",") if p.strip())


@dataclass
class AgentRegistry:
    """Holds the parsed specialized agents and renders the routing prompt."""

    _agents: dict[str, AgentSpec] = field(default_factory=dict)
    source: Path | None = None

    def add(self, spec: AgentSpec) -> None:
        self._agents[spec.name] = spec

    def get(self, name: str) -> AgentSpec | None:
        return self._agents.get(name)

    def all(self) -> list[AgentSpec]:
        return list(self._agents.values())

    def names(self) -> list[str]:
        return list(self._agents)

    def build_routing_prompt(self) -> str:
        """Render the agent list for the orchestrator's system prompt.

        Deliberately small: just name + when-to-use. The orchestrator never
        sees the specialized tools' JSON schemas — only which agents exist and
        when to use each (principle: small orchestrator prompt).
        """
        lines = [f"- {a.name}: {a.description}" for a in self._agents.values()]
        return "\n".join(lines)


def _find_agents_file(start: Path | None = None) -> Path | None:
    """Walk from `start` upward to find the nearest AGENTS.md (or None)."""
    cur = (start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for directory in [cur, *cur.parents]:
        candidate = directory / AGENTS_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_agents(path: Path | None = None) -> AgentRegistry:
    """Load + parse AGENTS.md into an AgentRegistry.

    `path` may point straight at a file (used by tests); otherwise AGENTS.md is
    discovered by walking up from the working directory (same convention as the
    memory loader). A missing file yields an empty registry — the caller decides
    whether that is fatal (the CLI treats an empty registry under --orchestrator
    as a hard error, principle #6).
    """
    agents_file = path if path is not None else _find_agents_file()
    registry = AgentRegistry(source=agents_file)
    if agents_file is None or not agents_file.is_file():
        return registry

    text = agents_file.read_text(encoding="utf-8")
    base = agents_file.parent
    for block in _split_blocks(text):
        fields, _ = parse_frontmatter(block)
        name = fields.get("name")
        if not name:
            continue
        registry.add(
            AgentSpec(
                name=name,
                description=fields.get("description", ""),
                tool_prefixes=_parse_prefixes(fields.get("tools", "")),
                home=_resolve_home(base, name, fields.get("home")),
            )
        )
    return registry


def _resolve_home(base: Path, name: str, home_override: str | None) -> Path:
    """Resolve an agent's home dir for SOUL.md/TOOLS.md discovery.

    `base` is the directory containing AGENTS.md. By default the home is
    name-based: `<base>/agents/<name>/` (matching the AGENTS.md agent `name`).
    An optional `home:` frontmatter key overrides this — absolute paths are used
    as-is, relative ones are resolved against `base`. The path is returned even
    when it does not exist; the OPTIONAL file reads in AgentSpec handle absence.
    """
    if home_override:
        override = Path(home_override)
        return override if override.is_absolute() else (base / override)
    return base / AGENTS_HOME_DIRNAME / name
