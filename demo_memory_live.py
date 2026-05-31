"""
demo_memory_live.py — Layer 1 Memory loaded into a REAL LLM loop.

Part 1 (no LLM): build a memory hierarchy on disk
    project/AGENT_MEMORY.md        (global rule + @import of coding-standards.md)
    project/coding-standards.md    (imported module)
    project/service/AGENT_MEMORY.md(local override-scope rule)
    project/service/AUTO_MEMORY.md (agent-written notes, capped)
  -> MemoryLoader.load() walks the tree, expands @import, caps AUTO_MEMORY,
     and merges global-first / local-last.

Part 2 (real LLM): inject that memory as the system prompt and ask the model a
  question whose answer is ONLY found in memory. If the model answers correctly,
  the Memory layer is demonstrably feeding the agent (not the prompt we typed).
"""
import os, sys, tempfile, shutil
from pathlib import Path

ROOT = Path("/tmp/PyClaw"); sys.path.insert(0, str(ROOT))
from pyclaw.memory.loader import MemoryLoader
from pyclaw.core.llm import OpenRouterProvider

bar = "=" * 66
proj = Path(tempfile.mkdtemp()) / "project"
svc = proj / "service"
svc.mkdir(parents=True)

# --- build the hierarchy --------------------------------------------------
(proj / "coding-standards.md").write_text(
    "## Coding Standards\n"
    "- Indentation: 4 spaces, never tabs.\n"
    "- The project's secret canary string is BLUE-PANGOLIN-42.\n",
    encoding="utf-8")
(proj / "AGENT_MEMORY.md").write_text(
    "# Global Project Memory\n"
    "All code must pass ruff before commit.\n"
    "@./coding-standards.md\n",
    encoding="utf-8")
(svc / "AGENT_MEMORY.md").write_text(
    "# Service-Local Memory\n"
    "This service (billing) must never log card numbers.\n",
    encoding="utf-8")
(svc / "AUTO_MEMORY.md").write_text(
    "\n".join(f"auto-note {i}" for i in range(500)),  # will be capped to 200
    encoding="utf-8")

print(bar); print("PART 1 — MemoryLoader walks the tree (deterministic, no LLM)"); print(bar)
bundle = MemoryLoader(root=proj).load(start=svc)
print("Sources merged (global-first -> local-last):")
for s in bundle.sources:
    print("   -", s.relative_to(proj.parent))
auto_lines = [l for l in bundle.text.splitlines() if l.startswith("auto-note")]
print(f"\nChecks:")
print(f"   global rule present : {'ruff before commit' in bundle.text}")
print(f"   @import expanded    : {'BLUE-PANGOLIN-42' in bundle.text and '@./coding-standards.md' not in bundle.text}")
print(f"   local scope present : {'never log card numbers' in bundle.text}")
print(f"   scope order ok      : {bundle.text.index('Global Project Memory') < bundle.text.index('Service-Local Memory')}")
print(f"   AUTO_MEMORY capped  : {len(auto_lines)} lines (<=200) -> {len(auto_lines) <= 200}")
print(f"\nMerged memory is {len(bundle.text)} chars from {len(bundle.sources)} files.\n")

print(bar); print("PART 2 — inject memory into a REAL LLM and query memory-only facts"); print(bar)
provider = OpenRouterProvider()
system = ("You are PyClaw. The following is your project memory. Answer ONLY "
          "from it.\n\n# Memory\n" + bundle.text)

def ask(q):
    r = provider.complete(messages=[{"role":"system","content":system},
                                    {"role":"user","content":q}])
    return r.text.strip()

q1 = "What is the project's secret canary string? Reply with just the string."
a1 = ask(q1)
print(f"Q: {q1}\nA: {a1}")
ok1 = "BLUE-PANGOLIN-42" in a1
print(f"   -> canary recalled from imported module: {ok1}\n")

q2 = "In the billing service, is it allowed to log card numbers? Answer yes or no and why in one line."
a2 = ask(q2)
print(f"Q: {q2}\nA: {a2}")
ok2 = "no" in a2.lower()
print(f"   -> local-scope rule honored: {ok2}\n")

print(bar)
if ok1 and ok2:
    print("RESULT: Layer 1 memory (directory walk + @import + scope + caps) fed a")
    print("real LLM, which answered facts that exist ONLY in memory. PASS.")
else:
    print("RESULT: memory did not fully propagate — see answers above.")
print(bar)
shutil.rmtree(proj.parent, ignore_errors=True)
