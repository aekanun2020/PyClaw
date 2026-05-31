"""MCP — Model Context Protocol clients.

EliteClaw's strongest layer (🟢): custom SSE + Streamable HTTP clients with a
transport factory + fallback. PyClaw keeps the same design, configured via
`.agent/mcp-servers.yaml`, and exposes discovered MCP tools to the core loop
(where they pass through the Hook engine like any other tool).
"""

from pyclaw.mcp.client import (  # noqa: F401
    McpClient,
    McpServerConfig,
    Transport,
    detect_transport,
    load_server_configs,
    load_server_configs_from_dotenv,
    load_server_configs_from_env,
    parse_transport,
)
