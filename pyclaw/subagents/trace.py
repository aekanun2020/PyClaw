"""Bridge the parent loop's live `on_tool` observer to the spawn tool.

Problem: `AgentLoop._invoke_tool` calls `tool.fn(arguments)` only — a tool's
function never receives the parent's `on_tool` observer. The `spawn_subagent`
tool needs that observer so it can forward it into each subagent's loop and so
the user can SEE `[sub#N]` lines interleave under `--trace`.

Mechanism (item d): a `contextvars.ContextVar` holder. The loop publishes the
active `on_tool` around `self.tools.dispatch(...)`; the spawn tool reads it. We
chose this over (1) a shared mutable list captured in the tool closure or (2)
extending the `Tool.fn` signature because:

  - it touches NEITHER the `Tool`/`ToolRegistry`/`_invoke_tool` calling
    contract NOR any other tool — every existing tool keeps `fn(arguments)`;
  - it is the parent loop's OWN thread that runs the spawn tool fn, so the
    contextvar set in `_invoke_tool` is visible when `_spawn` reads it (no
    cross-thread copy needed for the bridge itself);
  - per-subagent fan-out is then done by explicit parameter passing
    (`ParallelTeam.run(specs, on_tool)` -> `spawn(spec, on_tool=labeled)`), so
    each worker thread gets its OWN labelled observer rather than racing on a
    shared one.

The observer stays a PURE observer — this module only reads/writes the holder,
never changes control flow.
"""

from __future__ import annotations

import contextvars
from typing import Callable

_active_on_tool: contextvars.ContextVar[Callable | None] = contextvars.ContextVar(
    "pyclaw_active_on_tool", default=None
)


def set_active_on_tool(on_tool):
    """Publish the active trace observer; returns a token for `reset`."""
    return _active_on_tool.set(on_tool)


def reset_active_on_tool(token) -> None:
    """Restore the previous observer (call in a finally to avoid leaks)."""
    _active_on_tool.reset(token)


def get_active_on_tool():
    """Read the active trace observer (None when tracing is off)."""
    return _active_on_tool.get()
