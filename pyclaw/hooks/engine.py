"""Layer 3 — HookEngine: register hooks and fire them deterministically.

The engine is the single chokepoint the core loop calls at each lifecycle
moment. Resolution rules (deterministic, order-stable):

  1. Hooks for an event run in registration order (stable sort by priority).
  2. If ANY hook returns BLOCK, the engine returns BLOCK immediately
     (fail-closed) — no later hook can un-block it.
  3. MODIFY results chain: each MODIFY's `modified_payload` feeds the next hook.
  4. NOTIFY messages accumulate and are returned alongside the final action.
  5. If no hooks match, the engine returns ALLOW (unless `fail_loudly` and the
     event is declared required — principle #6).

Because this runs in code on every call, an LLM cannot bypass a registered
policy (principle #1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyclaw.hooks.events import HookAction, HookEvent, HookPayload, HookResult
from pyclaw.hooks.runners import RUNNERS, RunnerType


@dataclass
class HookSpec:
    """One registered hook (typically parsed from a plugin's hooks config)."""

    name: str
    event: HookEvent
    runner: RunnerType
    target: str                 # command / 'module:func' / url / prompt
    priority: int = 100         # lower runs first
    enabled: bool = True


@dataclass
class HookEngine:
    _hooks: list[HookSpec] = field(default_factory=list)
    fail_loudly: bool = True

    def register(self, spec: HookSpec) -> None:
        self._hooks.append(spec)

    def hooks_for(self, event: HookEvent) -> list[HookSpec]:
        matches = [h for h in self._hooks if h.event == event and h.enabled]
        return sorted(matches, key=lambda h: h.priority)

    def fire(self, payload: HookPayload) -> HookResult:
        """Run all hooks for `payload.event` and resolve a single HookResult.

        TODO (implement resolution rules above):
          - iterate self.hooks_for(payload.event)
          - run each via RUNNERS[spec.runner].run(spec.target, payload)
          - BLOCK short-circuits (return immediately, fail-closed)
          - MODIFY updates the running payload for subsequent hooks
          - NOTIFY messages accumulate
          - return final HookResult (ALLOW/MODIFY/NOTIFY) or BLOCK
        """
        del RUNNERS  # referenced by the implementation; silence unused for now
        raise NotImplementedError("HookEngine.fire: resolution loop (scaffold)")
