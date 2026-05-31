"""Layer 1 — Memory loader: directory walking, @import, auto-memory caps.

Implements the ADK Spec section 2 behaviours:

  - Directory walking : scan from the working dir up to `root`, concatenating
    every memory file found (no overriding — all are kept).
  - Scope inheritance : files farther from the working dir are read FIRST
    (global rules), files nearer are read LAST (specific rules that may
    override), so the nearest file has the final say in the prompt order.
  - Import support     : `@path/to/file.md` lines are expanded recursively, up
    to MAX_IMPORT_HOPS (5), with cycle guarding.
  - Auto memory caps   : AUTO_MEMORY.md is loaded but capped at 200 lines /
    25 KB so it cannot bloat the context.
  - Full human memory  : AGENT_MEMORY.md (and the CLAUDE.md alias) load in full.

EliteClaw had per-agent SOUL.md / TOOLS.md but no hierarchy, no @import, and
no auto-memory. This closes that gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Names recognised at each directory level. AGENT_MEMORY.md is canonical;
# CLAUDE.md is the compatibility alias (Claude Code interop).
MEMORY_FILENAMES: tuple[str, ...] = ("AGENT_MEMORY.md", "CLAUDE.md")
AUTO_MEMORY_FILENAME = "AUTO_MEMORY.md"

MAX_IMPORT_HOPS = 5
AUTO_MEMORY_MAX_LINES = 200
AUTO_MEMORY_MAX_BYTES = 25 * 1024

_IMPORT_RE = re.compile(r"^@(\S+)\s*$")


@dataclass
class MemoryBundle:
    """The merged, ready-to-inject memory text + provenance."""

    text: str = ""
    sources: list[Path] = field(default_factory=list)


@dataclass
class MemoryLoader:
    root: Path | None = None  # stop directory walking here; default = filesystem root

    def load(self, start: Path | None = None) -> MemoryBundle:
        """Walk from `start` up to `root`, concatenate memory files, resolve @imports."""
        start_dir = (start or Path.cwd()).resolve()
        if start_dir.is_file():
            start_dir = start_dir.parent
        stop = self.root.resolve() if self.root is not None else None

        # Collect directories from start up to (and including) root.
        chain: list[Path] = []
        cur = start_dir
        while True:
            chain.append(cur)
            if stop is not None and cur == stop:
                break
            if cur.parent == cur:  # filesystem root
                break
            cur = cur.parent

        # Scope inheritance: farthest-from-start (global) FIRST, nearest LAST.
        chain.reverse()

        parts: list[str] = []
        sources: list[Path] = []

        for directory in chain:
            for name in MEMORY_FILENAMES:
                f = directory / name
                if f.is_file():
                    raw = f.read_text(encoding="utf-8")
                    expanded = self._resolve_imports(raw, base=f.parent, seen={f.resolve()})
                    parts.append(expanded)
                    sources.append(f)
                    break  # one canonical memory file per directory

        # AUTO_MEMORY.md from the working dir, capped, appended last.
        auto = start_dir / AUTO_MEMORY_FILENAME
        if auto.is_file():
            capped = self._cap_auto_memory(auto.read_text(encoding="utf-8"))
            if capped.strip():
                parts.append(capped)
                sources.append(auto)

        return MemoryBundle(text="\n\n".join(p for p in parts if p.strip()), sources=sources)

    def _resolve_imports(
        self, text: str, base: Path, hops: int = 0, seen: set[Path] | None = None
    ) -> str:
        """Expand `@./file.md` lines recursively, up to MAX_IMPORT_HOPS."""
        if hops > MAX_IMPORT_HOPS:
            raise RuntimeError(
                f"@import exceeded MAX_IMPORT_HOPS={MAX_IMPORT_HOPS} (fail loudly, principle #6)"
            )
        seen = seen or set()

        out_lines: list[str] = []
        for line in text.splitlines():
            m = _IMPORT_RE.match(line.strip())
            if not m:
                out_lines.append(line)
                continue

            target = (base / m.group(1)).resolve()
            if target in seen:
                out_lines.append(f"<!-- @import cycle skipped: {m.group(1)} -->")
                continue
            if not target.is_file():
                out_lines.append(f"<!-- @import not found: {m.group(1)} -->")
                continue

            seen.add(target)
            nested = target.read_text(encoding="utf-8")
            out_lines.append(
                self._resolve_imports(nested, base=target.parent, hops=hops + 1, seen=seen)
            )

        return "\n".join(out_lines)

    @staticmethod
    def _cap_auto_memory(text: str) -> str:
        """Keep only the first 200 lines / 25 KB of AUTO_MEMORY (whichever hits first)."""
        lines = text.splitlines()[:AUTO_MEMORY_MAX_LINES]
        capped = "\n".join(lines)
        if len(capped.encode("utf-8")) > AUTO_MEMORY_MAX_BYTES:
            encoded = capped.encode("utf-8")[:AUTO_MEMORY_MAX_BYTES]
            # decode safely, dropping any partial trailing multibyte char
            capped = encoded.decode("utf-8", errors="ignore")
        return capped

    def append_auto_memory(self, path: Path, note: str) -> None:
        """Append a note to AUTO_MEMORY.md, enforcing the 200-line / 25 KB cap (FIFO trim)."""
        existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        existing.append(note.rstrip("\n"))

        # FIFO trim by line count.
        if len(existing) > AUTO_MEMORY_MAX_LINES:
            existing = existing[-AUTO_MEMORY_MAX_LINES:]

        text = "\n".join(existing) + "\n"
        # FIFO trim by byte size (drop oldest lines until under the cap).
        while len(text.encode("utf-8")) > AUTO_MEMORY_MAX_BYTES and len(existing) > 1:
            existing = existing[1:]
            text = "\n".join(existing) + "\n"

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX
