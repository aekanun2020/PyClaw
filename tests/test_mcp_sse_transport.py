"""End-to-end test of the classic MCP SSE transport (GET stream + /messages POST).

Spins up a tiny in-process SSE MCP server (stdlib http.server) that mirrors the
HTTP+SSE servers EliteClaw/OpenClaw connect to: the root only allows GET, the
real endpoint is /sse, and replies come back over the open stream. No mocks of
McpClient itself — this exercises the real transport.
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from pyclaw.mcp.client import McpClient, McpServerConfig, Transport

_SESSIONS: dict[str, queue.Queue] = {}
_TOOLS = [{"name": "list_tables", "description": "list", "inputSchema": {"type": "object", "properties": {}}}]


def _reply(req):
    mid, method = req.get("id"), req.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": _TOOLS}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "data": "SSE_CANARY"}]}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if urlparse(self.path).path != "/sse":
            self.send_response(405); self.send_header("Allow", "GET"); self.end_headers()
            self.wfile.write(b'{"detail":"Method Not Allowed"}'); return
        sid = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        _SESSIONS[sid] = q
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: endpoint\n")
        self.wfile.write(f"data: /messages?session_id={sid}\n\n".encode())
        self.wfile.flush()
        while True:
            msg = q.get()
            if msg is None:
                break
            self.wfile.write(b"event: message\n")
            self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
            self.wfile.flush()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/messages":
            self.send_response(404); self.end_headers(); return
        sid = parse_qs(u.query).get("session_id", [""])[0]
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        self.send_response(202); self.end_headers()
        q = _SESSIONS.get(sid)
        if q is not None and req.get("id") is not None:
            q.put(_reply(req))


@pytest.fixture()
def sse_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/sse"
    finally:
        srv.shutdown()


def test_classic_sse_connect_list_and_call(sse_server):
    cfg = McpServerConfig(
        name="mssql", url=sse_server,
        transport=Transport.SSE, fallback=Transport.STREAMABLE_HTTP,
        timeout=10.0, tool_prefix="db_",
    )
    client = McpClient(cfg)
    client.connect()
    assert client._connected is True
    assert "session_id=" in client._sse.messages_url

    tools = client.list_tools()
    assert [t.name for t in tools] == ["list_tables"]

    res = client.call_tool("list_tables", {})
    assert res["content"][0]["data"] == "SSE_CANARY"

    client.close()
    assert client._sse is None
