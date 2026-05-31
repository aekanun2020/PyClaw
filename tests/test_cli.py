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

        def run(self, task, user="user"):
            self.seen.append(task)
            return f"reply#{len(self.seen)}: {task}"

    one = FakeLoop.__new__(FakeLoop)
    one.seen = []
    one._mcp_mounted = []
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

    class FakeLoop:
        _mcp_mounted: list = []
        def run(self, task, user="user"):
            return "x"

    monkeypatch.setattr(cli, "_build_loop", lambda **kw: FakeLoop())

    def _raise(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise)
    rc = cli.main(["chat"])
    assert rc == 0  # Ctrl-D exits cleanly


def test_no_command_errors():
    with pytest.raises(SystemExit):
        cli.main([])
