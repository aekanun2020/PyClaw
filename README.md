# PyClaw

> **PyClaw = EliteClaw rewritten in Python + more features.**
> A **deterministic-first** agent runtime implementing the **5-Layer ADK Spec**.
> Core principle: **Prompt ≠ Policy** — deterministic logic lives in **Hooks (code)**, never in LLM prompts.

PyClaw is the Python successor to [EliteClaw](https://github.com/aekanun2020/EliteClaw) (private, TypeScript v7.0.0).
It keeps EliteClaw's strengths (Orchestrator → Specialized Agents, custom MCP clients, SOUL/TOOLS/SKILL.md pattern,
OpenRouter LLM) and **closes the gaps** EliteClaw was missing against the ADK Spec.

## Why deterministic-first?

An LLM is **not 100% deterministic** even at `temperature=0` + fixed seed:
floating-point non-associativity, dynamic batching (reduction-order changes), and autoregressive decoding
amplify tiny numeric differences into different tokens. So any rule that *must* hold every time
(permission checks, PDPA guards, audit logging, approvals) **cannot** be left to the prompt.

PyClaw enforces those rules in the **Hook engine (Layer 3)**: every tool call passes through `PreToolUse`,
which can `block` / `modify` / `notify` deterministically in code. The LLM cannot skip a hook.

For reproducible *inference* itself, PyClaw's OpenRouter provider documents the batch-invariant options
(vLLM `VLLM_BATCH_INVARIANT=1`, SGLang `--enable-deterministic-inference`) for self-hosted backends.

## The 5-Layer ADK Spec (+ Layer 0)

| Layer | Module | Responsibility | EliteClaw status → PyClaw |
|-------|--------|----------------|---------------------------|
| **0 Runtime** | `pyclaw/runtime` | context mgmt, audit log, HITL approval | 🟡 → 🟢 |
| **1 Memory** | `pyclaw/memory` | hierarchy, `@import`, auto-memory | 🟡 → 🟢 |
| **2 Skill** | `pyclaw/skills` | frontmatter, lazy load, auto-detect, chaining | 🟡 → 🟢 |
| **3 Hook** ★ | `pyclaw/hooks` | 8 events, allow/modify/block/notify | 🔴 → 🟢 |
| **4 Subagent** | `pyclaw/subagents` | explore/plan/review/general + parallel | 🟢 → 🟢 |
| **5 Plugin** | `pyclaw/plugins` | plugin.yaml, permissions.yaml, versioning | 🔴 → 🟢 |
| MCP | `pyclaw/mcp` | `.agent/mcp-servers.yaml` | 🟢 → 🟢 |

★ = the deterministic core. Hooks wrap **every** tool call in `pyclaw/core/loop.py`.

## 7 Design Principles (from the ADK Spec)

1. **Prompt ≠ Policy** — deterministic logic must be a Hook, not a prompt instruction.
2. **Lazy load** — skills/memory loaded on demand, not all upfront.
3. **Bound delegation** — subagents inherit-then-restrict, no nested spawning.
4. **Memory is constitutional** — `AGENT_MEMORY.md` is the source of truth.
5. **Package for reuse** — plugins bundle skills/hooks/agents for team distribution.
6. **Fail loudly** — a missing required layer raises, it does not silently pass.
7. **Maintain compatibility** — support both `AGENT_MEMORY.md` and `CLAUDE.md`.

## Status

✅ **Production-ready.** All 6 layers implemented (no stubs). 95 unit tests pass · `pyclaw doctor`
probes every layer · live LLM demos in [`docs/demos/`](docs/demos/).

## Layout

```
pyclaw/
├── runtime/      # Layer 0 — context.py, audit.py, hitl.py
├── memory/       # Layer 1 — loader.py (hierarchy + @import + auto-memory)
├── skills/       # Layer 2 — loader.py, registry.py (frontmatter, lazy, auto-detect, chaining)
├── hooks/        # Layer 3 ★ — engine.py, runners.py, events.py (8 events)
├── subagents/    # Layer 4 — runner.py, types.py (explore/plan/review/general + parallel)
├── plugins/      # Layer 5 — loader.py, permissions.py (plugin.yaml + permissions.yaml)
├── mcp/          # MCP — client.py (.agent/mcp-servers.yaml)
├── core/         # agent loop + LLM provider (OpenRouter) — Hook wraps every tool call
├── config.py     # paths, .env, .agent discovery
└── cli.py        # entrypoint
.agent/           # runtime state: logs/audit.jsonl, hooks/, mcp-servers.yaml
```

## Run it

```bash
pip install -e .

# 1) LLM key
export OPENROUTER_API_KEY="sk-or-..."

# 2) Your MCP server(s) — add as many as you like (EliteClaw-compatible).
#    A URL ending in /mcp is auto-detected as Streamable HTTP; otherwise classic
#    SSE — for which you point the URL at the event-stream path (usually /sse).
export MCP_SERVER_1_URL="http://127.0.0.1:9000/sse"
export MCP_SERVER_1_NAME="mssql"
export MCP_SERVER_1_PREFIX="db_"      # tools become db_<toolname>

# 3) Run — pyclaw connects to your MCP servers and exposes their tools to the agent
pyclaw run "your task here"
```

That's it. Every MCP tool passes through the hook engine, permission policy, and
audit log like any built-in tool. A server that's unreachable is skipped with a
warning (set `PYCLAW_MCP_STRICT=1` to fail instead). Run `pyclaw doctor` to see
which servers are configured.

Prefer a file over env vars? Put servers in `.agent/mcp-servers.yaml` (keys:
`name, url, transport, headers, fallback, timeout, tool_prefix`) — or point
`PYCLAW_DOTENV` at an existing EliteClaw `.env`. All sources are merged.

### Already have an EliteClaw / OpenClaw `.env`?

Reuse it verbatim — one variable, nothing to rewrite:

```bash
pip install -e .
PYCLAW_DOTENV=/path/to/your/.env pyclaw run "your task here"
```

PyClaw reads that file for **both** its MCP servers (`MCP_SERVER_1_*`, …) and
its LLM settings: `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` (e.g. a local
Ollama at `http://host:11434/v1`, where the key is `ollama`), and the model from
`OPENROUTER_MODEL`. Set `PYCLAW_DEFAULT_MODEL` only if you want to override that
model just for PyClaw. Run `PYCLAW_DOTENV=/path/.env pyclaw doctor` first to see
every server it picked up.
