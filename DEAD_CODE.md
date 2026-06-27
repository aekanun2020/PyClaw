# DEAD_CODE.md — dead / dormant / orphan inventory

> Audited 2026-06-27 against HEAD `6dc22df` (read-only; nothing was deleted).
> Re-verify with the grep checks below before acting — code may have moved.
>
> Purpose: when an AI agent scans this repo it must NOT be misled into thinking
> there are two ways to load hooks (there is only one: `PluginLoader`), nor
> "fix" a file that is intentionally dormant.

## TL;DR

| Item | Class | Anything bound to it | Safe to delete? | Recommended action |
|---|---|---|---|---|
| `config.hooks_dir` property (`pyclaw/config.py`) | DEAD | no caller anywhere | yes | delete (also fix test docstring) |
| `.agent/hooks/default_hooks.yaml` | DEAD | only a test *docstring* | yes | delete |
| `.agent/hooks/example_pretooluse.yaml` | DEAD | targets don't exist | yes (safest) | delete |
| `autoformat` hook (`pyclaw_hooks/format.py`) | DORMANT | unit test calls it directly | **no — keep** | keep until PostEdit fire-site (B1) |
| `demo_spec_proof.py` | ORPHAN | runnable, just undocumented | yes but wasteful | **add to docs/demos/README.md instead** |

## Root cause / context

These three DEAD items are leftovers from the **pre-plugin hook-loading era**.
`config.hooks_dir` used to point the runtime at `.agent/hooks/*.yaml`. That path
was abandoned: the only code that registers hooks at startup is
`PluginLoader.load_all()` (`pyclaw/cli.py:100`), which scans `*/plugin.yaml` under:

- `.agent/plugins/`               → flat-loop hook set
- `.agent/orchestrator-plugins/`  → orchestrator combined-answer enforce
- `agents/<name>/plugins/`        → per-agent grounding

Leaving the dead `.agent/hooks/` files in place is harmless to runtime (no code
reads them) but **misleads readers** into thinking two load paths exist. This was
the original CCTV regression root cause (specs sat in `default_hooks.yaml`, never
loaded, every answer passed ungated).

## Per-item detail + verification

### 1. `config.hooks_dir` — DEAD property
```
grep -rn "hooks_dir" --include="*.py" .      # → only the def at pyclaw/config.py:77, zero callers
```
`tests/test_plugin_hook_loading.py` references `.agent/hooks/default_hooks.yaml`
ONLY in its module docstring (explaining root cause); no assertion depends on it.
Delete-safety: deleting the property + yaml does not break any test; just update
that docstring so it doesn't reference a removed file.

### 2. `.agent/hooks/default_hooks.yaml` — DEAD config
Self-documents as `⚠️ SUPERSEDED — this file is NOT read by the runtime.` and
holds `hooks: []`. Kept historically as documentation only.

### 3. `.agent/hooks/example_pretooluse.yaml` — DEAD example
All three targets point at things that DO NOT EXIST:
- `pyclaw_hooks.pdpa:guard`  → no `pyclaw_hooks/pdpa.py`
- `pyclaw_hooks.redact:run`  → no `pyclaw_hooks/redact.py`
- `./.agent/hooks/notify_delete.sh` → file absent

So it can never load; if someone activated it, startup would fail at import.
Pure paper example. Safest of the three to remove.

### 4. `autoformat` (`pyclaw_hooks/format.py`) — DORMANT, DO NOT DELETE
Registered in `.agent/plugins/pdpa-grounding/plugin.yaml` on event `PostEdit`,
but `pyclaw/core/loop.py` has **no PostEdit fire-site** — so it is never invoked
at runtime (tracked as B1). It is NOT dead:
- the function is correct and covered by `tests/test_shipped_hooks.py` (which
  calls `autoformat()` directly, not through the engine);
- it is kept for parity with the intended default hook set.
Keep it. It activates for free once an edit tool emits PostEdit.

### 5. `demo_spec_proof.py` — ORPHAN demo
A real, runnable script (`python demo_spec_proof.py`) that proves the
`agent-spec-claude.md` MVP via the public API, but it is the only demo NOT listed
in `docs/demos/README.md` (the other five are). Not junk — just undocumented.
Recommended: add it to the demos README rather than delete.

## NOT dead (don't be fooled)

- `pyclaw/core/_test_retry_hooks.py`, `pyclaw/subagents/_test_hooks.py` — the
  `_test_` prefix is misleading; they MUST live in-package so `PythonRunner` can
  import them by `module:func` target. Used by test_inloop_retry / test_subagents
  / test_plugins. Do not move to tests/.
- `.agent/plugins/pdpa-grounding/plugin.yaml` vs `agents/pdpa-agent/plugins/...`
  — look duplicated but serve different loops (flat vs per-agent). Both needed.
- All grounding factories (`make_record/merge/enforce_grounding`,
  `make_extract_ids`, `default_result_parser`) — used by the PDPA wrapper + tests.
