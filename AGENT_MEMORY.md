# AGENT_MEMORY.md — PyClaw constitution

> Layer 1 source of truth (principle #4). `CLAUDE.md` is supported as an alias
> (principle #7). Lines beginning with `@` import another file (≤5 hops).

## Project
- **PyClaw = EliteClaw rewritten in Python + more features.**
- Deterministic-first: policy lives in Hooks (Layer 3), never in prompts (#1).

## Conventions
- Tools never run from raw model output — always via HookEngine + PermissionPolicy.
- Missing required layers fail loudly (#6).

# @import example (uncomment to pull in team rules):
# @./team/standards.md
