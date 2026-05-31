"""MCP — client + transport factory (SSE / Streamable HTTP) with fallback.

Mirrors EliteClaw's custom MCP clients (no SDK lock-in). Servers are declared
in `.agent/mcp-servers.yaml`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pyclaw.config import SETTINGS


class Transport(str, Enum):
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


@dataclass
class McpServerConfig:
    name: str
    url: str
    transport: Transport = Transport.STREAMABLE_HTTP
    headers: dict[str, str] = field(default_factory=dict)
    fallback: Transport | None = Transport.SSE  # try this if primary fails


@dataclass
class McpTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpClient:
    config: McpServerConfig

    def connect(self) -> None:
        """Open the primary transport; on failure, try `config.fallback`.

        TODO:
          - build transport per config.transport (httpx streaming / SSE)
          - on connect error and fallback set -> retry with fallback
          - fail loudly if both fail (principle #6)
        """
        raise NotImplementedError("McpClient.connect: transport factory (scaffold)")

    def list_tools(self) -> list[McpTool]:
        """MCP `tools/list`. TODO: JSON-RPC call -> [McpTool]."""
        raise NotImplementedError("McpClient.list_tools (scaffold)")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """MCP `tools/call`. TODO: JSON-RPC call -> result."""
        raise NotImplementedError("McpClient.call_tool (scaffold)")


def load_server_configs(path: Path | None = None) -> list[McpServerConfig]:
    """Parse `.agent/mcp-servers.yaml` into McpServerConfig list.

    TODO: yaml.safe_load(path or SETTINGS.mcp_servers_path) -> [McpServerConfig]
    """
    del SETTINGS  # used by the implementation
    raise NotImplementedError("load_server_configs (scaffold)")
