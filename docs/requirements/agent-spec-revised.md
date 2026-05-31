# Agent Architecture Specification (Claude-Style 5-Layer ADK)
### ฉบับ Revised — พฤษภาคม 2026

> **วัตถุประสงค์:** เอกสารนี้คือ specification สำหรับ AI ที่จะสร้างระบบ Agent แบบ Claude Code Architecture โดยอ้างอิงจาก 5-Layer Agent Development Kit ของ Anthropic (อัปเดตปี 2026)

---

## 1. Overview

ระบบ Agent ที่ต้องสร้างต้องประกอบด้วย 5 เลเยอร์หลักที่ทำงานร่วมกัน แต่ละเลเยอร์แก้ปัญหาเฉพาะที่ prompt อย่างเดียวไม่สามารถแก้ได้ พร้อมด้วย Layer 0 (Runtime Foundation) และ MCP Layer สำหรับ external connectivity

```
┌─────────────────────────────────────────────────────┐
│          Layer 5: Plugin / Distribution Layer        │
│        (Package & share agent behavior teamwide)    │
├─────────────────────────────────────────────────────┤
│          Layer 4: Subagent / Delegation Layer        │
│        (Isolated task execution, bounded scope)     │
├─────────────────────────────────────────────────────┤
│          Layer 3: Hook / Guardrail Layer             │
│        (Deterministic enforcement at event points)  │
├─────────────────────────────────────────────────────┤
│          Layer 2: Skill / Knowledge Layer            │
│        (On-demand modular expertise)                │
├─────────────────────────────────────────────────────┤
│          Layer 1: Memory / Policy Layer              │
│        (Persistent project context & rules)         │
├─────────────────────────────────────────────────────┤
│          Layer 0: Runtime & Tooling Foundation       │
│        (Computer use, artifact mgmt, context mgmt)  │
└─────────────────────────────────────────────────────┘
              ↕ MCP Layer (External Connectivity)
```

---

## 2. Layer 1 — Memory & Policy Layer

### วัตถุประสงค์
เก็บบริบทถาวรของโปรเจกต์และนโยบายที่ต้องโหลดทุก session โดยไม่ต้องพิมพ์ซ้ำ

### ไฟล์หลัก

| ไฟล์ | บทบาท | ลำดับความสำคัญ |
|------|-------|----------------|
| `AGENT_MEMORY.md` | กฎ/นโยบายที่มนุษย์เขียนเอง (declarative) | หลัก |
| `CLAUDE.md` | compatibility กับ Claude Code official | รอง (โหลดอัตโนมัติถ้ามี) |
| `AUTO_MEMORY.md` | บันทึกที่ agent เขียนสะสมเอง (emergent, adaptive) | เสริม |

### พฤติกรรมที่ต้องสร้าง

| พฤติกรรม | รายละเอียด |
|----------|------------|
| **Directory walking** | Agent ต้อง scan หา memory file จาก working dir ขึ้นไปถึง root แล้ว concat ทั้งหมด (ไม่ override กัน) |
| **Scope inheritance** | ไฟล์ที่อยู่ใกล้ root = กฎ global, ไฟล์ใกล้ working dir = กฎ specific ที่อ่านทีหลัง (override ได้) |
| **Import support** | รองรับ `@path/to/file` syntax ให้แตก memory ออกเป็นโมดูลย่อยได้ recursive สูงสุด 5 hops |
| **Auto memory limit** | AUTO_MEMORY โหลดแค่ 200 บรรทัดแรก หรือ 25 KB (กันบวมเกิน) |
| **Full load for human memory** | AGENT_MEMORY.md และ CLAUDE.md โหลดเต็มทุกครั้ง ไม่จำกัด |
| **Org-level deployment** | รองรับ path กลางของ organization ให้ deploy memory file ให้ทุกคนในทีมได้ |

### สิ่งที่ต้องเก็บใน Memory
- Coding standards, naming conventions
- Architecture decisions & preferred libraries
- Review checklists
- Workflow constraints ที่ต้องทำทุกครั้ง
- ข้อมูลที่ agent ควรรู้ตั้งแต่เริ่ม session

### สิ่งที่ไม่ควรเก็บใน Memory (ให้ไปอยู่ Layer อื่น)
- ขั้นตอน step-by-step เฉพาะงาน → ใส่ใน **Skill** (Layer 2)
- Logic ที่ต้อง enforce แบบ deterministic → ใส่ใน **Hook** (Layer 3)

---

## 3. Layer 2 — Knowledge / Skill Layer

### วัตถุประสงค์
จัดเก็บความรู้เฉพาะงานแบบโมดูลาร์ที่โหลดเข้า context เฉพาะเมื่อ task นั้นถูกเรียกใช้ (on-demand) ไม่บวมทุก session

### โครงสร้างไฟล์

```
.agent/
  skills/
    debug-flaky-tests/
      SKILL.md          ← คำอธิบายทักษะ + คำสั่งให้ agent
      reference.md      ← เอกสารอ้างอิง (โหลดฟรีจนกว่าจะ invoke)
    sql-migration-review/
      SKILL.md
    release-notes/
      SKILL.md
```

### พฤติกรรมที่ต้องสร้าง

| พฤติกรรม | รายละเอียด |
|----------|------------|
| **Lazy loading** | Skill body ไม่โหลดเข้า context จนกว่าจะถูกเรียกใช้ |
| **Invocation modes** | รองรับทั้ง `/skill-name` (manual) และ auto-detection จาก task description |
| **Runtime matching** | ใช้ description + semantic matching เพื่อเลือก skill ที่เหมาะสม |
| **Skill chaining** | รองรับการเรียก skill อื่นภายใน skill ได้ (composable) |
| **Subagent execution** | Skill บางตัวสามารถ route ไปรันใน subagent แยกได้ |
| **Context injection** | Skill สามารถ inject context เพิ่มเติม (env vars, file contents) ณ เวลา invocation |

### โครงสร้าง SKILL.md

```markdown
---
name: debug-flaky-tests
version: 1.0.0
description: ใช้เมื่อต้องการ debug test ที่ fail แบบ intermittent
invocation: auto        # auto | manual
subagent: false         # true ถ้าต้องรันแยก context
model_preference: null  # null = inherit, หรือระบุ model เฉพาะ
---

## วัตถุประสงค์
[อธิบายว่า skill นี้ทำอะไร]

## ขั้นตอน
1. ...
2. ...

## Reference
@./reference.md
```

### เมื่อไหร่ควรสร้าง Skill (ไม่ใช่ Memory)
- เมื่อต้องวาง instructions เดิมซ้ำมากกว่า 3 ครั้ง
- เมื่อ AGENT_MEMORY.md เริ่มเป็น step-by-step แทนที่จะเป็น fact/rule
- เมื่อมีขั้นตอนซับซ้อนเฉพาะงาน เช่น debug workflow, deploy checklist

---

## 4. Layer 3 — Guardrail / Hook Layer

### วัตถุประสงค์
บังคับพฤติกรรมของ agent แบบ deterministic ที่ lifecycle event points — ไม่ใช่แค่ "แนะนำ" ผ่าน prompt

### ความแตกต่างหลัก

| Prompt-based instruction | Hook-based guardrail |
|--------------------------|----------------------|
| "Please always format code after editing" | รัน formatter ทุกครั้ง ไม่มีข้อยกเว้น |
| อาจถูก agent ข้ามได้ | ไม่สามารถข้ามได้ |
| Soft constraint | Hard constraint |

### Hook Events ที่รองรับ

| Event | เวลาที่ fire | ตัวอย่างการใช้ |
|-------|-------------|----------------|
| `PreToolUse` | ก่อน agent ใช้ tool | กรอง input, block dangerous commands |
| `PostToolUse` | หลัง agent ใช้ tool | transform output, log |
| `PostEdit` | หลัง agent แก้ไขไฟล์ | auto-format, lint |
| `PreSession` | เริ่ม session | load critical context, validate config |
| `PostCompaction` | หลัง context compaction | re-inject rules กลับเข้า context |
| `OnError` | เมื่อเกิด error | recovery procedure, notify |
| `PreSubagentSpawn` | ก่อน spawn subagent | validate permission, log delegation |
| `PreResponse` | ก่อนตอบผู้ใช้ | final quality check, safety filter |

### Hook Payload Structure

```json
// input payload ที่ hook รับ
{
  "event": "PreToolUse",
  "tool": "bash",
  "input": {
    "command": "pytest tests/ -v"
  },
  "context": {
    "session_id": "...",
    "working_dir": "...",
    "user": "..."
  }
}

// output ที่ hook ส่งกลับ
{
  "action": "modify",         // allow | modify | block | notify
  "modified_input": {
    "command": "pytest tests/ -v 2>&1 | grep FAILED"
  },
  "reason": "Filtered to failing tests only to reduce token usage"
}
```

### Hook Execution Methods
- **Bash script** — สำหรับ shell operations, formatting
- **Python script** — สำหรับ logic ซับซ้อน, data transformation
- **HTTP endpoint** — สำหรับ integration กับ external service
- **LLM call** — สำหรับ semantic check (เช่น PreResponse quality check)

### Use Cases สำคัญ

| Use Case | Hook Event | วิธีทำ |
|----------|-----------|--------|
| Auto-format code | `PostEdit` | รัน `prettier` / `black` |
| Block protected files | `PreToolUse` | ตรวจ path แล้ว block ถ้า match `.env`, `secrets/` |
| Token optimization | `PreToolUse` | กรอง log ขนาดใหญ่ก่อนส่งให้ agent |
| Audit trail | `PostToolUse` | บันทึกทุก tool invocation ลง log |
| Context preservation | `PostCompaction` | re-inject project rules |
| Error recovery | `OnError` | rollback, notify Slack |

### กฎการใช้ Hook
> **Hook คือเครื่องมือเมื่อ "ต้องเกิดขึ้นทุกครั้ง ไม่มีข้อยกเว้น"** — ไม่ใช่เมื่อ "ส่วนใหญ่ควรทำ"

---

## 5. Layer 4 — Delegation / Subagent Layer

### วัตถุประสงค์
แบ่งงานออกจาก main agent ไปยัง worker agent ที่รันใน context แยก เพื่อป้องกัน context pollution และเพิ่ม specialization

### ปัญหาที่ Subagent แก้ไข

| ปัญหา | วิธีแก้ด้วย Subagent |
|-------|---------------------|
| Main context ยาวและรกจาก exploration logs | Subagent รัน exploratory แยก ส่งแค่ result กลับ |
| ต้องการ agent เฉพาะทาง | Subagent มี system prompt, tool access, model ของตัวเอง |
| Cost optimization | Route งานง่ายไปยัง cheaper model (เช่น Haiku) |
| Security isolation | Subagent ถูกจำกัด tool access ตาม role |

### Built-in Subagent Types

| Subagent | Tool Access | Model Recommended | วัตถุประสงค์ |
|---------|------------|-------------------|-------------|
| `explore` | Read-only | Haiku / Sonnet | วิเคราะห์ codebase โดยไม่แก้ไขอะไร |
| `plan` | Read + write notes | Sonnet | วางแผนงานก่อน execute |
| `review` | Read-only | Sonnet | Code review เฉพาะทาง |
| `general` | Full (restricted) | Sonnet / Opus | งานทั่วไป multi-step |

### โครงสร้าง Subagent Definition

```markdown
---
name: code-explorer
description: Reads and analyzes codebase without making any changes
model: claude-haiku-4
tools:
  - read_file
  - search_files
  - run_command: readonly
memory: inherit         # inherit | isolated
hooks: []
skills:
  - codebase-analysis
---

## System Prompt
คุณคือ code analysis agent ที่มีหน้าที่อ่านและวิเคราะห์โค้ดเท่านั้น
ห้ามแก้ไขไฟล์ใดๆ ทั้งสิ้น
ส่งคืนเฉพาะ summary ของสิ่งที่พบ
```

### กฎ Subagent (สำคัญมาก)

1. **ห้าม spawn subagent ซ้อนกัน** — subagent ไม่สามารถสร้าง subagent ได้ (กัน infinite recursion)
2. **Return result only** — subagent ส่งกลับเฉพาะผลลัพธ์สรุป ไม่ใช่ reasoning ทั้งหมด
3. **Inherit then restrict** — รับ permission จาก parent แล้ว restrict เพิ่ม (ไม่ expand ได้)
4. **Isolated context** — งานใน subagent ไม่ปนเปื้อน main context

### Agent Team Pattern
รองรับ multiple subagents ทำงาน **parallel** ภายใต้ lead agent:

```
Lead Agent
├── explorer subagent (async)   → วิเคราะห์ codebase
├── plan subagent (async)       → draft implementation plan
└── review subagent (async)     → check existing tests
         ↓ (รอ results ทั้ง 3)
Lead Agent → synthesize → implement
```

### Subagent Storage Scopes

```
~/.agent/agents/          ← user-level (ใช้ได้ทุกโปรเจกต์)
.agent/agents/            ← project-level (เฉพาะโปรเจกต์นี้)
--subagent flag           ← session-only (ใช้ครั้งเดียว)
plugins/agents/           ← plugin-distributed (แจก teamwide)
```

---

## 6. Layer 5 — Distribution / Plugin Layer

### วัตถุประสงค์
แพ็กเกจ behavior ทั้งหมด (memory rules, skills, hooks, subagents) ให้ติดตั้งและใช้งานร่วมกันได้ทั้งทีม โดยไม่ต้อง rebuild ทุกครั้ง

### โครงสร้าง Plugin

```
my-team-agent-plugin/
├── plugin.yaml           ← manifest หลัก
├── memory/
│   └── AGENT_MEMORY.md   ← org-wide rules
├── skills/
│   ├── code-review/
│   └── deploy-checklist/
├── hooks/
│   ├── pre-commit.sh
│   └── post-edit-format.sh
├── agents/
│   ├── explorer.md
│   └── planner.md
└── permissions.yaml      ← tool allowlist/blocklist
```

### plugin.yaml

```yaml
name: my-team-agent-plugin
version: 1.0.0
description: Standard agent setup for our engineering team
requires:
  agent_runtime: ">=1.0.0"
  claude_code_compat: true    # รองรับ CLAUDE.md

memory:
  - memory/AGENT_MEMORY.md

skills:
  - skills/code-review
  - skills/deploy-checklist

hooks:
  pre_tool_use:
    - hooks/pre-commit.sh
  post_edit:
    - hooks/post-edit-format.sh

agents:
  - agents/explorer.md
  - agents/planner.md

permissions:
  allowed_tools:
    - read_file
    - write_file
    - bash
    - search
  blocked_tools:
    - delete_file
  code_intelligence: true

settings:
  inherit_user_settings: true
  propagate_cli_args: false    # CLI args ไม่ทะลุเข้า plugin
```

### Governance ที่ Plugin ทำได้
- กำหนด tool allowlist/blocklist ระดับ org
- แจก approved subagents ให้ทีม
- ฝัง standard hooks (formatting, auditing)
- Deploy memory policy ส่วนกลางให้ทุก machine
- Version control พฤติกรรม agent ทั้งองค์กร

---

## 7. External Connectivity — MCP Layer

### วัตถุประสงค์
เชื่อม agent กับระบบภายนอก (databases, APIs, tools) ผ่าน Model Context Protocol

### MCP Server Configuration

```yaml
# .agent/mcp-servers.yaml
servers:
  - name: github
    command: npx @modelcontextprotocol/server-github
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}

  - name: postgres
    command: npx @modelcontextprotocol/server-postgres
    env:
      DATABASE_URL: ${DATABASE_URL}

  - name: filesystem
    command: npx @modelcontextprotocol/server-filesystem
    args:
      - /workspace

  - name: slack
    command: npx @modelcontextprotocol/server-slack
    env:
      SLACK_TOKEN: ${SLACK_TOKEN}
```

---

## 8. Layer 0 — Runtime & Tooling Foundation

> Layer พื้นฐานที่รองรับการทำงานของ Layer 1–5 ทั้งหมด (ไม่เปลี่ยน 5-layer หลัก)

### 8.1 Computer Use / Browser Control
- รองรับ screenshot, click, type, scroll
- ใช้สำหรับ UI testing, web research, form automation

### 8.2 Artifact Management
- จัดการ output artifacts (ไฟล์, report, code snippet)
- Version artifact และ link กลับไปยัง task ที่สร้าง

### 8.3 Context Management Strategy

| กลยุทธ์ | เมื่อใช้ |
|---------|---------|
| **Summarization** | เมื่อ conversation ยาวเกิน threshold |
| **Retrieval** | ดึงเฉพาะ context ที่เกี่ยวข้องกับ task ปัจจุบัน |
| **Compaction** | รวม context เก่าให้กระชับก่อน PostCompaction hook fire |

### 8.4 Observability & Governance

```yaml
observability:
  audit_log:
    enabled: true
    path: .agent/logs/audit.jsonl
    fields: [timestamp, event, tool, input_hash, output_hash, user]

  evaluation:
    enabled: true
    metrics: [task_completion, tool_accuracy, context_efficiency]

  human_in_the_loop:
    require_approval_for:
      - delete_file
      - deploy_to_production
      - modify_secrets
    approval_timeout: 60s  # หมดเวลา auto-reject
```

---

## 9. Failure Mode Mapping

| ปัญหาที่พบบ่อย | Layer ที่ขาด | วิธีแก้ |
|---------------|-------------|---------|
| Agent ลืม coding standards ทุก session | Layer 1 (Memory) | เพิ่ม AGENT_MEMORY.md |
| ต้องพิมพ์ instructions เดิมซ้ำๆ | Layer 2 (Skill) | สร้าง Skill สำหรับ task นั้น |
| Agent ทำลาย protected files ได้ | Layer 3 (Hook) | เพิ่ม PreToolUse hook |
| Context รกจาก exploration logs | Layer 4 (Subagent) | delegate ให้ explorer subagent |
| ทีมต้องตั้งค่า agent เองทุกคน | Layer 5 (Plugin) | สร้าง plugin แจกทีม |
| ไม่สามารถ query database ได้ | MCP Layer | เพิ่ม MCP server |
| Subagent ได้รับ permission เกิน | Layer 4 (Subagent) | ตั้ง PreSubagentSpawn hook + restrict tools |
| Hook ไม่ fire ตามที่ตั้งค่า | Layer 3 (Hook) | ตรวจสอบ event binding, ใช้ /doctor |
| Agent loop ไม่สิ้นสุด | Layer 0 (Runtime) | ตั้ง max_iterations + timeout ใน runtime config |

---

## 10. Implementation Checklist

### Minimum Viable Agent (MVP)
- [ ] สร้าง `AGENT_MEMORY.md` และ/หรือ `CLAUDE.md` ที่ project root
- [ ] กำหนด coding standards และ architectural rules
- [ ] สร้าง Skill อย่างน้อย 1 ตัวสำหรับ task ที่ทำซ้ำบ่อยที่สุด
- [ ] ตั้ง `PreToolUse` hook เพื่อป้องกัน destructive operations
- [ ] ตั้ง `PostEdit` hook สำหรับ auto-format
- [ ] สร้าง basic `explore` subagent (read-only)

### Production-Ready Agent
- [ ] **Layer 1:** Memory hierarchy ครบ (global → project → local → auto)
- [ ] **Layer 2:** Skills ครบทุก recurring task, รองรับ skill chaining
- [ ] **Layer 3:** Hooks ครบทุก lifecycle event รวมถึง `OnError`, `PreResponse`
- [ ] **Layer 4:** Subagents แยก read-only vs read-write, รองรับ agent team parallel
- [ ] **Layer 5:** Plugin สำหรับ team distribution พร้อม versioning
- [ ] **MCP:** เชื่อมต่อ external tools ที่จำเป็น
- [ ] **Layer 0:** Audit logging, HITL approval, context management strategy

---

## 11. Design Principles

1. **Prompt ≠ Policy** — สิ่งที่ "ต้องเกิดทุกครั้ง" ต้องเป็น Hook ไม่ใช่ prompt
2. **Lazy load expertise** — โหลด context เข้า agent เฉพาะเมื่อต้องการ ไม่บวมทุก session
3. **Bound delegation** — subagent ต้องมีขอบเขต tool access ที่ชัดเจน ห้าม spawn ซ้อน
4. **Memory is constitutional** — AGENT_MEMORY.md คือ "รัฐธรรมนูญ" ของ agent ไม่ใช่แค่ note
5. **Package for reuse** — behavior ที่ดีต้อง distribute ได้ ไม่ใช่ rebuild ทุกครั้ง
6. **Fail loudly on missing layers** — ระบุชัดว่า layer ไหนขาด แทนที่จะ fallback เป็น prompt
7. **Maintain compatibility** — รองรับทั้ง `AGENT_MEMORY.md` และ `CLAUDE.md` เพื่อให้ทำงานร่วมกับ Claude Code ได้ดีที่สุด

---

*Spec นี้อ้างอิงจาก Anthropic Claude Code Architecture / 5-Layer Agent Development Kit*
*เวอร์ชัน: 2.0 (Revised) | วันที่: พฤษภาคม 2026*
