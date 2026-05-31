"""PostEdit auto-format hook.

After a file edit, run the formatter deterministically (principle #1) so code
style never depends on the LLM remembering to format. Currently formats Python
files with ruff (falling back to black if ruff is absent). No-op for non-Python
files or when no formatter is installed.

The payload's edited file path is taken from arguments['path'] (or 'file').
"""

from __future__ import annotations

import shutil
import subprocess

from pyclaw.hooks.events import HookAction, HookPayload, HookResult


def _edited_path(payload: HookPayload) -> str | None:
    args = payload.arguments or {}
    for key in ("path", "file", "filename"):
        val = args.get(key)
        if isinstance(val, str):
            return val
    return None


def autoformat(payload: HookPayload) -> HookResult:
    """Format the just-edited Python file. NOTIFY on success, ALLOW otherwise."""
    path = _edited_path(payload)
    if not path or not path.endswith(".py"):
        return HookResult(action=HookAction.ALLOW)

    if shutil.which("ruff"):
        cmd = ["ruff", "format", path]
        tool = "ruff"
    elif shutil.which("black"):
        cmd = ["black", "-q", path]
        tool = "black"
    else:
        return HookResult(action=HookAction.ALLOW)  # no formatter available

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return HookResult(action=HookAction.ALLOW)  # never block on a format failure

    return HookResult(
        action=HookAction.NOTIFY,
        message=f"auto-formatted {path} with {tool}",
        source_hook="autoformat",
    )
