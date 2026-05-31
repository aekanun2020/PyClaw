"""PyClaw CLI entrypoint.

Usage:
    pyclaw run "your task here"
    pyclaw doctor

`run` assembles every layer from `.agent/` + environment and executes one task
through the AgentLoop. `doctor` validates the wiring and reports any missing or
broken layer (principle #6 — fail loudly: a missing required layer is a hard
error, not a silent default).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import os

from pyclaw.config import SETTINGS


def _api_key() -> str:
    """Read the OpenRouter key live from the environment.

    SETTINGS is frozen at import time; reading the env here means `doctor`/`run`
    reflect the current shell (and are trivially testable via monkeypatch).
    """
    return os.getenv("OPENROUTER_API_KEY", "") or SETTINGS.openrouter_api_key


def _dotenv_path() -> Path | None:
    """Locate an EliteClaw-style `.env` to read MCP servers from.

    `PYCLAW_DOTENV` overrides; otherwise a `.env` in the current directory is
    used if present. Returns None when there is nothing to read.
    """
    override = os.getenv("PYCLAW_DOTENV")
    if override:
        return Path(override)
    local = Path.cwd() / ".env"
    return local if local.is_file() else None


def _mount_mcp(registry) -> list:
    """Discover + connect MCP servers and register their tools into `registry`.

    Reads both PyClaw YAML and EliteClaw `.env`. Returns the mounted servers
    (empty list when no servers are configured — MCP is optional).
    """
    from pyclaw.mcp.bridge import discover_configs, mount_mcp_tools

    configs = discover_configs(dotenv_path=_dotenv_path())
    if not configs:
        return []
    strict = os.getenv("PYCLAW_MCP_STRICT", "").lower() in {"1", "true", "yes"}
    return mount_mcp_tools(
        registry,
        configs,
        strict=strict,
        on_warn=lambda msg: sys.stderr.write(f"[mcp] WARN {msg}\n"),
    )


# -- shared assembly ----------------------------------------------------------
def _build_loop(*, with_memory: bool = True, with_skills: bool = True, with_mcp: bool = True):
    """Assemble a fully-wired AgentLoop from config/.agent. Returns the loop.

    Wiring (all 6 layers):
      L0 runtime : ContextManager + AuditLog + HITLGate
      L1 memory  : MemoryLoader rooted at cwd (global->local walk)
      L2 skills  : SkillRegistry scanned from .agent/skills
      L3 hooks   : HookEngine + default + plugin-contributed hooks
      L4 subagents: available via SubagentRunner (wired by callers as needed)
      L5 plugins : PluginLoader -> merged PermissionPolicy
      MCP        : connect to configured servers, register their tools
    """
    from pyclaw.core.llm import OpenRouterProvider
    from pyclaw.core.loop import AgentLoop
    from pyclaw.core.tools import ToolRegistry
    from pyclaw.hooks import HookEngine
    from pyclaw.memory import MemoryLoader
    from pyclaw.plugins.loader import PluginLoader
    from pyclaw.plugins.permissions import PermissionPolicy
    from pyclaw.runtime.audit import AuditLog
    from pyclaw.runtime.context import ContextManager
    from pyclaw.runtime.hitl import HITLGate
    from pyclaw.skills.loader import SkillLoader
    from pyclaw.skills.registry import SkillRegistry

    hooks = HookEngine()
    skills_registry = SkillRegistry()

    # L5 — plugins contribute hooks/skills and a merged permission policy.
    plugins_root = SETTINGS.agent_dir / "plugins"
    permissions = PermissionPolicy()
    loader = PluginLoader(plugins_root=plugins_root, installed_versions={"core": "0.1.0"})
    permissions = loader.load_all(hooks=hooks, skills=skills_registry)

    # L2 — also scan a top-level .agent/skills dir if present.
    skills_dir = SETTINGS.agent_dir / "skills"
    if skills_dir.is_dir():
        skills_registry.scan(skills_dir)

    # A simple CLI prompt for HITL approval (fail-closed: empty -> deny).
    def _cli_prompt(req) -> bool:
        sys.stderr.write(f"[approval] run {req.tool} with {req.arguments}? [y/N] ")
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}

    tools = ToolRegistry()
    # MCP — register every configured server's tools (your primary surface).
    mounted = _mount_mcp(tools) if with_mcp else []

    loop = AgentLoop(
        llm=OpenRouterProvider(),
        hooks=hooks,
        context=ContextManager(),
        audit=AuditLog(),
        hitl=HITLGate(prompt_fn=_cli_prompt),
        permissions=permissions,
        tools=tools,
        memory=MemoryLoader(Path.cwd()) if with_memory else None,
        skills=SkillLoader(skills_registry) if with_skills else None,
    )
    loop._mcp_mounted = mounted  # introspection for the CLI / doctor
    return loop


# -- commands -----------------------------------------------------------------
def _cmd_run(task: str) -> int:
    if not _api_key():
        sys.stderr.write(
            "error: OPENROUTER_API_KEY not set (fail loudly, principle #6)\n"
            "        export OPENROUTER_API_KEY=... and retry.\n"
        )
        return 2
    loop = _build_loop()
    mounted = getattr(loop, "_mcp_mounted", [])
    for m in mounted:
        sys.stderr.write(
            f"[mcp] {m.config.name}: {len(m.tool_names)} tool(s) — {', '.join(m.tool_names) or '(none)'}\n"
        )
    answer = loop.run(task)
    sys.stdout.write(answer.rstrip() + "\n")
    return 0


def _make_tool_tracer(write=None):
    """Return an on_tool(phase, name, info) callback that prints a live trace.

    `--trace` shows the full "tool execution" stage: the tool name, the
    arguments sent, and the result returned (plus elapsed time). It is OFF by
    default because results can contain PII (PDPA); the audit log only ever
    stores hashes, so this verbose view is opt-in.
    """
    import json as _json

    out = write or (lambda s: sys.stderr.write(s))

    def _short(value: object, limit: int = 2000) -> str:
        try:
            text = value if isinstance(value, str) else _json.dumps(
                value, ensure_ascii=False, default=str
            )
        except (TypeError, ValueError):
            text = str(value)
        return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit} chars)"

    def on_tool(phase: str, name: str, info: dict) -> None:
        if phase == "call":
            out(f"  → call  {name}({_short(info.get('arguments', {}))})\n")
        elif phase == "return":
            secs = info.get("seconds", 0.0)
            out(f"  ← return {name}  [{secs:.2f}s]  {_short(info.get('result'))}\n")

    return on_tool


def _cmd_chat(resume: str | None = None, no_stream: bool = False,
              trace: bool = False) -> int:
    """Interactive multi-turn chat (EliteClaw-style), completing the agentic loop.

    The OpenClaw definition of an agentic loop is:
        intake -> context assembly -> model inference -> tool execution
        -> streaming replies -> persistence

    This command realises all of it:
      - builds the loop + mounts MCP exactly ONCE, reusing the same context
        across turns (history / Short-term Memory; also avoids reconnecting MCP);
      - streams assistant text token-by-token as it is generated (unless
        --no-stream);
      - persists the conversation to `.agent/sessions/<id>.json` after every
        turn, so a chat can be resumed later with `--resume <id>` (Episodic
        Memory across processes).

    Exit with `quit`/`exit`, or Ctrl-D / Ctrl-C.
    """
    if not _api_key():
        sys.stderr.write(
            "error: OPENROUTER_API_KEY not set (fail loudly, principle #6)\n"
            "        export OPENROUTER_API_KEY=... and retry.\n"
        )
        return 2

    from pyclaw.runtime.session import SessionStore

    # Build once: this is where MCP servers are dialed and tools registered.
    loop = _build_loop()
    mounted = getattr(loop, "_mcp_mounted", [])
    for m in mounted:
        sys.stderr.write(
            f"[mcp] {m.config.name}: {len(m.tool_names)} tool(s) — {', '.join(m.tool_names) or '(none)'}\n"
        )

    store = SessionStore()
    if resume:
        try:
            meta = store.load_into(resume, loop.context)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2
        session_id = meta["id"]
        turns = sum(1 for m in loop.context.messages if m.role.value == "user")
        sys.stderr.write(f"[session] resumed {session_id} ({turns} prior turn(s))\n")
    else:
        session_id = store.create()
        sys.stderr.write(f"[session] new {session_id}\n")

    stream = not no_stream
    tracer = _make_tool_tracer() if trace else None
    sys.stderr.write(
        "\nPyClaw chat — multi-turn, history preserved + persisted across turns.\n"
        f"  streaming: {'on' if stream else 'off'}   trace: {'on' if trace else 'off'}"
        f"   session saved to: {store.root}/{session_id}.json\n"
        "Type your request and press Enter. Commands: 'quit' / 'exit' to leave.\n\n"
    )
    sys.stderr.flush()

    while True:
        try:
            sys.stderr.write("you> ")
            sys.stderr.flush()
            line = input()
        except (EOFError, KeyboardInterrupt):
            sys.stderr.write("\nbye.\n")
            return 0

        task = line.strip()
        if not task:
            continue
        if task.lower() in {"quit", "exit", ":q"}:
            sys.stderr.write("bye.\n")
            return 0

        # Stream assistant text to stdout as it arrives (streaming replies).
        def _emit(chunk: str) -> None:
            sys.stdout.write(chunk)
            sys.stdout.flush()

        try:
            if stream:
                answer = loop.run(task, on_delta=_emit, on_tool=tracer)
                sys.stdout.write("\n")  # finish the streamed line
            else:
                answer = loop.run(task, on_tool=tracer)
                sys.stdout.write(answer.rstrip() + "\n")
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001 — keep the REPL alive on errors.
            sys.stderr.write(f"\n[error] {exc}\n")
            continue

        # Persistence: save the whole conversation after every completed turn.
        try:
            store.save(session_id, loop.context)
        except OSError as exc:  # noqa: BLE001
            sys.stderr.write(f"[warn] could not save session: {exc}\n")


def _cmd_doctor() -> int:
    """Check config + every layer; report missing/broken ones. Exit 1 if any fail."""
    checks: list[tuple[str, bool, str]] = []

    # Config
    key = _api_key()
    checks.append((
        "OPENROUTER_API_KEY", bool(key),
        "set" if key else "MISSING — export it before `run`",
    ))
    # .agent is optional (auto-created on first audit write), so this is
    # informational only — never a hard failure.
    checks.append((
        ".agent dir", True,
        str(SETTINGS.agent_dir) + ("" if SETTINGS.agent_dir.is_dir() else " (absent — created on first audit write)"),
    ))
    checks.append((f"default model", True, SETTINGS.default_model))

    # Each layer imports + constructs? (a broken layer fails loudly here)
    layer_probes = [
        ("L0 runtime/context", "pyclaw.runtime.context", "ContextManager"),
        ("L0 runtime/audit", "pyclaw.runtime.audit", "AuditLog"),
        ("L0 runtime/hitl", "pyclaw.runtime.hitl", "HITLGate"),
        ("L1 memory", "pyclaw.memory.loader", "MemoryLoader"),
        ("L2 skills", "pyclaw.skills.registry", "SkillRegistry"),
        ("L3 hooks", "pyclaw.hooks.engine", "HookEngine"),
        ("L4 subagents", "pyclaw.subagents.runner", "SubagentRunner"),
        ("L5 plugins", "pyclaw.plugins.loader", "PluginLoader"),
        ("L5 permissions", "pyclaw.plugins.permissions", "PermissionPolicy"),
        ("core/llm", "pyclaw.core.llm", "OpenRouterProvider"),
        ("core/loop", "pyclaw.core.loop", "AgentLoop"),
        ("mcp", "pyclaw.mcp.client", "McpClient"),
    ]
    import importlib

    for label, module_name, attr in layer_probes:
        try:
            module = importlib.import_module(module_name)
            getattr(module, attr)
            checks.append((label, True, "ok"))
        except Exception as exc:  # noqa: BLE001
            checks.append((label, False, f"BROKEN: {exc}"))

    # MCP config discovery (offline — does NOT connect, so doctor never hangs).
    try:
        from pyclaw.mcp.bridge import discover_configs

        cfgs = discover_configs(dotenv_path=_dotenv_path())
        names = ", ".join(c.name for c in cfgs)
        detail = f"{len(cfgs)} server(s) configured: {names}" if cfgs else "no servers configured (set MCP_SERVER_* or .agent/mcp-servers.yaml)"
        checks.append(("mcp servers", True, detail))
    except Exception as exc:  # noqa: BLE001
        checks.append(("mcp servers", False, f"config error: {exc}"))

    # Can we assemble a full loop? (catches stubs that still raise NotImplementedError)
    # MCP is disabled here so doctor stays offline-safe (it won't dial servers).
    try:
        _build_loop(with_mcp=False)
        checks.append(("assemble AgentLoop", True, "ok"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("assemble AgentLoop", False, f"FAILED: {exc}"))

    width = max(len(name) for name, _, _ in checks)
    all_ok = True
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        sys.stdout.write(f"[{mark}] {name.ljust(width)}  {detail}\n")

    sys.stdout.write("\n" + ("doctor: all checks passed\n" if all_ok else "doctor: some checks FAILED\n"))
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyclaw", description="PyClaw agent runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a task through the agent loop")
    run_p.add_argument("task", help="the task / user request")

    chat_p = sub.add_parser(
        "chat", help="interactive multi-turn chat with streaming + persistent history"
    )
    chat_p.add_argument(
        "--resume", metavar="SESSION_ID",
        help="resume a saved session from .agent/sessions/<id>.json",
    )
    chat_p.add_argument(
        "--no-stream", action="store_true",
        help="disable token streaming (print the full reply at once)",
    )
    chat_p.add_argument(
        "--trace", action="store_true",
        help="show live tool calls + arguments + results (verbose; may reveal PII)",
    )

    sub.add_parser("doctor", help="check config, .agent layout, and layer wiring")

    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args.task)
    if args.command == "chat":
        return _cmd_chat(resume=args.resume, no_stream=args.no_stream, trace=args.trace)
    if args.command == "doctor":
        return _cmd_doctor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
