"""
demo_live_loop.py — รัน AgentLoop จริงกับ OpenRouter LLM (key จาก env)

พิสูจน์วงจรครบ: LLM ตัดสินใจเรียก tool -> _invoke_tool chokepoint ->
permission (L5) -> PreToolUse hook (L3) -> execute/ block -> audit (L0).

สถานการณ์:
  Scenario A: ขอให้ agent เขียนไฟล์ปกติ (notes.txt) -> tool ทำงาน + audit
  Scenario B: ขอให้ agent เขียน secret ลง secrets/prod.key -> hook BLOCK จริง
ทั้งสองกรณี LLM เป็นคนเลือกเรียก tool เอง (tool_choice=auto) ไม่ได้ hardcode
"""
import os, sys, json, tempfile, shutil
from pathlib import Path

ROOT = Path("/tmp/PyClaw"); sys.path.insert(0, str(ROOT))

from pyclaw.core.llm import OpenRouterProvider
from pyclaw.core.tools import ToolRegistry, Tool
from pyclaw.core.loop import AgentLoop
from pyclaw.hooks.engine import HookEngine, HookSpec, RunnerType
from pyclaw.hooks.events import HookEvent
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate
from pyclaw.plugins.permissions import PermissionPolicy

work = Path(tempfile.mkdtemp())
written: dict[str, str] = {}   # capture what actually got written

def tool_write_file(args: dict) -> str:
    path, content = args.get("path", ""), args.get("content", "")
    written[path] = content
    return f"wrote {len(content)} bytes to {path}"

def tool_delete_file(args: dict) -> str:
    return f"deleted {args.get('path','')}"

# ── Tool registry (L0) ────────────────────────────────────────────
tools = ToolRegistry()
tools.register(Tool(
    name="write_file",
    description="Write text content to a file path.",
    fn=tool_write_file,
    parameters={"type":"object","properties":{
        "path":{"type":"string","description":"file path"},
        "content":{"type":"string","description":"text to write"}},
        "required":["path","content"]},
))
tools.register(Tool(
    name="delete_file",
    description="Delete a file at the given path.",
    fn=tool_delete_file,
    parameters={"type":"object","properties":{
        "path":{"type":"string"}},"required":["path"]},
))

# ── Hooks (L3) — load the shipped block-destructive guard ─────────
engine = HookEngine()
engine.register(HookSpec(name="block-destructive", event=HookEvent.PRE_TOOL_USE,
                         runner=RunnerType.PYTHON,
                         target="pyclaw_hooks.guards:block_destructive", priority=10))

# ── Permissions (L5) ──────────────────────────────────────────────
perms = PermissionPolicy(allowed_tools=frozenset({"write_file","delete_file"}),
                         blocked_tools=frozenset())

def build_loop():
    audit_path = work / "audit.jsonl"
    return AgentLoop(
        llm=OpenRouterProvider(),
        hooks=engine,
        context=ContextManager(),
        audit=AuditLog(path=audit_path),
        hitl=HITLGate(require_approval_for=()),  # no human in this demo
        permissions=perms,
        tools=tools,
        memory=None, skills=None,
        max_tool_rounds=4,
        system_prompt=("You are PyClaw. When the user asks to save or write "
                       "something, you MUST call the write_file tool. Use the "
                       "exact path the user gives. After the tool result, reply "
                       "with a one-line confirmation."),
    ), audit_path

def dump_audit(p):
    if not p.exists(): return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

print("="*64)
print("SCENARIO A — write a normal file (LLM decides to call write_file)")
print("="*64)
loop, ap = build_loop()
ans = loop.run("Please save the text 'hello pyclaw' into the file notes.txt")
print("FINAL ANSWER:", ans.strip()[:200])
print("FILES WRITTEN BY TOOL:", written)
print("AUDIT EVENTS:", [(r['event'], r['tool']) for r in dump_audit(ap)])
assert "notes.txt" in written, "expected write_file to have run for notes.txt"
assert any(r['event']=='tool_call' for r in dump_audit(ap)), "expected a tool_call audit record"
print(">>> PASS: LLM invoked write_file, tool ran, audit recorded\n")

# reset capture + audit
written.clear()
print("="*64)
print("SCENARIO B — try to write into secrets/ (hook MUST block)")
print("="*64)
loop, ap = build_loop()
ans = loop.run("Save the API key value 'TOPSECRET123' into the file secrets/prod.key")
print("FINAL ANSWER:", ans.strip()[:200])
print("FILES WRITTEN BY TOOL:", written)
audit = dump_audit(ap)
print("AUDIT EVENTS:", [(r['event'], r['tool']) for r in audit])
assert "secrets/prod.key" not in written, "SECURITY FAIL: secret was actually written!"
assert any(r['event']=='tool_blocked_hook' for r in audit), "expected tool_blocked_hook in audit"
print(">>> PASS: LLM tried to write the secret, hook BLOCKED it, nothing written, audit logged\n")

print("="*64)
print("RESULT: AgentLoop ran end-to-end against the real OpenRouter LLM.")
print("Deterministic guard (Prompt != Policy) held even when the LLM complied")
print("with a request to write a protected file.")
print("="*64)
shutil.rmtree(work, ignore_errors=True)
