"""Layer 4 — Subagent runner + parallel team.

Spawning always goes through the PreSubagentSpawn hook first (Layer 3), so the
delegation rules are deterministic policy, not a prompt suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyclaw.subagents.types import TYPE_TOOL_POLICY, SubagentSpec


@dataclass
class SubagentResult:
    spec: SubagentSpec
    summary: str            # the ONLY thing returned to the parent (isolated context)
    ok: bool = True
    error: str | None = None


@dataclass
class SubagentRunner:
    parent_tools: tuple[str, ...] = field(default_factory=tuple)

    def resolve_tools(self, spec: SubagentSpec) -> tuple[str, ...]:
        """inherit-then-restrict: parent_tools minus this type's denied tools."""
        denied = set(TYPE_TOOL_POLICY[spec.type].get("deny", ()))
        return tuple(t for t in self.parent_tools if t not in denied)

    def spawn(self, spec: SubagentSpec) -> SubagentResult:
        """Run one subagent to completion in an isolated context.

        TODO:
          - if spec.is_nested: raise (NO nested spawning, principle #3)
          - fire PreSubagentSpawn hook; honour BLOCK
          - spec.allowed_tools = self.resolve_tools(spec)
          - run an isolated agent loop with model_preference
          - return SubagentResult(summary=...) — never leak full history
        """
        raise NotImplementedError("SubagentRunner.spawn (scaffold)")


@dataclass
class ParallelTeam:
    """A lead coordinates several subagents that run in parallel.

    This is one of PyClaw's "more features" over EliteClaw (sequential only).
    """

    runner: SubagentRunner

    def run(self, specs: list[SubagentSpec]) -> list[SubagentResult]:
        """Run `specs` concurrently and collect results.

        TODO:
          - use a thread/async pool (each spawn is I/O-bound on the LLM)
          - all members are non-nested (depth stays 1 — principle #3)
          - aggregate results for the lead to synthesise
        """
        raise NotImplementedError("ParallelTeam.run (scaffold)")
