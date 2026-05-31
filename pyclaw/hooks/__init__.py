"""Layer 3 — Hook engine ★ (the deterministic core of PyClaw).

This is the layer EliteClaw was completely missing (🔴 -> 🟢) and the reason
PyClaw exists. It implements design principle #1: **Prompt != Policy**.

Any rule that must hold *every time* — permission checks, PDPA guards, audit
logging, approval routing, secret redaction — is registered as a Hook and runs
in deterministic code. The LLM cannot skip it: the core loop fires the relevant
hook around every tool call and lifecycle moment.

Public surface:
  events.py  : the 8 hook events + payload/result types
  engine.py  : HookEngine (register, fire, resolve allow/modify/block/notify)
  runners.py : how a hook is executed (bash / python / HTTP / LLM)
"""

from pyclaw.hooks.events import HookEvent, HookAction, HookResult, HookPayload  # noqa: F401
from pyclaw.hooks.engine import HookEngine, HookSpec  # noqa: F401
