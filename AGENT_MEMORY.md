# AGENT_MEMORY.md — PyClaw constitution

> Layer 1 source of truth (principle #4). `CLAUDE.md` is supported as an alias
> (principle #7). Lines beginning with `@` import another file (≤5 hops).
> Keep this short — short memory = better adherence.

## Project
- **PyClaw = EliteClaw rewritten in Python + more features.**
- Deterministic-first: policy lives in Hooks (Layer 3), never in prompts (#1).
- Implements the 5-Layer ADK Spec (+ Layer 0 Runtime) and the MVP checklist.

## Architectural rules (must hold every time)
- A tool is NEVER executed from raw model output. It must pass through
  `AgentLoop._invoke_tool`: PermissionPolicy → PreToolUse hook → HITL → exec →
  PostToolUse hook → audit. Do not add tool-execution paths that bypass this.
- Anything that "must happen every time" is a Hook, not a prompt instruction (#1).
- Destructive ops (delete_file, deploy_to_production, modify_secrets) and any
  access to `.env` / `secrets/` / `.git/` / `*.pem` are blocked by the
  `block_destructive` PreToolUse hook. Do not weaken it.
- Missing required layers fail loudly — never silently fall back to a prompt (#6).
- Subagents: inherit-then-restrict tools, no nested spawning, return summary only.

## Coding standards
- Python ≥ 3.11, full type hints; module-level docstrings on every file.
- Line length 100 (ruff). Format with `ruff format`; do not hand-format.
- Prefer dataclasses for typed records; inject collaborators for testability.
- Fail loudly: raise on misconfiguration rather than guessing a default.
- Every new behaviour ships with a pytest. No untested public function.

## Layer placement (where new logic goes)
- Persistent fact/rule → this file (AGENT_MEMORY.md).
- Repeated step-by-step procedure → a Skill (.agent/skills/<name>/SKILL.md).
- Must-happen-every-time enforcement → a Hook (pyclaw_hooks/ + .agent/hooks).

# @import example (uncomment to pull in team rules):
# @./team/standards.md
