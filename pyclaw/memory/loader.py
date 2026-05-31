"""Layer 1 — Memory loader: directory walking, @import, auto-memory caps.

EliteClaw had per-agent SOUL.md / TOOLS.md but no hierarchy, no @import, and
no auto-memory (🟡 -> 🟢).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Names recognised at each directory level. AGENT_MEMORY.md is canonical;
# CLAUDE.md is the compatibility alias (principle #7).
MEMORY_FILENAMES: tuple[str, ...] = ("AGENT_MEMORY.md", "CLAUDE.md")
AUTO_MEMORY_FILENAME = "AUTO_MEMORY.md"

MAX_IMPORT_HOPS = 5
AUTO_MEMORY_MAX_LINES = 200
AUTO_MEMORY_MAX_BYTES = 25 * 1024


@dataclass
class MemoryBundle:
    """The merged, ready-to-inject memory text + provenance."""

    text: str = ""
    sources: list[Path] = field(default_factory=list)


@dataclass
class MemoryLoader:
    root: Path | None = None  # stop directory walking here; default = filesystem root

    def load(self, start: Path | None = None) -> MemoryBundle:
        """Walk from `start` up to `root`, concatenate memory files, resolve @imports.

        TODO:
          - collect dirs from start up to root (inclusive)
          - for each dir (parent-first so children override), read MEMORY_FILENAMES
          - resolve @path imports via self._resolve_imports (depth-limited)
          - append AUTO_MEMORY.md last, after enforcing caps
          - return MemoryBundle(text, sources)
        """
        raise NotImplementedError("MemoryLoader.load: directory walk + merge (scaffold)")

    def _resolve_imports(self, text: str, base: Path, hops: int = 0) -> str:
        """Expand `@./file.md` lines recursively, up to MAX_IMPORT_HOPS.

        TODO:
          - if hops > MAX_IMPORT_HOPS: raise (fail loudly, principle #6)
          - for each line matching r'^@(\\S+)': read & recurse, guard cycles
        """
        raise NotImplementedError("MemoryLoader._resolve_imports (scaffold)")

    def append_auto_memory(self, path: Path, note: str) -> None:
        """Append a note to AUTO_MEMORY.md, enforcing the 200-line / 25 KB cap.

        TODO:
          - read existing; append note; if over caps, trim oldest lines (FIFO)
          - write back atomically
        """
        raise NotImplementedError("MemoryLoader.append_auto_memory (scaffold)")
