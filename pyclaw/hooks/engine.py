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
from pyclaw.hooks.runners import RUNNERS, HookRunner, RunnerType


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
    # Required events: if declared and no hook is registered, fire() raises
    # (principle #6 — fail loudly on a missing required layer).
    required_events: frozenset[HookEvent] = field(default_factory=frozenset)
    # Runner table is injectable so tests can substitute fakes.
    runners: dict[RunnerType, HookRunner] = field(default_factory=lambda: dict(RUNNERS))

    def register(self, spec: HookSpec) -> None:
        self._hooks.append(spec)

    def hooks_for(self, event: HookEvent) -> list[HookSpec]:
        """Enabled hooks for `event`, ordered by priority (ascending).

        Python's sort is stable, so hooks with equal priority keep their
        registration order — making resolution fully deterministic.
        """
        matches = [h for h in self._hooks if h.event == event and h.enabled]
        return sorted(matches, key=lambda h: h.priority)

    def fire(self, payload: HookPayload) -> HookResult:
        """Run every hook for `payload.event` and resolve one HookResult.

        Resolution rules (deterministic):
          1. Hooks run in priority order (stable within equal priority).
          2. The first BLOCK short-circuits and returns immediately (fail-closed).
          3. MODIFY chains: a hook's `modified_payload` becomes the payload fed
             to all later hooks, and the engine remembers it for the result.
          4. NOTIFY messages accumulate (joined into the final result).
          5. No matching hooks -> ALLOW (or raise if the event is required and
             `fail_loudly`).
        """
        hooks = self.hooks_for(payload.event)

        if not hooks:
            if self.fail_loudly and payload.event in self.required_events:
                raise RuntimeError(
                    f"No hook registered for required event {payload.event.value} "
                    "(fail loudly, principle #6)"
                )
            return HookResult(action=HookAction.ALLOW)

        current = payload
        modified = False
        notices: list[str] = []

        for spec in hooks:
            try:
                runner = self.runners[spec.runner]
            except KeyError as exc:  # misconfigured hook -> fail loudly
                raise RuntimeError(
                    f"No runner for type {spec.runner!r} (hook {spec.name!r})"
                ) from exc

            result = runner.run(spec.target, current)

            if result.action is HookAction.BLOCK:
                # Fail-closed: stop now, no later hook can un-block.
                return HookResult(
                    action=HookAction.BLOCK,
                    message=result.message,
                    source_hook=result.source_hook or spec.name,
                )

            if result.action is HookAction.MODIFY:
                if result.modified_payload is None:
                    raise RuntimeError(
                        f"Hook {spec.name!r} returned MODIFY without a modified_payload"
                    )
                current = result.modified_payload
                modified = True

            if result.action is HookAction.NOTIFY and result.message:
                notices.append(result.message)

        joined = "; ".join(notices) if notices else None
        if modified:
            return HookResult(
                action=HookAction.MODIFY,
                modified_payload=current,
                message=joined,
            )
        if notices:
            return HookResult(action=HookAction.NOTIFY, message=joined)
        return HookResult(action=HookAction.ALLOW)
