"""Layer 2 — Skill loader: selection (auto + manual), full load, chaining.

EliteClaw exposed a `read_skill` meta-tool for on-demand full loads; PyClaw
keeps that and adds auto-detection + chaining.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyclaw.skills.registry import SkillMeta, SkillRegistry


@dataclass
class SkillLoader:
    registry: SkillRegistry

    def detect(self, user_request: str) -> list[SkillMeta]:
        """Auto-detect which AUTO skills match the request (semantic matching).

        TODO:
          - embed/keyword-match user_request against each SkillMeta.description
          - return ranked matches above a threshold (invocation == AUTO only)
        """
        raise NotImplementedError("SkillLoader.detect (scaffold)")

    def resolve_manual(self, command: str) -> SkillMeta | None:
        """Map a '/skill-name' command to its SkillMeta (MANUAL or AUTO)."""
        name = command.lstrip("/").strip()
        return self.registry.get(name)

    def load_full(self, meta: SkillMeta) -> str:
        """Read the full SKILL.md body (instructions) for injection.

        TODO:
          - read meta.path, strip frontmatter, return the instruction body
        """
        raise NotImplementedError("SkillLoader.load_full (scaffold)")

    def expand_chain(self, meta: SkillMeta, seen: set[str] | None = None) -> list[SkillMeta]:
        """Resolve `chains_to` follow-on skills (cycle-guarded).

        TODO:
          - DFS over meta.chains_to via registry.get, guard cycles with `seen`
          - return ordered list of chained SkillMeta
        """
        raise NotImplementedError("SkillLoader.expand_chain (scaffold)")
