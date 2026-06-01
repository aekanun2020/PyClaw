"""Layer 2 — Skill registry: parse frontmatter, hold lightweight metadata.

Only frontmatter is parsed at startup (lazy loading, principle #2). The heavy
instruction body is read on demand by SkillLoader when a skill is actually
selected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a '---' YAML-ish frontmatter block from the body.

    Returns (fields, body). Only simple `key: value` lines are parsed (enough
    for SKILL.md), plus comma-separated lists for `chains_to`. Falls back to an
    empty mapping when there is no frontmatter — we never crash on a stray file.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fields: dict[str, str] = {}
    body_start = len(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            # strip inline comments and surrounding quotes
            value = value.split("#", 1)[0].strip().strip("'\"")
            fields[key.strip()] = value
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return fields, body


class Invocation(str, Enum):
    AUTO = "auto"        # selected by semantic auto-detection
    MANUAL = "manual"    # only via /skill-name
    ALWAYS = "always"    # always injected into the owning agent's system prompt


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

        Lazy loading (principle #2): we read only the small frontmatter here, not
        the instruction body — that is loaded on demand by SkillLoader.
        """
        if not skills_root.is_dir():
            return
        for skill_file in sorted(skills_root.glob("**/SKILL.md")):
            fields, _ = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            name = fields.get("name") or skill_file.parent.name
            chains = [c.strip() for c in fields.get("chains_to", "").split(",") if c.strip()]
            self._skills[name] = SkillMeta(
                name=name,
                description=fields.get("description", ""),
                path=skill_file,
                version=fields.get("version", "0.0.0"),
                invocation=Invocation(fields.get("invocation", "auto")),
                subagent=fields.get("subagent") or None,
                model_preference=fields.get("model_preference") or None,
                chains_to=chains,
            )

    def all(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)

    def build_prompt_catalog(self) -> str:
        """Render name+description list for the system prompt (cheap, lazy)."""
        return "\n".join(f"- {m.name}: {m.description}" for m in self._skills.values())
