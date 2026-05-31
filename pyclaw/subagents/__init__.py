"""Layer 4 — Subagents (bounded delegation, principle #3).

EliteClaw could route to specialized agents but only sequentially and without
model preferences (🟢 with gaps). PyClaw adds built-in subagent types, a
parallel agent team under a lead, and model_preference — while enforcing the
delegation rules in code.

Built-in types (ADK Spec):
  explore : read-only investigation (no mutating tools)
  plan    : produce a plan, no execution
  review  : critique/verify an artifact
  general : general-purpose execution

Delegation rules (enforced, not prompted — and checked by the PreSubagentSpawn
hook):
  - NO nested spawning (a subagent cannot spawn subagents)
  - return result only (isolated context; parent gets the summary, not history)
  - inherit-then-restrict (subagent tools ⊆ parent tools, then narrowed by type)
"""

from pyclaw.subagents.types import SubagentType, SubagentSpec  # noqa: F401
from pyclaw.subagents.runner import SubagentRunner, ParallelTeam  # noqa: F401
