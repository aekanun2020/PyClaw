"""Core — local tool registry + dispatch.

A `Tool` is any callable that takes a dict of arguments and returns a result.
The registry holds local tools; MCP tools (pyclaw/mcp) can be adapted into the
same shape and registered here, so the agent loop dispatches everything
uniformly — and therefore everything passes through the Hook engine + the
permission layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ToolFn = Callable[[dict[str, Any]], Any]


@dataclass
class Tool:
    name: str
    description: str
    fn: ToolFn
    # JSON-schema-ish description of arguments (passed to the LLM as a tool spec).
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_llm_spec(self) -> dict[str, Any]:
        """Render the OpenAI/OpenRouter `tools` entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def llm_specs(self) -> list[dict[str, Any]]:
        return [t.to_llm_spec() for t in self._tools.values()]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name. Unknown tool -> fail loudly (principle #6)."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown tool {name!r}")
        return tool.fn(arguments)
