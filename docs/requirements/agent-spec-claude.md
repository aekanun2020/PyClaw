# Agent Architecture Specification (Claude-Style 5-Layer ADK)

> **วัตถุประสงค์:** เอกสารนี้คือ specification สำหรับ AI ที่จะสร้างระบบ Agent แบบ Claude Code Architecture โดยอ้างอิงจาก 5-Layer Agent Development Kit ของ Anthropic

---

## 1. Overview

ระบบ Agent ที่ต้องสร้างต้องประกอบด้วย 5 เลเยอร์ที่ทำงานร่วมกัน แต่ละเลเยอร์แก้ปัญหาเฉพาะที่ prompt อย่างเดียวไม่สามารถแก้ได้

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
└─────────────────────────────────────────────────────┘
```

---

## 2. Layer 1 — Memory & Policy Layer

### วัตถุประสงค์
เก็บบริบทถาวรของโปรเจกต์และนโยบายที่ต้องโหลดทุก session โดยไม่ต้องพิมพ์ซ้ำ

### ไฟล์หลัก
- `AGENT_MEMORY.md` — กฎ/นโยบายที่มนุษย์เขียนเอง (declarative)
- `AUTO_MEMORY.md` — บันทึกที่ agent เขียนสะสมเอง (emergent, adaptive)

### พฤติกรรมที่ต้องสร้าง

| พฤติกรรม | รายละเอียด |
|----------|------------|
| **Directory walking** | Agent ต้อง scan หา memory file จาก working dir ขึ้นไปถึง root แล้ว concat ทั้งหมด (ไม่ override กัน) |
| **Scope inheritance** | ไฟล์ที่อยู่ไกลจาก root = กฎ global, ไฟล์ใกล้ working dir = กฎ specific ที่อ่านทีหลัง (override ได้) |
| **Import support** | รองรับ `@path/to/file` syntax ให้แตก memory ออกเป็นโมดูลย่อยได้ recursive สูงสุด 5 hops |
| **Auto memory limit** | AUTO_MEMORY โหลดแค่ 200 บรรทัดแรก หรือ 25 KB (กันบวมเกิน) |
| **Full load for human memory** | AGENT_MEMORY.md โหลดเต็มทุกครั้ง ไม่จำกัด (แต่แนะนำให้สั้น เพื่อ adherence ดี) |
| **Org-level deployment** | รองรับ path กลางของ organization ให้ deploy memory file ให้ทุกคนในทีมได้ |

### สิ่งที่ต้องเก็บใน Memory
- Coding standards, naming conventions
- Architecture decisions & preferred libraries
- Review checklists
- Workflow constraints ที่ต้องทำทุกครั้ง
- ข้อมูลที่ agent ควรรู้ตั้งแต่เริ่ม session

### สิ่งที่ไม่ควรเก็บใน Memory
- ขั้นตอน step-by-step ที่ใช้เฉพาะงาน → ใส่ใน Skill แทน
- Logic ที่ต้อง enforce แบบ deterministic → ใส่ใน Hook แทน

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
| **Invocation modes** | รองรับทั้ง (1) user เรียกผ่าน slash command `/skill-name` และ (2) agent detect เองว่า task match แล้ว auto-load |
| **Runtime matching** | Agent ต้องอ่าน description ของทุก skill และประเมินว่า task ปัจจุบัน match skill ใด |
| **Subagent execution** | Skill บางตัวสามารถ route ไปรันใน subagent แยกได้ |
| **Context injection** | Skill สามารถ inject context เพิ่มเติม (เช่น environment vars, file contents) ณ เวลา invocation |

### เมื่อไหร่ควรสร้าง Skill (ไม่ใช่ Memory)
- เมื่อคุณพบว่าต้องวาง instructions เดิมซ้ำหลายครั้ง
- เมื่อ AGENT_MEMORY.md เริ่มเป็น step-by-step procedure แทนที่จะเป็น fact/rule
- เมื่อมีขั้นตอนที่ซับซ้อนเฉพาะงาน เช่น debug workflow, deploy checklist

### โครงสร้าง SKILL.md

```markdown
---
name: debug-flaky-tests
description: ใช้เมื่อต้องการ debug test ที่ fail แบบ intermittent
invocation: auto  # หรือ manual
subagent: false   # หรือ true
---

## วัตถุประสงค์
[อธิบายว่า skill นี้ทำอะไร]

## ขั้นตอน
1. ...
2. ...

## Reference
@./reference.md
```

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

### Hook Events ที่ต้องรองรับ

| Event | เวลาที่ fire | ตัวอย่างการใช้ |
|-------|-------------|----------------|
| `PreToolUse` | ก่อน agent ใช้ tool | กรอง/แก้ไข input, บล็อก tool call ที่อันตราย |
| `PostToolUse` | หลัง agent ใช้ tool | auto-format, log output, transform result |
| `PostEdit` | หลัง agent แก้ไขไฟล์ | lint, format, reload env |
| `PreSession` | เริ่ม session | inject context, validate config |
| `PostCompaction` | หลัง context ถูก compact | re-inject critical context กลับเข้าไป |

### โครงสร้าง Hook

```json
// input payload ที่ hook รับ
{
  "event": "PreToolUse",
  "tool": "bash",
  "input": {
    "command": "pytest tests/ -v"
  },
  "context": { ... }
}

// output ที่ hook ส่งกลับ
{
  "action": "modify",       // หรือ "block", "allow", "notify"
  "modified_input": {
    "command": "pytest tests/ -v 2>&1 | grep FAILED"  // กรองเฉพาะ failure
  },
  "reason": "Filtered to failing tests only to reduce token usage"
}
```

### Use Cases สำคัญ

- **Auto-format:** PostEdit hook รัน `prettier` / `black` ทุกครั้งที่แก้ไฟล์
- **Block protected files:** PreToolUse ป้องกันการแก้ไฟล์ sensitive เช่น `.env`, `secrets/`
- **Token optimization:** PreToolUse กรอง log ขนาดใหญ่ก่อนส่งให้ agent เห็น
- **Audit trail:** PostToolUse บันทึกทุก tool invocation ลง audit log
- **Context preservation:** PostCompaction re-inject project rules กลับเข้า context

### กฎการใช้ Hook
> Hook คือเครื่องมือเมื่อ "ต้องเกิดขึ้นทุกครั้ง ไม่มีข้อยกเว้น" — ไม่ใช่เมื่อ "ส่วนใหญ่ควรทำ"

---

## 5. Layer 4 — Delegation / Subagent Layer

### วัตถุประสงค์
แบ่งงานออกจาก main agent ไปยัง worker agent ที่รันใน context แยก เพื่อป้องกัน context pollution และเพิ่ม specialization

### ปัญหาที่ Subagent แก้ไข

| ปัญหา | วิธีแก้ด้วย Subagent |
|-------|---------------------|
| Main context ยาวและรกจาก exploration logs | Subagent รันงาน exploratory แยก ส่งแค่ result กลับ |
| ต้องการ agent เฉพาะทาง | Subagent มี system prompt, tool access, model เฉพาะของตัวเอง |
| Cost optimization | Route งานง่ายไปยัง cheaper model (เช่น Haiku) |
| Security isolation | Subagent ถูก จำกัด tool access ตาม role |

### Built-in Subagent Types (ตัวอย่าง)

| Subagent | Tool Access | วัตถุประสงค์ |
|---------|------------|-------------|
| `explore` | Read-only | วิเคราะห์ codebase โดยไม่แก้ไขอะไร |
| `plan` | Read + write notes | วางแผนงานก่อน execute |
| `general` | Full tools | รองรับงานซับซ้อน multi-step |

### โครงสร้าง Subagent Definition

```markdown
---
name: code-explorer
description: Reads and analyzes codebase without making any changes
model: claude-haiku-4  # เลือก model ที่ถูกกว่าสำหรับงานนี้
tools:
  - read_file
  - search_files
  - run_command: readonly
memory: inherit  # หรือ isolated
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
3. **Inherit then restrict** — subagent รับ permission จาก parent แล้ว restrict เพิ่ม (ไม่ expand ได้)
4. **Isolated context** — งานใน subagent ไม่ปนเปื้อน main context

### Subagent Storage Scopes

```
~/.agent/agents/          ← user-level (ใช้ได้ทุกโปรเจกต์)
.agent/agents/            ← project-level (เฉพาะโปรเจกต์นี้)
--subagent flag           ← session-only (ใช้ครั้งเดียว)
plugins/                  ← plugin-distributed (แจก teamwide)
```

---

## 6. Layer 5 — Distribution / Plugin Layer

### วัตถุประสงค์
แพ็กเกจ behavior ทั้งหมด (memory rules, skills, hooks, subagents) ให้ติดตั้งและใช้งานร่วมกันได้ทั้งทีม โดยไม่ต้อง rebuild ทุกครั้ง

### สิ่งที่ Plugin ประกอบด้วย

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

### plugin.yaml โครงสร้าง

```yaml
name: my-team-agent-plugin
version: 1.0.0
description: Standard agent setup for our engineering team

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
    - delete_file  # ต้อง approve ด้วยตัวเอง
  code_intelligence: true

settings:
  inherit_user_settings: true
  propagate_cli_args: false  # CLI args ไม่ทะลุเข้า plugin
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

### MCP Server Structure

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
```

---

## 8. Failure Mode Mapping

| ปัญหาที่พบบ่อย | Layer ที่ขาด | วิธีแก้ |
|---------------|-------------|---------|
| Agent ลืม coding standards ทุก session | Layer 1 (Memory) | เพิ่ม AGENT_MEMORY.md |
| ต้องพิมพ์ instructions เดิมซ้ำๆ | Layer 2 (Skill) | สร้าง Skill สำหรับ task นั้น |
| Agent ทำลาย protected files ได้ | Layer 3 (Hook) | เพิ่ม PreToolUse hook |
| Context รกจาก exploration logs | Layer 4 (Subagent) | delegate ให้ explorer subagent |
| ทีมต้องตั้งค่า agent เองทุกคน | Layer 5 (Plugin) | สร้าง plugin แจกทีม |
| ไม่สามารถ query database ได้ | MCP Layer | เพิ่ม MCP server |

---

## 9. Implementation Checklist

### Minimum Viable Agent (MVP)
- [ ] สร้าง `AGENT_MEMORY.md` ระดับ project root
- [ ] กำหนด coding standards และ architectural rules
- [ ] สร้าง skill อย่างน้อย 1 ตัวสำหรับ task ที่ทำซ้ำบ่อยที่สุด
- [ ] ตั้ง PreToolUse hook เพื่อป้องกัน destructive operations

### Production-Ready Agent
- [ ] Layer 1: Memory hierarchy ครบ (global → project → local)
- [ ] Layer 2: Skills ครบทุก recurring task
- [ ] Layer 3: Hooks ครบทุก critical lifecycle event
- [ ] Layer 4: Subagents แยก read-only vs read-write
- [ ] Layer 5: Plugin สำหรับ team distribution
- [ ] MCP: เชื่อมต่อ external tools ที่จำเป็น

---

## 10. Design Principles

1. **Prompt ≠ Policy** — สิ่งที่ "ต้องเกิดทุกครั้ง" ต้องเป็น Hook ไม่ใช่ prompt
2. **Lazy load expertise** — โหลด context เข้า agent เฉพาะเมื่อต้องการ ไม่บวมทุก session
3. **Bound delegation** — subagent ต้องมีขอบเขต tool access ที่ชัดเจน ห้าม spawn ซ้อน
4. **Memory is constitutional** — AGENT_MEMORY.md คือ "รัฐธรรมนูญ" ของ agent ไม่ใช่แค่ note
5. **Package for reuse** — behavior ที่ดีต้อง distribute ได้ ไม่ใช่ rebuild ทุกครั้ง
6. **Fail loudly on missing layers** — ระบุชัดว่า layer ไหนขาด แทนที่จะ fallback เป็น prompt

---

*Spec นี้อ้างอิงจาก Anthropic Claude Code Architecture / 5-Layer Agent Development Kit*
*เวอร์ชัน: 1.0 | วันที่: พฤษภาคม 2026*
