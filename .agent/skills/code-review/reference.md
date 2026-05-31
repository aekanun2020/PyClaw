# code-review reference

Loaded lazily (only when the code-review skill is invoked), via the `@./reference.md`
import in SKILL.md.

## Review checklist (quick)
- [ ] Does the change do what the PR description says — and only that?
- [ ] New/changed behaviour has tests; edge cases covered.
- [ ] No secrets, tokens, or credentials committed.
- [ ] Errors are handled and surfaced, not swallowed.
- [ ] Public functions are typed and documented.
- [ ] No obvious performance traps (N+1, unbounded loops, sync I/O in hot paths).

## Severity guide
| Label | Meaning | Blocks merge? |
|-------|---------|---------------|
| Blocking | correctness/security defect | yes |
| Should-fix | maintainability/readability risk | author's call, but argue for it |
| Nit | cosmetic | no |

## PyClaw-specific
- Any deterministic rule belongs in a Hook, not a prompt or a code comment (#1).
- Tool execution must go through the permission layer + PreToolUse hook.
- Memory edits go to AGENT_MEMORY.md (human) or AUTO_MEMORY.md (agent), never inline.
