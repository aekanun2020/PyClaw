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

from enum import Enum
from typing import Protocol

from pyclaw.hooks.events import HookPayload, HookResult


class RunnerType(str, Enum):
    BASH = "bash"
    PYTHON = "python"
    HTTP = "http"
    LLM = "llm"


class HookRunner(Protocol):
    def run(self, target: str, payload: HookPayload) -> HookResult: ...


class BashRunner:
    """`target` is a shell command. Payload piped as JSON on stdin; stdout JSON parsed."""

    def run(self, target: str, payload: HookPayload) -> HookResult:
        # TODO: subprocess.run(target, input=json.dumps(payload), shell=True, timeout=...)
        # TODO: parse stdout JSON -> HookResult; non-zero exit -> BLOCK
        raise NotImplementedError("BashRunner.run (scaffold)")


class PythonRunner:
    """`target` is 'module:function'. Function signature: (HookPayload) -> HookResult."""

    def run(self, target: str, payload: HookPayload) -> HookResult:
        # TODO: importlib import module, getattr function, call with payload
        raise NotImplementedError("PythonRunner.run (scaffold)")


class HttpRunner:
    """`target` is a URL. POST payload JSON; response JSON parsed -> HookResult."""

    def run(self, target: str, payload: HookPayload) -> HookResult:
        # TODO: httpx.post(target, json=payload, timeout=...) -> HookResult
        raise NotImplementedError("HttpRunner.run (scaffold)")


class LlmRunner:
    """`target` is a prompt template. ADVISORY ONLY — never a hard constraint."""

    def run(self, target: str, payload: HookPayload) -> HookResult:
        # TODO: call LLM provider; map classification -> HookResult (ALLOW/NOTIFY only)
        raise NotImplementedError("LlmRunner.run (scaffold)")


RUNNERS: dict[RunnerType, HookRunner] = {
    RunnerType.BASH: BashRunner(),
    RunnerType.PYTHON: PythonRunner(),
    RunnerType.HTTP: HttpRunner(),
    RunnerType.LLM: LlmRunner(),
}
