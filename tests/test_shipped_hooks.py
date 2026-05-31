"""Tests for the shipped guardrails: block_destructive + autoformat,
and the spec-compatible bash alias (input / modified_input / reason).
"""

from __future__ import annotations

from pyclaw.hooks.events import HookAction, HookEvent, HookPayload
from pyclaw.hooks.runners import BashRunner
from pyclaw_hooks.format import autoformat
from pyclaw_hooks.guards import block_destructive


def _pre(tool: str, **args) -> HookPayload:
    return HookPayload(event=HookEvent.PRE_TOOL_USE, tool=tool, arguments=dict(args))


# --- block_destructive --------------------------------------------------------
def test_blocks_delete_of_env() -> None:
    res = block_destructive(_pre("delete_file", path="config/.env"))
    assert res.action is HookAction.BLOCK


def test_blocks_write_to_secrets() -> None:
    res = block_destructive(_pre("write_file", path="secrets/key.txt"))
    assert res.action is HookAction.BLOCK


def test_blocks_pem_and_git() -> None:
    assert block_destructive(_pre("write_file", path="deploy/server.pem")).action is HookAction.BLOCK
    assert block_destructive(_pre("write_file", path=".git/config")).action is HookAction.BLOCK


def test_allows_normal_write() -> None:
    res = block_destructive(_pre("write_file", path="src/app.py"))
    assert res.action is HookAction.ALLOW


def test_destructive_tool_on_normal_path_escalates_to_notify() -> None:
    # A destructive op on a non-protected path must NOT pass the hook silently;
    # it escalates to NOTIFY so the HITL gate forces approval (spec section 9).
    res = block_destructive(_pre("delete_file", path="README.md"))
    assert res.action is HookAction.NOTIFY
    assert res.message


def test_allows_read_of_env_via_safe_tool() -> None:
    # read_file is not destructive, but .env is protected -> still blocked.
    res = block_destructive(_pre("read_file", path=".env"))
    assert res.action is HookAction.BLOCK


# --- autoformat ---------------------------------------------------------------
def test_autoformat_skips_non_python() -> None:
    res = autoformat(HookPayload(event=HookEvent.POST_EDIT, tool="edit",
                                 arguments={"path": "README.md"}))
    assert res.action is HookAction.ALLOW


def test_autoformat_python_returns_allow_or_notify(tmp_path) -> None:
    f = tmp_path / "x.py"
    f.write_text("x=1\n")
    res = autoformat(HookPayload(event=HookEvent.POST_EDIT, tool="edit",
                                 arguments={"path": str(f)}))
    # NOTIFY if a formatter is installed, ALLOW if not — never BLOCK.
    assert res.action in (HookAction.ALLOW, HookAction.NOTIFY)


# --- spec-compatible bash alias ----------------------------------------------
def test_bash_alias_reason_maps_to_message() -> None:
    runner = BashRunner()
    res = runner.run("""echo '{"action":"block","reason":"per spec"}'""",
                     _pre("bash", command="x"))
    assert res.action is HookAction.BLOCK
    assert res.message == "per spec"


def test_bash_alias_modified_input_maps_to_arguments() -> None:
    runner = BashRunner()
    out = '{"action":"modify","modified_input":{"command":"safe"}}'
    res = runner.run(f"echo '{out}'", _pre("bash", command="danger"))
    assert res.action is HookAction.MODIFY
    assert res.modified_payload is not None
    assert res.modified_payload.arguments == {"command": "safe"}


def test_bash_receives_input_alias_on_stdin() -> None:
    # A spec-style hook reads payload['input']; confirm we provide it.
    runner = BashRunner()
    script = (
        """python3 -c 'import sys,json; p=json.load(sys.stdin); """
        """print(json.dumps({"action":"notify","reason":p["input"]["command"]}))'"""
    )
    res = runner.run(script, _pre("bash", command="echo-hi"))
    assert res.action is HookAction.NOTIFY
    assert res.message == "echo-hi"
