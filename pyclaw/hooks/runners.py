"""Layer 3 — Hook runners: HOW a hook executes.

A hook spec declares an execution `type`; the matching runner runs it and
parses a HookResult. Per the ADK Spec, hooks can be:
  - bash   : run a shell command; stdout JSON -> HookResult
  - python : import & call a `def hook(payload) -> HookResult`
  - http   : POST payload to a URL; JSON response -> HookResult
  - llm    : ask an LLM to classify (use sparingly — see note)

NOTE on determinism: bash/python/http runners are deterministic and are the
preferred home for policy (principle #1). The `llm` runner is NON-deterministic
and must only be used for soft/advisory hooks (e.g. tone checks), never for a
hard constraint.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Protocol

from pyclaw.hooks.events import HookAction, HookPayload, HookResult

# Hard cap so a misbehaving hook can never hang the agent loop.
DEFAULT_HOOK_TIMEOUT_SECONDS = 10


def _payload_to_dict(payload: HookPayload) -> dict[str, Any]:
    """Serialise a payload to a plain dict for a runner's stdin.

    Includes a spec-compatible `input` alias for `arguments` so hook scripts
    written against the Claude-style ADK spec (which uses `input`) work
    unchanged alongside PyClaw's native `arguments` key.
    """
    data = asdict(payload)
    data["event"] = payload.event.value
    data["input"] = data.get("arguments", {})  # spec alias
    return data


def _result_from_dict(data: dict[str, Any], *, base: HookPayload) -> HookResult:
    """Parse a runner's JSON output into a HookResult.

    Expected shape: {"action": "allow|modify|block|notify",
                     "message": "..."?, "modified": {..payload fields..}?}
    Unknown/missing action -> ALLOW (fail-open is fine here because a separate
    BLOCK hook, or the permission layer, enforces hard policy).
    """
    action = HookAction(data.get("action", "allow"))
    # `reason` is the spec's name for the human-readable message.
    message = data.get("message") or data.get("reason")
    modified_payload: HookPayload | None = None
    if action is HookAction.MODIFY:
        patch = data.get("modified", {}) or {}
        # Spec uses `modified_input` to mean new tool arguments.
        new_args = data.get("modified_input", patch.get("arguments", base.arguments))
        modified_payload = HookPayload(
            event=base.event,
            tool=patch.get("tool", base.tool),
            arguments=new_args,
            result=patch.get("result", base.result),
            user=patch.get("user", base.user),
            extra=patch.get("extra", base.extra),
        )
    return HookResult(action=action, modified_payload=modified_payload, message=message)


class RunnerType(str, Enum):
    BASH = "bash"
    PYTHON = "python"
    HTTP = "http"
    LLM = "llm"


class HookRunner(Protocol):
    def run(self, target: str, payload: HookPayload) -> HookResult: ...


class BashRunner:
    """`target` is a shell command. Payload piped as JSON on stdin; stdout JSON parsed.

    A non-zero exit code is treated as BLOCK (fail-closed) — a hook script that
    errors must not silently allow the action.
    """

    timeout: int = DEFAULT_HOOK_TIMEOUT_SECONDS

    def run(self, target: str, payload: HookPayload) -> HookResult:
        stdin = json.dumps(_payload_to_dict(payload))
        try:
            proc = subprocess.run(
                target,
                shell=True,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return HookResult(
                action=HookAction.BLOCK,
                message=f"hook command timed out after {self.timeout}s",
            )

        if proc.returncode != 0:
            return HookResult(
                action=HookAction.BLOCK,
                message=(proc.stderr or proc.stdout or "hook command failed").strip(),
            )

        out = proc.stdout.strip()
        if not out:
            return HookResult(action=HookAction.ALLOW)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # Treat non-JSON stdout as an advisory notice rather than crashing.
            return HookResult(action=HookAction.NOTIFY, message=out)
        return _result_from_dict(data, base=payload)


class PythonRunner:
    """`target` is 'module:function'. Function signature: (HookPayload) -> HookResult."""

    def run(self, target: str, payload: HookPayload) -> HookResult:
        if ":" not in target:
            raise ValueError(f"PythonRunner target must be 'module:function', got {target!r}")
        module_name, func_name = target.split(":", 1)
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        result = func(payload)
        if not isinstance(result, HookResult):
            raise TypeError(
                f"{target} must return HookResult, got {type(result).__name__}"
            )
        return result


@dataclass
class HttpRunner:
    """`target` is a URL. POST payload JSON; response JSON parsed -> HookResult.

    A network error or non-2xx is treated as BLOCK (fail-closed) so a flaky
    policy endpoint cannot silently allow an action. The poster is injectable
    for testing without real network I/O.
    """

    timeout: int = DEFAULT_HOOK_TIMEOUT_SECONDS
    poster: Any = None  # (url, json_body, timeout) -> (status_code, json_dict)

    def run(self, target: str, payload: HookPayload) -> HookResult:
        body = _payload_to_dict(payload)
        try:
            if self.poster is not None:
                status, data = self.poster(target, body, self.timeout)
            else:
                import httpx

                resp = httpx.post(target, json=body, timeout=self.timeout)
                status = resp.status_code
                data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001 - network failure -> fail-closed
            return HookResult(action=HookAction.BLOCK, message=f"hook HTTP error: {exc}")

        if status >= 400:
            return HookResult(action=HookAction.BLOCK, message=f"hook HTTP {status}")
        if not isinstance(data, dict):
            return HookResult(action=HookAction.ALLOW)
        return _result_from_dict(data, base=payload)


@dataclass
class LlmRunner:
    """`target` is a prompt template. ADVISORY ONLY — never a hard constraint.

    Because hosted LLM inference is non-deterministic (see core/llm docstring),
    this runner may only ever produce ALLOW or NOTIFY — it can flag a concern
    but can NEVER block or modify (that would put policy in a prompt, violating
    principle #1). A BLOCK/MODIFY classification is downgraded to NOTIFY.

    The provider is injectable so tests run without a live model.
    """

    provider: Any = None  # object with .complete(messages) -> obj with .text

    def run(self, target: str, payload: HookPayload) -> HookResult:
        provider = self.provider
        if provider is None:
            from pyclaw.core.llm import OpenRouterProvider

            provider = OpenRouterProvider()

        prompt = target.format(
            event=payload.event.value,
            tool=payload.tool or "",
            arguments=json.dumps(payload.arguments),
        )
        messages = [
            {"role": "system", "content":
                "You are an advisory policy classifier. Reply with a single word: "
                "ALLOW or NOTIFY. Use NOTIFY only to raise a soft concern. "
                "You cannot block."},
            {"role": "user", "content": prompt},
        ]
        try:
            text = (provider.complete(messages).text or "").strip().upper()
        except Exception as exc:  # noqa: BLE001 - advisory only: never block on error
            return HookResult(action=HookAction.ALLOW, message=f"llm hook skipped: {exc}")

        if text.startswith("NOTIFY"):
            return HookResult(action=HookAction.NOTIFY, message=text)
        # Anything else (incl. an attempted BLOCK/MODIFY) is downgraded to ALLOW.
        return HookResult(action=HookAction.ALLOW)


RUNNERS: dict[RunnerType, HookRunner] = {
    RunnerType.BASH: BashRunner(),
    RunnerType.PYTHON: PythonRunner(),
    RunnerType.HTTP: HttpRunner(),
    RunnerType.LLM: LlmRunner(),
}
