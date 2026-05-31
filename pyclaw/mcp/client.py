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
    # EliteClaw namespaces each server's tools (e.g. "db_", "rag_") so that
    # tools from different servers never collide. Empty string = no prefix.
    tool_prefix: str = ""


def detect_transport(url: str) -> Transport:
    """EliteClaw rule: a URL whose path ends with `/mcp` is Streamable HTTP,
    everything else defaults to SSE (backward compatible)."""
    from urllib.parse import urlparse

    path = urlparse(url).path.rstrip("/")
    if path.endswith("/mcp"):
        return Transport.STREAMABLE_HTTP
    return Transport.SSE


def parse_transport(value: str | None) -> Transport | None:
    """Accept both EliteClaw spellings and PyClaw's enum values.

    `sse` -> SSE; `streamable-http`/`streamable_http`/`http` -> STREAMABLE_HTTP.
    Returns None for empty/unknown so callers can fall back to auto-detect.
    """
    if not value:
        return None
    v = value.strip().lower().replace("-", "_")
    if v == "sse":
        return Transport.SSE
    if v in ("streamable_http", "http"):
        return Transport.STREAMABLE_HTTP
    return None


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
                tool_prefix=str(entry.get("tool_prefix", entry.get("toolPrefix", "")) or ""),
            )
        )
    return configs


def load_server_configs_from_env(
    env: dict[str, str] | None = None,
) -> list[McpServerConfig]:
    """Parse EliteClaw-style MCP config straight from environment variables.

    This reads the exact same `.env` shape EliteClaw uses, so an existing
    EliteClaw deployment can point PyClaw at its current `.env` with no edits::

        MCP_SERVER_1_URL=http://10.211.55.2:9000
        MCP_SERVER_1_NAME=mssql
        MCP_SERVER_1_PREFIX=db_
        MCP_SERVER_1_TRANSPORT=sse        # optional; auto-detected from URL
        MCP_SERVER_1_TIMEOUT=60000        # optional; milliseconds (EliteClaw)
        MCP_SERVER_1_HOST=example.com     # optional; sent as the Host header
        ...
        # legacy single-server form (also supported):
        MCP_SERVER_URL=https://your-server
        MCP_SERVER_NAME=mssql
        MCP_TOOL_PREFIX=db_

    Transport rules match EliteClaw: an explicit `*_TRANSPORT` wins, otherwise
    a URL path ending in `/mcp` is Streamable HTTP and everything else is SSE.
    Timeouts are read in EliteClaw's milliseconds (falling back to
    `REQUEST_TIMEOUT`) and converted to PyClaw's seconds. No servers found
    yields an empty list (MCP is optional); a malformed timeout fails loudly
    (principle #6).
    """
    import os

    e = env if env is not None else dict(os.environ)

    def _timeout_seconds(raw: str | None, default_ms: float) -> float:
        if raw is None or raw.strip() == "":
            return default_ms / 1000.0
        try:
            return float(raw) / 1000.0
        except ValueError as exc:
            raise ValueError(f"invalid MCP timeout {raw!r} (expected milliseconds)") from exc

    default_ms = _timeout_ms(e.get("REQUEST_TIMEOUT"))
    configs: list[McpServerConfig] = []

    # Numbered form: MCP_SERVER_1_*, MCP_SERVER_2_*, ... (stop at first gap,
    # mirroring EliteClaw which iterates a fixed range and skips blanks).
    i = 1
    misses = 0
    while misses < 3:  # tolerate a couple of gaps, like EliteClaw's loop
        url = (e.get(f"MCP_SERVER_{i}_URL") or "").strip()
        if not url:
            i += 1
            misses += 1
            continue
        misses = 0
        transport = parse_transport(e.get(f"MCP_SERVER_{i}_TRANSPORT")) or detect_transport(url)
        host = (e.get(f"MCP_SERVER_{i}_HOST") or "").strip()
        configs.append(
            McpServerConfig(
                name=(e.get(f"MCP_SERVER_{i}_NAME") or f"server-{i}").strip(),
                url=url,
                transport=transport,
                headers={"Host": host} if host else {},
                fallback=_other_transport(transport),
                timeout=_timeout_seconds(e.get(f"MCP_SERVER_{i}_TIMEOUT"), default_ms),
                tool_prefix=(e.get(f"MCP_SERVER_{i}_PREFIX") or "").strip(),
            )
        )
        i += 1

    # Legacy single-server form.
    legacy_url = (e.get("MCP_SERVER_URL") or "").strip()
    if legacy_url:
        transport = parse_transport(e.get("MCP_SERVER_TRANSPORT")) or detect_transport(legacy_url)
        configs.append(
            McpServerConfig(
                name=(e.get("MCP_SERVER_NAME") or "mcp-server").strip(),
                url=legacy_url,
                transport=transport,
                fallback=_other_transport(transport),
                timeout=_timeout_seconds(e.get("MCP_SERVER_TIMEOUT"), default_ms),
                tool_prefix=(e.get("MCP_TOOL_PREFIX") or "").strip(),
            )
        )
    return configs


def load_server_configs_from_dotenv(path: Path) -> list[McpServerConfig]:
    """Read an EliteClaw `.env` FILE and parse its MCP servers.

    Point this at an existing EliteClaw deployment's `.env` to bring its MCP
    servers into PyClaw unchanged. Only `KEY=VALUE` lines are read; comments
    (`#`) and blanks are ignored, surrounding quotes are stripped, and a
    leading `export ` is tolerated. A missing file yields an empty list.
    """
    if not path.is_file():
        return []
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if key:
            env[key] = val
    return load_server_configs_from_env(env)


def _timeout_ms(raw: str | None) -> float:
    """EliteClaw's default REQUEST_TIMEOUT is 30000 ms."""
    if raw is None or raw.strip() == "":
        return 30000.0
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"invalid REQUEST_TIMEOUT {raw!r} (expected milliseconds)") from exc


def _other_transport(t: Transport) -> Transport:
    """The complementary transport to use as fallback."""
    return Transport.SSE if t is Transport.STREAMABLE_HTTP else Transport.STREAMABLE_HTTP
