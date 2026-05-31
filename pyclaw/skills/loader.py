"""Layer 2 — Skill loader: selection (auto + manual), full load, chaining.

EliteClaw exposed a `read_skill` meta-tool for on-demand full loads; PyClaw
keeps that and adds auto-detection + chaining.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyclaw.skills.registry import Invocation, SkillMeta, SkillRegistry, parse_frontmatter

_WORD = re.compile(r"[a-z0-9]+")


def _keywords(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


@dataclass
class SkillLoader:
    registry: SkillRegistry

    def detect(self, user_request: str, *, min_overlap: int = 1) -> list[SkillMeta]:
        """Auto-detect which AUTO skills match the request.

        Lightweight keyword overlap between the request and each skill's
        name+description. Deterministic and dependency-free; swap in embeddings
        later without changing callers. Returns AUTO skills ranked by overlap
        (descending), ties broken by name for stable ordering.
        """
        req = _keywords(user_request)
        scored: list[tuple[int, str, SkillMeta]] = []
        for meta in self.registry.all():
            if meta.invocation is not Invocation.AUTO:
                continue
            overlap = len(req & _keywords(f"{meta.name} {meta.description}"))
            if overlap >= min_overlap:
                scored.append((overlap, meta.name, meta))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [meta for _, _, meta in scored]

    def resolve_manual(self, command: str) -> SkillMeta | None:
        """Map a '/skill-name' command to its SkillMeta (MANUAL or AUTO)."""
        name = command.lstrip("/").strip()
        return self.registry.get(name)

    def load_full(self, meta: SkillMeta) -> str:
        """Read the full SKILL.md body (instructions), stripping frontmatter.

        This is the on-demand heavy load (principle #2) — only called once a
        skill is actually selected.
        """
        _, body = parse_frontmatter(meta.path.read_text(encoding="utf-8"))
        return body

    def expand_chain(self, meta: SkillMeta, seen: set[str] | None = None) -> list[SkillMeta]:
        """Resolve `chains_to` follow-on skills via DFS, cycle-guarded.

        Returns the chained skills in order (not including `meta` itself).
        """
        seen = seen if seen is not None else set()
        seen.add(meta.name)
        ordered: list[SkillMeta] = []
        for nxt_name in meta.chains_to:
            if nxt_name in seen:
                continue
            nxt = self.registry.get(nxt_name)
            if nxt is None:
                continue
            ordered.append(nxt)
            ordered.extend(self.expand_chain(nxt, seen))
        return ordered
