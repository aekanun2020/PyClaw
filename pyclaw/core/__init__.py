"""Core — the agent loop that ties all layers together.

  llm.py  : OpenRouter provider (same backend as EliteClaw)
  loop.py : AgentLoop — runs rounds; fires Hooks (Layer 3) around EVERY tool
            call, checks permissions (Layer 5), HITL (Layer 0), and writes the
            audit log (Layer 0).

The loop is where principle #1 (Prompt != Policy) becomes concrete: tools are
NEVER called directly from the model's output — they always pass through the
HookEngine first.
"""

from pyclaw.core.loop import AgentLoop  # noqa: F401
from pyclaw.core.llm import OpenRouterProvider, LLMResponse  # noqa: F401
from pyclaw.core.tools import Tool, ToolRegistry  # noqa: F401
