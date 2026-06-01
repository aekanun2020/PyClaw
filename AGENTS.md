# PyClaw specialized agents

This file is the source of truth for the Orchestrator's agent registry (#5).
The Orchestrator reads it at startup to build BOTH its routing prompt and each
agent's allowed tool group. It is parsed by `pyclaw/orchestrator/registry.py`
using the same `---` frontmatter convention as SKILL.md (see
`pyclaw/skills/registry.py`).

Each agent is one frontmatter block. Recognised keys:

  - `name`        : the agent id used in `route_to_agent(agent=...)`.
  - `description` : when-to-use text, shown verbatim in the routing prompt.
  - `tools`       : comma-separated tool-name prefixes the agent may use. A
                    tool is granted to the agent when its name starts with any
                    listed prefix (e.g. `db_` matches `db_execute_query_tool`).
                    The orchestrator resolves these against the live tool
                    registry, so the agent only ever receives REAL callables.
  - `home`        : OPTIONAL override for where the agent's persona files live.
                    By default each agent's home is `agents/<name>/` (next to
                    this file). That folder may hold `SOUL.md` (persona +
                    boundaries) and `TOOLS.md` (tool-usage rules); when present
                    they are composed — with the anti-hallucination guardrail
                    appended — into the routed agent's OWN system prompt. Both
                    files are OPTIONAL: if absent, the agent falls back to the
                    generic subagent prompt (no breakage). The tool allowlist
                    above stays in force regardless (defence in depth).

Backward-compat note: this file is config only. With `--orchestrator` OFF (the
default) nothing here is loaded, so the flat chat loop is unaffected.

---
name: db-agent
description: Read-only queries against TestDB, an HR system with 9 tables. Use for questions about employees, departments, salaries, org structure, or any tabular HR data. Cannot write or modify data.
tools: db_
---

---
name: pdpa-agent
description: Thai PDPA (Personal Data Protection Act) law question-and-answer. Use for questions about Thai data-protection law, PDPA sections, penalties, and compliance obligations.
tools: pdpa_
---

---
name: rag-agent
description: ค้นหาและจัดการคลังเอกสาร RAG (retrieval-augmented generation). ใช้เมื่อผู้ใช้ถามว่าในคลัง/RAG มีเอกสารหรือแหล่งข้อมูลอะไรบ้าง, ต้องการค้นเนื้อหาจากเอกสารที่จัดเก็บไว้, หรือเพิ่มเอกสาร/ไดเรกทอรีเข้าคลัง. นี่คือคลังเอกสาร ไม่ใช่ฐานข้อมูล SQL — อย่าสับสนกับ db-agent.
tools: rag_
---
