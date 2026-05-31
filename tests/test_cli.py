"""Tests for the CLI: doctor reports layer health; run guards on missing key."""
from __future__ import annotations

import pytest

from pyclaw import cli


def test_doctor_all_layers_pass(capsys, monkeypatch):
    # With every stub implemented, doctor's import/construct probes must pass.
    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    # Each layer probe present and passing
    for layer in ["L0 runtime/context", "L1 memory", "L2 skills", "L3 hooks",
                  "L4 subagents", "L5 plugins", "L5 permissions", "core/loop", "mcp"]:
        assert layer in out
    assert "FAIL" not in out
    assert rc == 0


def test_doctor_reports_missing_key(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_api_key", lambda: "")
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "OPENROUTER_API_KEY" in out
    assert "MISSING" in out
    assert rc == 1  # a failed check -> non-zero


def test_run_without_key_fails_fast(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_api_key", lambda: "")
    rc = cli.main(["run", "do something"])
    err = capsys.readouterr().err
    assert "OPENROUTER_API_KEY not set" in err
    assert rc == 2


def test_run_executes_with_stubbed_loop(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")

    class FakeLoop:
        def run(self, task, user="user"):
            return f"answer for: {task}"

    monkeypatch.setattr(cli, "_build_loop", lambda **kw: FakeLoop())
    rc = cli.main(["run", "summarise the repo"])
    out = capsys.readouterr().out
    assert "answer for: summarise the repo" in out
    assert rc == 0


def test_chat_repl_multi_turn_and_quit(capsys, monkeypatch):
    """`chat` builds the loop ONCE and reuses it across stdin lines until quit."""
    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")

    builds: list[int] = []

    class FakeLoop:
        _mcp_mounted: list = []

        def __init__(self):
            builds.append(1)
            self.seen: list[str] = []

        def run(self, task, user="user", on_delta=None, on_tool=None):
            self.seen.append(task)
            reply = f"reply#{len(self.seen)}: {task}"
            if on_delta:
                on_delta(reply)
            return reply

    one = FakeLoop.__new__(FakeLoop)
    one.seen = []
    one._mcp_mounted = []
    one.context = __import__("pyclaw.runtime.context", fromlist=["ContextManager"]).ContextManager()
    monkeypatch.setattr(cli, "_build_loop", lambda **kw: (builds.append(1) or one))

    # Two real turns, one blank line (ignored), then quit.
    lines = iter(["hello", "", "and again", "quit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(lines))

    rc = cli.main(["chat"])
    out = capsys.readouterr().out
    assert rc == 0
    assert builds == [1]                       # loop built exactly once (MCP mounted once)
    assert one.seen == ["hello", "and again"]  # blank line skipped, quit not run
    assert "reply#1: hello" in out
    assert "reply#2: and again" in out


def test_chat_without_key_fails_fast(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_api_key", lambda: "")
    rc = cli.main(["chat"])
    err = capsys.readouterr().err
    assert "OPENROUTER_API_KEY not set" in err
    assert rc == 2


def test_chat_eof_exits_cleanly(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")

    from pyclaw.runtime.context import ContextManager

    class FakeLoop:
        _mcp_mounted: list = []
        def __init__(self):
            self.context = ContextManager()
        def run(self, task, user="user", on_delta=None, on_tool=None):
            return "x"

    monkeypatch.setattr(cli, "_build_loop", lambda **kw: FakeLoop())

    def _raise(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise)
    rc = cli.main(["chat"])
    assert rc == 0  # Ctrl-D exits cleanly


def test_chat_persists_and_resumes(tmp_path, capsys, monkeypatch):
    """chat saves the session each turn; --resume reloads it into a new loop."""
    from pyclaw.runtime.context import ContextManager, Message, Role
    from pyclaw.runtime.session import SessionStore

    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")
    store = SessionStore(root=tmp_path / "sessions")
    monkeypatch.setattr(cli, "SessionStore", lambda: store, raising=False)
    # _cmd_chat imports SessionStore locally; patch the source module instead.
    import pyclaw.runtime.session as sess_mod
    monkeypatch.setattr(sess_mod, "SessionStore", lambda *a, **k: store)

    class FakeLoop:
        def __init__(self):
            self.context = ContextManager()
            self._mcp_mounted = []

        def run(self, task, user="user", on_delta=None, on_tool=None):
            self.context.append(Message(role=Role.USER, content=task))
            reply = f"echo:{task}"
            if on_delta:
                on_delta(reply)
            self.context.append(Message(role=Role.ASSISTANT, content=reply))
            return reply

    loop1 = FakeLoop()
    monkeypatch.setattr(cli, "_build_loop", lambda **kw: loop1)
    lines = iter(["remember 42", "quit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(lines))

    rc = cli.main(["chat"])
    assert rc == 0
    saved = store.list_ids()
    assert len(saved) == 1                       # session persisted to disk
    sid = saved[0]

    # New process / loop: resume the saved session and confirm history loaded.
    loop2 = FakeLoop()
    monkeypatch.setattr(cli, "_build_loop", lambda **kw: loop2)
    lines2 = iter(["quit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(lines2))
    rc = cli.main(["chat", "--resume", sid])
    assert rc == 0
    contents = [m.content for m in loop2.context.messages]
    assert "remember 42" in contents             # prior turn restored
    assert "echo:remember 42" in contents


def test_chat_resume_missing_fails(capsys, monkeypatch, tmp_path):
    from pyclaw.runtime.session import SessionStore
    import pyclaw.runtime.session as sess_mod

    monkeypatch.setattr(cli, "_api_key", lambda: "sk-test")
    store = SessionStore(root=tmp_path / "sessions")
    monkeypatch.setattr(sess_mod, "SessionStore", lambda *a, **k: store)

    class FakeLoop:
        from pyclaw.runtime.context import ContextManager as _CM
        def __init__(self):
            from pyclaw.runtime.context import ContextManager
            self.context = ContextManager()
            self._mcp_mounted = []
        def run(self, *a, **k):
            return "x"

    monkeypatch.setattr(cli, "_build_loop", lambda **kw: FakeLoop())
    rc = cli.main(["chat", "--resume", "no-such-id"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not found" in err


def test_tool_tracer_prints_name_args_and_result():
    """_make_tool_tracer prints the tool name, arguments on 'call', and the
    result + elapsed time on 'return' — full detail for --trace."""
    lines: list[str] = []
    tracer = cli._make_tool_tracer(write=lines.append)
    tracer("call", "db_query", {"arguments": {"sql": "SELECT 1"}})
    tracer("return", "db_query", {"result": {"rows": [[1]]}, "seconds": 0.123})
    blob = "".join(lines)
    assert "call" in blob and "db_query" in blob
    assert "SELECT 1" in blob              # arguments surfaced
    assert "return" in blob
    assert "rows" in blob                  # real result surfaced
    assert "0.12s" in blob                 # elapsed time formatted


def test_tool_tracer_truncates_long_results():
    """Very long results are truncated so a huge tool payload can't flood the
    terminal (PII surface is bounded too)."""
    lines: list[str] = []
    tracer = cli._make_tool_tracer(write=lines.append)
    big = "x" * 5000
    tracer("return", "db_query", {"result": big, "seconds": 0.0})
    blob = "".join(lines)
    assert "chars)" in blob                # truncation marker present
    assert len(blob) < 5000                # not the full 5000-char payload


def test_no_command_errors():
    with pytest.raises(SystemExit):
        cli.main([])
