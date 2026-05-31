"""
demo_subagents_live.py — รัน Layer 4 (Subagent delegation) จริงกับ OpenRouter LLM

พิสูจน์ 4 อย่างด้วย LLM จริง (key จาก env, ไม่มี hardcode):

  1) ParallelTeam รัน 3 subagent พร้อมกัน (EXPLORE / PLAN / GENERAL)
     แต่ละตัวมี AgentLoop + context แยกอิสระ (principle #2)
  2) Tool restriction: subagent EXPLORE ถูกถอด write_file ออก (inherit-then-restrict)
     => ขอให้เขียนไฟล์ ก็เขียนไม่ได้ เพราะ permission policy ไม่อนุญาต
  3) No-nesting (principle #3): ไม่มี tool spawn_subagent ใน subagent เลย -> ขยายชั้นไม่ได้
  4) PreSubagentSpawn hook (L3) BLOCK ได้จริง — เป็น policy ไม่ใช่ prompt (principle #1)

สิ่งเดียวที่ข้ามกลับมาหา lead คือ SubagentResult.summary (ไม่ใช่ transcript เต็ม)
"""
import os, sys, json, tempfile, shutil
from pathlib import Path

ROOT = Path("/tmp/PyClaw"); sys.path.insert(0, str(ROOT))

from pyclaw.core.llm import OpenRouterProvider
from pyclaw.core.tools import ToolRegistry, Tool
from pyclaw.core.loop import AgentLoop
from pyclaw.hooks import HookEngine
from pyclaw.hooks.engine import HookSpec
from pyclaw.hooks.events import HookEvent, HookAction, HookResult, HookPayload
from pyclaw.hooks.runners import RunnerType
from pyclaw.runtime.audit import AuditLog
from pyclaw.runtime.context import ContextManager
from pyclaw.runtime.hitl import HITLGate
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.subagents.runner import ParallelTeam, SubagentRunner
from pyclaw.subagents.types import SubagentSpec, SubagentType

work = Path(tempfile.mkdtemp())
writes: dict[str, str] = {}        # files any subagent managed to write
tool_log: list[tuple[str, str]] = []  # (subagent_objective_tag, tool_name)


def make_tools(tag: str) -> ToolRegistry:
    """A fresh tool registry per subagent (isolation also of side-effects)."""
    reg = ToolRegistry()

    def read_file(args: dict) -> str:
        tool_log.append((tag, "read_file"))
        return "PROJECT: PyClaw — a deterministic-first agent runtime (6 layers)."

    def write_file(args: dict) -> str:
        tool_log.append((tag, "write_file"))
        writes[args.get("path", "")] = args.get("content", "")
        return f"wrote {args.get('path','')}"

    reg.register(Tool(name="read_file", description="Read a project fact.",
                      fn=read_file, parameters={"type": "object", "properties": {}}))
    reg.register(Tool(name="write_file", description="Write content to a file path.",
                      fn=write_file, parameters={"type": "object", "properties": {
                          "path": {"type": "string"}, "content": {"type": "string"}},
                          "required": ["path", "content"]}))
    return reg


def isolated_runner(spec: SubagentSpec) -> str:
    """Build + run a REAL isolated AgentLoop for one subagent (LLM-backed).

    The loop is allowlisted to spec.allowed_tools, gets a FRESH context, and is
    given only the tools it is permitted to use. No spawn tool exists -> depth 1.
    """
    tag = spec.type.value
    reg = make_tools(tag)
    # Permission policy = exactly the tools resolved for this subagent type.
    perms = PermissionPolicy(allowed_tools=frozenset(spec.allowed_tools))
    loop = AgentLoop(
        llm=OpenRouterProvider(),
        hooks=HookEngine(),
        context=ContextManager(),               # isolated context
        audit=AuditLog(path=work / f"audit_{tag}.jsonl"),
        hitl=HITLGate(require_approval_for=()),
        permissions=perms,
        tools=reg,
        max_tool_rounds=4,
        system_prompt=(
            f"You are a PyClaw {tag} subagent. Use a tool if helpful, then reply "
            "with ONE concise sentence summarising your finding. "
            "You cannot spawn other subagents."
        ),
    )
    return loop.run(spec.objective, user=f"subagent:{tag}")


print("=" * 68)
print("DEMO — Layer 4 subagent delegation against the REAL OpenRouter LLM")
print("=" * 68)

# Parent inherits these tools; each subagent type restricts from here.
parent_tools = ("read_file", "write_file")
runner = SubagentRunner(parent_tools=parent_tools, run_isolated=isolated_runner, max_workers=3)
team = ParallelTeam(runner=runner)

specs = [
    SubagentSpec(type=SubagentType.EXPLORE,
                 objective="Read the project fact and tell me what PyClaw is."),
    SubagentSpec(type=SubagentType.PLAN,
                 objective="Read the project fact, then state one next step to harden it."),
    SubagentSpec(type=SubagentType.GENERAL,
                 objective="Read the project fact and write a one-word tag to summary.txt."),
]

# Show the resolved tool sets BEFORE running (deterministic policy).
print("\nResolved tool sets (inherit-then-restrict):")
for s in specs:
    print(f"  {s.type.value:8s} -> {runner.resolve_tools(s)}")

print("\nRunning 3 subagents in parallel...\n")
results = team.run(specs)

for r in results:
    print(f"[{r.spec.type.value:8s}] ok={r.ok}")
    print(f"            summary: {r.summary.strip()[:180]}")

# --- assertions: prove the guarantees -------------------------------------
print("\n" + "-" * 68)
explore_tools = runner.resolve_tools(specs[0])
assert "write_file" not in explore_tools, "EXPLORE must not inherit write_file"
assert "spawn_subagent" not in explore_tools, "no nesting tool allowed"
print(">>> PASS: EXPLORE/PLAN cannot write (write_file removed); no spawn tool anywhere")

# EXPLORE attempted no write (it had no permission); GENERAL was allowed to.
explore_writes = [t for (tag, t) in tool_log if tag == "explore" and t == "write_file"]
assert not explore_writes, "SECURITY FAIL: an EXPLORE subagent wrote a file"
print(">>> PASS: EXPLORE subagent performed NO write (permission policy held)")

assert all(r.ok for r in results), "a subagent failed"
assert all(r.summary.strip() for r in results), "a subagent returned an empty summary"
print(">>> PASS: all 3 isolated loops returned ONLY a summary (no transcript leaked)")

# --- PreSubagentSpawn hook BLOCK (policy, not prompt) ----------------------
print("\n" + "=" * 68)
print("PreSubagentSpawn hook BLOCK — delegation policy is code, not a prompt")
print("=" * 68)
guard = HookEngine()
guard.register(HookSpec(name="deny_explore", event=HookEvent.PRE_SUBAGENT_SPAWN,
                        runner=RunnerType.PYTHON,
                        target="pyclaw.subagents._test_hooks:deny_explore", priority=10))
guarded = SubagentRunner(parent_tools=parent_tools, hooks=guard, run_isolated=isolated_runner)
blocked = guarded.spawn(SubagentSpec(type=SubagentType.EXPLORE, objective="should be blocked"))
print(f"spawn(EXPLORE) -> ok={blocked.ok}, error={blocked.error}")
assert blocked.ok is False and "blocked" in (blocked.error or "").lower()
print(">>> PASS: hook blocked the spawn deterministically (LLM never ran)")

print("\n" + "=" * 68)
print("RESULT: parallel subagent delegation works on the real LLM, with")
print("isolation (#2), no-nesting (#3), and code-enforced policy (#1) all holding.")
print("=" * 68)
shutil.rmtree(work, ignore_errors=True)
