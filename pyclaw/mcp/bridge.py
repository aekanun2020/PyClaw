"""Bridge MCP servers into the agent's ToolRegistry.

This is the glue that makes MCP a first-class tool source for `pyclaw run`:
read the server configs (EliteClaw `.env` *or* PyClaw `.agent/mcp-servers.yaml`),
connect to each server, list its tools, and register every tool into the loop's
ToolRegistry. Once registered, MCP tools pass through the Hook engine, the
permission policy, and the audit log exactly like any built-in tool — so the
"Prompt != Policy" guarantee covers MCP too.

Tool names are namespaced with the server's `tool_prefix` (e.g. ``db_query``)
so tools from different servers never collide.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyclaw.core.tools import Tool, ToolRegistry
from pyclaw.mcp.client import (
    McpClient,
    McpServerConfig,
    load_server_configs,
    load_server_configs_from_dotenv,
    load_server_configs_from_env,
)


@dataclass
class MountedServer:
    config: McpServerConfig
    client: McpClient
    tool_names: list[str]


def discover_configs(
    *,
    dotenv_path: Path | None = None,
    yaml_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[McpServerConfig]:
    """Find MCP server configs from every supported source, merged by name.

    Order of precedence (later wins on a name clash):
      1. PyClaw YAML  (`.agent/mcp-servers.yaml`)
      2. EliteClaw env vars (current process env, or the supplied ``env``)
      3. An explicit EliteClaw ``.env`` file, if ``dotenv_path`` is given

    Any source may be empty; MCP is optional. Returns a de-duplicated list.
    """
    merged: dict[str, McpServerConfig] = {}
    for cfg in load_server_configs(yaml_path):
        merged[cfg.name] = cfg
    for cfg in load_server_configs_from_env(env if env is not None else dict(os.environ)):
        merged[cfg.name] = cfg
    if dotenv_path is not None:
        for cfg in load_server_configs_from_dotenv(dotenv_path):
            merged[cfg.name] = cfg
    return list(merged.values())


def _prefixed(config: McpServerConfig, tool_name: str) -> str:
    return f"{config.tool_prefix}{tool_name}" if config.tool_prefix else tool_name


def mount_mcp_tools(
    registry: ToolRegistry,
    configs: list[McpServerConfig],
    *,
    client_factory: Any = None,
    strict: bool = False,
    on_warn: Any = None,
) -> list[MountedServer]:
    """Connect to each MCP server and register its tools into ``registry``.

    ``client_factory(config) -> McpClient`` is injectable for tests; it defaults
    to the real ``McpClient``.

    Resilience: by default a server that fails to connect is **skipped with a
    warning** so one dead server can't take down the whole agent — important
    when MCP is your primary surface and you run several servers. Pass
    ``strict=True`` (CLI: ``PYCLAW_MCP_STRICT=1``) to fail loudly instead
    (principle #6). ``on_warn(message)`` receives skip warnings.
    """
    factory = client_factory or (lambda cfg: McpClient(cfg))
    warn = on_warn or (lambda _msg: None)
    mounted: list[MountedServer] = []

    for config in configs:
        client = factory(config)
        try:
            client.connect()
            tools = client.list_tools()
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise
            warn(f"skipping MCP server {config.name!r}: {exc}")
            continue
        names: list[str] = []
        for mcp_tool in tools:
            local_name = _prefixed(config, mcp_tool.name)
            remote_name = mcp_tool.name

            def _call(arguments: dict[str, Any], _client=client, _remote=remote_name) -> Any:
                return _client.call_tool(_remote, arguments)

            registry.register(
                Tool(
                    name=local_name,
                    description=f"[mcp:{config.name}] {mcp_tool.description}",
                    fn=_call,
                    parameters=mcp_tool.input_schema or {"type": "object", "properties": {}},
                )
            )
            names.append(local_name)
        mounted.append(MountedServer(config=config, client=client, tool_names=names))

    return mounted
