"""Layer 2 — Skill system.

A skill is a SKILL.md file with YAML frontmatter + instruction body. PyClaw
keeps EliteClaw's strengths (frontmatter parsing + on-demand full load) and
adds what it lacked (🟡 -> 🟢):

  - auto-detection : semantic matching of the user request to a skill
  - /skill-name    : manual invocation
  - chaining       : a skill may declare follow-on skills
  - context inject  : matched skill instructions injected into the loop

Frontmatter fields (ADK Spec):
  name, version, description, invocation (auto|manual),
  subagent (optional type), model_preference (optional)
"""

from pyclaw.skills.registry import SkillRegistry, SkillMeta  # noqa: F401
from pyclaw.skills.loader import SkillLoader  # noqa: F401
