"""Layer 1 — Memory (constitutional).

Memory is the agent's source of truth (principle #4). PyClaw loads three files
and merges them with scope inheritance:

  AGENT_MEMORY.md  : the constitution (project-level, version-controlled)
  CLAUDE.md        : compatibility alias (principle #7 — read if present)
  AUTO_MEMORY.md   : agent-written memory (capped at 200 lines / 25 KB)

Loading behaviour (ADK Spec):
  - directory walking : scan from cwd up to root, concatenating each level's
    memory files so child scopes inherit parent scopes.
  - @path import      : a line `@./other.md` pulls in another file, recursively,
    up to 5 hops (cycle/over-depth -> fail loudly, principle #6).
"""

from pyclaw.memory.loader import MemoryLoader, MemoryBundle  # noqa: F401
