"""MCP — client + transport (Streamable HTTP / SSE) with fallback.

Mirrors EliteClaw's custom MCP clients (no SDK lock-in). Servers are declared
in `.agent/mcp-servers.yaml`. The wire protocol is JSON-RPC 2.0, exactly as in
the Model Context Protocol spec:

  initialize           -> handshake (protocolVersion, capabilities)
  notifications/initialized (notification, no id)
  tools/list           -> [{name, description, inputSchema}, ...]
  tools/call           -> {content: [...], isError?: bool}

Transport here is HTTP POST (Streamable HTTP); on a connect/transport failure
we retry once with `config.fallback`. MCP tools are returned as `McpTool` and
can be adapted into the local ToolRegistry shape so the agent loop dispatches
them through the SAME hook + permission chokepoints as native tools
(principle #1).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from pyclaw.config import SETTINGS

MCP_PROTOCOL_VERSION = "2024-11-05"


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
    timeout: float = 30.0


@dataclass
class McpTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class McpError(RuntimeError):
    """A JSON-RPC error or transport failure from an MCP server."""


@dataclass
class McpClient:
    config: McpServerConfig
    # Injectable HTTP poster (target, json_body, headers, timeout) -> dict.
    # Defaults to httpx; tests substitute a fake so no network is needed.
    poster: Any = None
    _connected: bool = False
    _id_counter: Any = field(default_factory=lambda: itertools.count(1))

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> None:
        """Handshake with the server; on failure, retry with `config.fallback`.

        Sends `initialize`, then the `notifications/initialized` notification.
        Fails loudly (principle #6) if both primary and fallback transports
        fail.
        """
        try:
            self._do_initialize(self.config.transport)
        except Exception as primary_exc:  # noqa: BLE001
            if self.config.fallback is None:
                raise McpError(
                    f"MCP connect to {self.config.name!r} failed: {primary_exc}"
                ) from primary_exc
            try:
                self._do_initialize(self.config.fallback)
            except Exception as fallback_exc:  # noqa: BLE001
                raise McpError(
                    f"MCP connect to {self.config.name!r} failed on both "
                    f"{self.config.transport.value} ({primary_exc}) and "
                    f"{self.config.fallback.value} ({fallback_exc})"
                ) from fallback_exc
        self._connected = True

    def _do_initialize(self, transport: Transport) -> None:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "pyclaw", "version": "0.1.0"},
            },
            transport=transport,
        )
        if not isinstance(result, dict):
            raise McpError(f"initialize returned unexpected payload: {result!r}")
        # Best-effort initialized notification (no response expected).
        self._notify("notifications/initialized", transport=transport)

    # -- MCP methods ----------------------------------------------------------
    def list_tools(self) -> list[McpTool]:
        """MCP `tools/list` -> [McpTool]."""
        self._require_connected()
        result = self._rpc("tools/list", {})
        tools_raw = (result or {}).get("tools", []) if isinstance(result, dict) else []
        out: list[McpTool] = []
        for t in tools_raw:
            out.append(
                McpTool(
                    server=self.config.name,
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", t.get("input_schema", {})) or {},
                )
            )
        return out

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """MCP `tools/call` -> result (content blocks, or raises on isError)."""
        self._require_connected()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict) and result.get("isError"):
            raise McpError(f"MCP tool {name!r} reported an error: {result.get('content')!r}")
        return result

    # -- transport ------------------------------------------------------------
    def _require_connected(self) -> None:
        if not self._connected:
            raise McpError(
                f"MCP client for {self.config.name!r} is not connected "
                "(call connect() first — fail loudly, principle #6)"
            )

    def _rpc(self, method: str, params: dict[str, Any], *, transport: Transport | None = None) -> Any:
        """Send a JSON-RPC request and return its `result` (raises on error)."""
        request = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": method,
            "params": params,
        }
        data = self._post(request, transport or self.config.transport)
        if isinstance(data, dict) and "error" in data and data["error"] is not None:
            err = data["error"]
            raise McpError(
                f"{method} JSON-RPC error {err.get('code')}: {err.get('message')}"
            )
        return data.get("result") if isinstance(data, dict) else data

    def _notify(self, method: str, *, transport: Transport | None = None) -> None:
        """Fire-and-forget JSON-RPC notification (no id, response ignored)."""
        request = {"jsonrpc": "2.0", "method": method, "params": {}}
        try:
            self._post(request, transport or self.config.transport)
        except Exception:  # noqa: BLE001 - a lost notification must not break connect
            pass

    def _post(self, request: dict[str, Any], transport: Transport) -> dict[str, Any]:
        """POST a JSON-RPC envelope; parse JSON or SSE-framed JSON back to a dict."""
        if self.poster is not None:
            return self.poster(self.config.url, request, dict(self.config.headers), transport)

        headers = {"Content-Type": "application/json", **self.config.headers}
        # Streamable HTTP and SSE both accept event-stream responses.
        headers.setdefault("Accept", "application/json, text/event-stream")
        resp = httpx.post(
            self.config.url, json=request, headers=headers, timeout=self.config.timeout
        )
        if resp.status_code >= 400:
            raise McpError(f"MCP HTTP {resp.status_code}: {resp.text[:300]}")

        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            return _parse_sse(resp.text)
        return resp.json()


def _parse_sse(body: str) -> dict[str, Any]:
    """Extract the first JSON object from an SSE stream's `data:` lines."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload and payload != "[DONE]":
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
    raise McpError("no JSON data frame found in SSE response")


def load_server_configs(path: Path | None = None) -> list[McpServerConfig]:
    """Parse `.agent/mcp-servers.yaml` into a list of McpServerConfig.

    Shape::

        servers:
          - name: docs
            url: https://example.com/mcp
            transport: streamable_http   # or sse
            headers: {Authorization: "Bearer ..."}
            fallback: sse

    A missing file yields an empty list (MCP is optional). Bad entries fail
    loudly (principle #6).
    """
    import yaml

    target = path or SETTINGS.mcp_servers_path
    if not target.is_file():
        return []

    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    servers = raw.get("servers", raw) if isinstance(raw, dict) else raw
    if not isinstance(servers, list):
        raise ValueError(f"{target}: expected a list of servers")

    configs: list[McpServerConfig] = []
    for entry in servers:
        if not isinstance(entry, dict) or "name" not in entry or "url" not in entry:
            raise ValueError(f"{target}: each server needs at least 'name' and 'url'")
        fallback = entry.get("fallback")
        configs.append(
            McpServerConfig(
                name=str(entry["name"]),
                url=str(entry["url"]),
                transport=Transport(entry.get("transport", "streamable_http")),
                headers=dict(entry.get("headers", {}) or {}),
                fallback=Transport(fallback) if fallback else None,
                timeout=float(entry.get("timeout", 30.0)),
            )
        )
    return configs
