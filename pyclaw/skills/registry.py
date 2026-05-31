"""Layer 2 — Skill registry: parse frontmatter, hold lightweight metadata.

Only frontmatter is parsed at startup (lazy loading, principle #2). The heavy
instruction body is read on demand by SkillLoader when a skill is actually
selected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Invocation(str, Enum):
    AUTO = "auto"        # selected by semantic auto-detection
    MANUAL = "manual"    # only via /skill-name


@dataclass
class SkillMeta:
    """Parsed from SKILL.md frontmatter — cheap to keep in memory for all skills."""

    name: str
    description: str
    path: Path
    version: str = "0.0.0"
    invocation: Invocation = Invocation.AUTO
    subagent: str | None = None          # run inside this subagent type, if set
    model_preference: str | None = None  # override LLM model for this skill
    chains_to: list[str] = field(default_factory=list)  # follow-on skill names


@dataclass
class SkillRegistry:
    _skills: dict[str, SkillMeta] = field(default_factory=dict)

    def scan(self, skills_root: Path) -> None:
        """Find every SKILL.md under `skills_root` and parse frontmatter only.

        TODO:
          - glob '**/SKILL.md'
          - parse YAML frontmatter (between leading '---' fences)
          - build SkillMeta and self._skills[name] = meta
        """
        raise NotImplementedError("SkillRegistry.scan (scaffold)")

    def all(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)

    def build_prompt_catalog(self) -> str:
        """Render name+description list for the system prompt (cheap, lazy)."""
        # TODO: format each SkillMeta as "- {name}: {description}"
        raise NotImplementedError("SkillRegistry.build_prompt_catalog (scaffold)")
