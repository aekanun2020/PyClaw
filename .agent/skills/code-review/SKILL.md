---
name: code-review
description: Review a pull request or diff for bugs, style, tests, and security before merge
version: 1.0.0
invocation: auto
subagent: review
model_preference:
chains_to:
---

## Purpose
Give a consistent, thorough review of a code change (PR or diff) so nothing
slips through. Use this whenever the task is to review, critique, or sign off on
code before it is merged.

## Steps
1. Read the diff in full. Note the scope and intent of the change.
2. Correctness: look for logic errors, off-by-one, unhandled edge cases, and
   incorrect error handling.
3. Tests: confirm new behaviour is covered. Flag any change with no test.
4. Style: check against the project rules in AGENT_MEMORY.md (naming, line
   length, typing). Do not nitpick what a formatter already enforces.
5. Security: watch for injected commands, secrets in code, unsafe deserialization,
   and missing input validation. Anything touching `.env`/`secrets/` is a red flag.
6. Summarize: list issues grouped as Blocking / Should-fix / Nit, then a verdict
   (approve / request changes).

## Output format
- **Blocking:** issues that must be fixed before merge
- **Should-fix:** strong suggestions
- **Nit:** optional polish
- **Verdict:** approve | request changes (one line)

## Reference
@./reference.md
