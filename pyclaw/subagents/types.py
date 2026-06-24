"""Layer 4 — Subagent types & spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SubagentType(str, Enum):
    EXPLORE = "explore"   # read-only
    PLAN = "plan"         # planning only, no execution
    REVIEW = "review"     # critique/verify
    GENERAL = "general"   # general-purpose


# Per-type tool restrictions applied AFTER inheriting the parent's tool set
# (inherit-then-restrict). Empty tuple in `deny` means "no extra restriction".
TYPE_TOOL_POLICY: dict[SubagentType, dict[str, tuple[str, ...]]] = {
    SubagentType.EXPLORE: {"deny": ("write_file", "delete_file", "deploy_to_production")},
    SubagentType.PLAN: {"deny": ("write_file", "delete_file", "deploy_to_production")},
    SubagentType.REVIEW: {"deny": ("delete_file", "deploy_to_production")},
    SubagentType.GENERAL: {"deny": ()},
}


@dataclass
class SubagentSpec:
    """A request to run one subagent."""

    type: SubagentType
    objective: str
    model_preference: str | None = None        # override LLM model
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)  # resolved at spawn time
    is_nested: bool = False                     # must stay False — guarded by runner
    # Optional per-agent system prompt (persona + boundaries + tool rules). When
    # None the isolated loop uses the generic subagent prompt (current behaviour
    # — backward compatible). The orchestrator sets this from an agent's
    # SOUL.md + TOOLS.md so a routed specialized agent runs with its OWN prompt.
    system_prompt: str | None = None
    # Optional per-agent plugin root (mechanism, NOT a domain concept). When set,
    # the isolated loop loads the plugin manifests under this directory into its
    # OWN HookEngine, so a routed agent runs with exactly the hooks it declares —
    # e.g. the pdpa-agent loads its citation-grounding plugin and self-enforces
    # inside its own loop. When None the loop gets an empty engine (current
    # behaviour — subagents inherit no parent hooks). The orchestrator sets this
    # to `<agent.home>/plugins` so grounding binds to the agent that needs it,
    # not to a global engine. Path-typed only; no domain vocabulary lives here.
    plugins_root: "object | None" = None  # pathlib.Path | None
