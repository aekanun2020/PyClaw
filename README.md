# PyClaw

> **PyClaw = EliteClaw ที่เขียนใหม่ด้วย Python + ฟีเจอร์เพิ่มเติม**
> เป็น agent runtime แนว **deterministic-first** ที่อิงตาม **5-Layer ADK Spec**
> หลักการสำคัญ: **Prompt ≠ Policy** — ตรรกะที่ต้องแน่นอนทุกครั้งอยู่ใน **Hooks (โค้ด)** ไม่ใช่ใน prompt ของ LLM

PyClaw เป็นรุ่นต่อยอดด้วย Python ของ [EliteClaw](https://github.com/aekanun2020/EliteClaw) (private, TypeScript v7.0.0)
โดยคงจุดแข็งเดิมไว้ (Orchestrator → Specialized Agents, custom MCP clients, รูปแบบ SOUL/TOOLS/SKILL.md,
LLM ผ่าน OpenRouter) และ **อุดช่องว่าง** ที่ EliteClaw ยังขาดเมื่อเทียบกับ ADK Spec

เอกสารนี้อธิบายฟีเจอร์ที่อยู่บน `main` ปัจจุบัน ครอบคลุม 4 ส่วนหลัก:
สถาปัตยกรรม (agent loop), Orchestrator + AGENTS.md, `--trace`/`--subagents`,
และ streaming + persistence + `--resume`

---

## 1. ภาพรวมสถาปัตยกรรม (Layers / Agent Loop)

### Pipeline และความลึก (depth)

PyClaw บังคับให้สายการทำงานลึกได้ไม่เกิน 1 ระดับของ agent — ไม่มี orchestration ซ้อน orchestration:

```
depth 0          depth 1                         depth 2
┌──────────────┐   route_to_agent   ┌───────────────────┐   tool call   ┌─────────────┐
│ Orchestrator │ ─────────────────▶ │ Specialized Agent │ ────────────▶ │  Tool / MCP │
│ (route only) │                    │ (db-agent /       │               │ (db_*,      │
└──────────────┘                    │  pdpa-agent)      │               │  pdpa_* …)  │
                                     └───────────────────┘               └─────────────┘
```

- **depth 0 — Orchestrator**: มีเครื่องมือเดียวคือ `route_to_agent` ไม่มี domain tool ใด ๆ
- **depth 1 — Specialized Agent**: รัน AgentLoop แยกอิสระ (context สดของตัวเอง) มีเฉพาะกลุ่มเครื่องมือที่ AGENTS.md อนุญาต
- **depth 2 — Tool calls**: เรียกเครื่องมือจริง (MCP) เท่านั้น

Specialized agent เป็น subagent แบบไม่ซ้อน จึง **spawn agent ต่อไม่ได้** (กันไว้ทั้ง `is_nested` guard
และการถอดเครื่องมือ spawn ออกจากชุดเครื่องมือลูก — ดู `pyclaw/subagents/runner.py`)

### Core Agent Loop

ลูปหลักอยู่ที่ `pyclaw/core/loop.py` (`AgentLoop.run` → `_run`) ทำงานเป็นรอบ
(จำกัดด้วย `SETTINGS.max_tool_rounds`) ตามขั้นตอนของ agentic loop:

```
intake → context assembly → model inference → tool execution → streaming reply → persistence
```

ลำดับจริงต่อหนึ่งรอบใน `_run`:

1. `ctx.maybe_compact()` — บีบ context ถ้าจำเป็น (ยิง hook `PostCompaction` เมื่อบีบจริง)
2. เรียก LLM: `complete_stream(...)` เมื่อมี `on_delta` (สตรีม) มิฉะนั้น `complete(...)`
3. ถ้าไม่มี tool call → ผ่าน hook `PreResponse` (`_finalize`) แล้วคืนข้อความสุดท้าย
4. ถ้ามี tool call → เรียก `_invoke_tool()` ทีละตัว

`_invoke_tool()` คือจุดควบคุม deterministic จุดเดียวของทุก tool call ตามลำดับ:

1. **L5 Permission** — `PermissionPolicy.is_allowed(name)` (fail-closed)
2. **L3 PreToolUse hook** — `ALLOW / MODIFY args / BLOCK / NOTIFY`
3. **L0 HITL** — ขออนุมัติถ้าเครื่องมืออยู่ในรายการ `require_approval_for`
4. ตั้งค่า trace observer ผ่าน contextvars แล้ว **execute** ผ่าน `ToolRegistry.dispatch`
5. **L3 PostToolUse hook** — แก้ผลลัพธ์ได้
6. **L0 Audit** — บันทึก `audit.record(...)` ทุกครั้ง
7. ต่อผลลัพธ์กลับเข้า context

> เครื่องมือจะ **ไม่มีทาง** ถูกรันตรงจากผลของโมเดล — ต้องผ่าน permission + PreToolUse hook ก่อนเสมอ

### โมดูลที่เกี่ยวข้อง

| บทบาท | โมดูล | คลาส/ฟังก์ชันหลัก |
|-------|-------|-------------------|
| Agent loop | `pyclaw/core/loop.py` | `AgentLoop.run` / `_run` / `_invoke_tool` |
| LLM provider | `pyclaw/core/llm.py` | `OpenRouterProvider` (`complete` / `complete_stream`) |
| Tool registry | `pyclaw/core/tools.py` | `Tool` / `ToolRegistry` (`dispatch`, `llm_specs`) |
| Orchestrator | `pyclaw/orchestrator/*` | `load_agents` / `OrchestratorRunner` / `route_to_agent` |
| Subagents | `pyclaw/subagents/*` | `SubagentRunner` / `ParallelTeam` / trace bridge |

---

## 2. Orchestrator + AGENTS.md (auto-routing)

### เปิดใช้งาน

```bash
pyclaw chat --orchestrator
```

- ธง `--orchestrator` อยู่บน subcommand **`chat`** และ **ปิดไว้โดยปริยาย (OFF)**
- แบนเนอร์ตอนเริ่มแสดงสถานะ `orchestrator: on` / `orchestrator: off`
- เมื่อปิดอยู่ AGENTS.md จะ **ไม่ถูกโหลด** และลูปแชตแบบ flat ทำงานตามปกติ

### Orchestrator เป็นเจ้าของเครื่องมือเดียว

ใน orchestrator mode (`pyclaw/cli.py::_build_orchestrator_loop`) loop ของ orchestrator
ได้ registry ที่มีเฉพาะ meta-tool `route_to_agent` — **ไม่มี domain tool**
ส่วนเครื่องมือ MCP จริงถูก mount ลง registry แยก (`domain_tools`) ที่หล่อเลี้ยง specialized agent
ผ่าน `tool_provider` ดังนั้น orchestrator เองเรียกเครื่องมือจริงไม่ได้เลย ทำได้แค่ "route"

system prompt สั่งให้ orchestrator วิเคราะห์ intent ของผู้ใช้แล้วเลือก agent ที่เหมาะ
และห้ามตอบเอง — งานจริงทั้งหมดต้องไปผ่านการ routing

### Parallel (Pattern A) vs Sequential (Pattern B)

`route_to_agent` (ดู `pyclaw/orchestrator/tool.py`) รับได้ทั้ง route เดียวและหลาย route:

| รูปแบบ | วิธีเรียก | พฤติกรรม |
|--------|-----------|----------|
| Single | `route_to_agent(agent="db-agent", message="...")` | ส่งงานเดียวให้ agent เดียว |
| Parallel (A) | `route_to_agent(routes=[{agent, message}, …], mode="parallel")` | รัน **พร้อมกัน** ผ่าน thread pool ผลเรียงตาม input |
| Sequential (B) | `route_to_agent(routes=[…], mode="sequential")` | รัน **ตามลำดับ** ป้อนผลของ agent ก่อนหน้าเป็น context ให้ตัวถัดไป |

`mode` มีค่า default เป็น `parallel` LLM เป็นผู้เลือกโหมดตามเจตนา:

- คำถามย่อยที่ **เป็นอิสระต่อกัน** → `mode="parallel"` (รันพร้อมกัน, `route_parallel`)
- agent B **ต้องใช้ผลของ** agent A → `mode="sequential"` (ป้อนผลก่อนหน้าไปข้างหน้า, `route_sequential`)
- ผู้ใช้ระบุ **ลำดับชัดเจน** → `mode="sequential"`

### AGENTS.md — แหล่งความจริง (source of truth)

ไฟล์ `AGENTS.md` อยู่ที่ root ของ repo และถูก parse โดย
`pyclaw/orchestrator/registry.py::load_agents` ซึ่งเดินไล่ขึ้นจาก working dir เพื่อหา AGENTS.md
ที่ root ให้เจอ รูปแบบไฟล์คือ **บล็อก frontmatter หลายบล็อกคั่นด้วย `---`** (กติกาเดียวกับ SKILL.md)
แต่ละบล็อก = หนึ่ง agent คีย์ที่รองรับ:

| คีย์ | ความหมาย |
|------|----------|
| `name` | id ที่ใช้ใน `route_to_agent(agent=...)` |
| `description` | ข้อความ "เมื่อไรควรใช้" คัดลอกตรง ๆ เข้า routing prompt |
| `tools` | prefix ของชื่อเครื่องมือ คั่นด้วยจุลภาค — เครื่องมือจะถูกมอบให้ agent เมื่อชื่อ **ขึ้นต้นด้วย** prefix ใด prefix หนึ่ง (เช่น `db_` ตรงกับ `db_execute_query_tool`) |

ข้อความเดียวกันนี้ถูกใช้สร้าง **ทั้งสองอย่าง**: routing prompt (จาก `name` + `description`)
และกลุ่มเครื่องมือที่แต่ละ agent ใช้ได้ (จากการ match prefix ของ `tools` กับ registry จริง)
agent จึงได้รับเฉพาะ callable จริงเท่านั้น

### สอง agent ปัจจุบัน

```
---
name: db-agent
description: Read-only queries against TestDB, an HR system with 9 tables. …
tools: db_
---

---
name: pdpa-agent
description: Thai PDPA (Personal Data Protection Act) law question-and-answer. …
tools: pdpa_
---
```

- **db-agent** (`db_*`): สอบถาม HR database (TestDB) แบบอ่านอย่างเดียว เขียน/แก้ข้อมูลไม่ได้
- **pdpa-agent** (`pdpa_*`): ถาม-ตอบกฎหมาย PDPA ของไทย

### วิธีเพิ่ม agent ใหม่

เพิ่มบล็อก frontmatter อีกหนึ่งบล็อกใน `AGENTS.md` ไม่ต้องแตะโค้ด เช่น:

```
---
name: docs-agent
description: Answers questions about internal documentation and runbooks.
tools: docs_
---
```

orchestrator จะอ่านบล็อกใหม่ตอนเริ่มทำงาน สร้าง routing prompt และผูก prefix `docs_`
เข้ากับเครื่องมือใน registry ให้อัตโนมัติ (ถ้า registry ภายใต้ `--orchestrator` ว่างเปล่าจะ error ทันที — fail loudly)

### ตัวอย่างจริง (พิสูจน์แล้วบนเครื่องผู้ใช้)

**Single routing**

```
you> ใน TestDB มีพนักงานกี่คน
```

routing: `route_to_agent({"agent":"db-agent", ...})` → จากนั้นบรรทัด trace ขึ้นต้นด้วย `[db-agent]`
แล้วเรียก `db_execute_query_tool` — คำตอบ: **25 คน**

**Parallel routing**

```
you> ช่วยบอก 2 อย่าง: ใน TestDB มีกี่ตาราง และ PDPA มีบทลงโทษอะไรบ้าง
```

LLM เลือก `mode=parallel` ส่งไป **db-agent + pdpa-agent พร้อมกัน**
ใน `--trace` จะเห็นบรรทัด `[db-agent]` และ `[pdpa-agent]` สลับกันไปมา (interleave) — หลักฐานตรงว่ารันขนานจริง
คำตอบ: ตารางทั้งหมด **9 ตาราง** + บทลงโทษ PDPA (อาญาสูงสุดจำคุก 1 ปี / ปรับ 1 ล้านบาท,
ปกครองสูงสุด 5 ล้านบาท, แพ่งชดใช้ค่าสินไหมได้ถึง 2 เท่า)

---

## 3. `--trace` และ `--subagents`

### `--trace` — ดู tool call สด ๆ

```bash
pyclaw chat --trace
```

- **ปิดไว้โดยปริยาย (OFF)** เพื่อความปลอดภัยด้าน PDPA/PII (ผลลัพธ์อาจมีข้อมูลส่วนบุคคล;
  audit log เก็บแค่ hash จึงต้อง opt-in หากต้องการเห็นข้อมูลเต็ม)
- พิมพ์ลง **stderr** ในรูปแบบ:
  - `→ call  NAME(args)`
  - `← return NAME  [N.NNs]  result`
- ผลลัพธ์ถูกตัดที่ราว ~2000 ตัวอักษร (ส่วนเกินแสดงเป็น `…(+N chars)`)
- เป็น **observer ล้วน ๆ** ไม่เปลี่ยนการควบคุม fire รอบ ๆ การ dispatch
  **หลังจาก** ผ่าน permission/hook/HITL แล้ว
- แบนเนอร์แสดง `trace: on` / `trace: off`

**การส่งต่อเข้า subagent / routed agent** — ทำผ่าน contextvars bridge ที่
`pyclaw/subagents/trace.py` (`set_active_on_tool` / `get_active_on_tool`): loop เผยแพร่ observer
รอบการ dispatch แล้วเครื่องมือ `spawn_subagent` / `route_to_agent` หยิบไปต่อเข้าลูปลูก
แต่ละ tool call จึงถูกติดป้าย:

- `[sub#N]` สำหรับ subagent (ตั้งโดย `ParallelTeam`, N เริ่มที่ 1 ตามลำดับ input)
- `[db-agent]` / `[pdpa-agent]` สำหรับ routed agent ของ orchestrator

ตัว tracer ใช้ `threading.Lock` ครอบการเขียนทีละบรรทัดให้สมบูรณ์ ดังนั้นบรรทัดจากหลาย thread
**ไม่ฉีกกลางบรรทัด** (การที่ทั้งบรรทัด interleave กันคือหลักฐานภาพตรง ๆ ของการรันขนาน)

### `--subagents` — ให้โมเดลกระจายงาน

```bash
pyclaw chat --subagents
```

- **ปิดไว้โดยปริยาย (OFF)** (การ spawn agent เพิ่มทำให้ค่า LLM เพิ่ม จึงต้องตั้งใจเปิด)
- ลงทะเบียนเครื่องมือ `spawn_subagent` (`pyclaw/subagents/tool.py`) ให้โมเดลเรียกได้
- ชนิด subagent: `explore` (อ่านอย่างเดียว), `plan` (วางแผน ไม่ลงมือ), `review` (ตรวจ/วิจารณ์),
  `general` (ทั่วไป) — แต่ละชนิดจำกัดกลุ่มเครื่องมือต่างกัน (inherit-then-restrict)
- รับได้ทั้ง `objective` เดียว หรือ list `objectives` หลายตัว → รันขนานผ่าน `ParallelTeam`
- subagent ได้รับ **เครื่องมือจริงของ parent** ผ่าน `tool_provider` (callable จริง ไม่ใช่การ hallucinate)
  โดยถูก allowlist ตามชื่อที่อนุญาตเท่านั้น
- subagent **spawn ต่อไม่ได้** (ถอดเครื่องมือ spawn ออกจากลูก + ตรวจ `is_nested`)
- แบนเนอร์แสดง `subagents: on` / `subagents: off`

> หมายเหตุ: `--orchestrator` กับ `--subagents` ใช้ร่วมกันไม่ได้ — เมื่อเปิด orchestrator
> ระบบจะข้าม `--subagents` เพราะ routed agent ก็คือ subagent ที่ถูกกำหนดไว้แล้วโดย AGENTS.md

---

## 4. Streaming + Persistence + `--resume`

ทั้งหมดนี้อยู่ในคำสั่ง `pyclaw chat` (แชตหลายเทิร์น) ซึ่งสร้าง loop และ mount MCP **ครั้งเดียว**
แล้วใช้ context เดิมข้ามทุกเทิร์น

### Streaming

- โทเค็นถูกสตรีมออกมาทันทีที่ได้รับ (เปิดโดยปริยาย; ปิดด้วย `--no-stream`)
- แบนเนอร์แสดง `streaming: on` / `streaming: off`
- กลไก: `OpenRouterProvider.complete_stream` อ่าน Server-Sent Events (SSE) ผ่าน `httpx.stream`
  สะสม text delta และ tool-call fragment ทีละชิ้น เมื่อจบเทิร์นผลเหมือน path แบบไม่สตรีมทุกประการ
  (มี fallback ไปใช้ `complete` แบบ non-stream เมื่อไม่มี `on_delta`)

### Persistence

- ทุกเทิร์นของแชตถูกบันทึกอัตโนมัติลง `.agent/sessions/<id>.json`
  (`pyclaw/runtime/session.py::SessionStore.save` เขียนแบบ atomic: เขียน `.tmp` แล้ว `os.replace`)
- แบนเนอร์แสดง path ที่บันทึก เช่น `session saved to: .agent/sessions/<id>.json`
- envelope เก็บ `id`, `created_at`, `updated_at`, และ `messages` ทั้งหมด
  ประวัติจึงคงอยู่ทั้งข้ามเทิร์นและข้ามการรีสตาร์ตโปรเซส
- ไฟล์ session ที่เสียหายจะ **fail loudly** ไม่เริ่มแชตเปล่าเงียบ ๆ

### `--resume`

```bash
pyclaw chat --resume SESSION_ID
```

- `--resume` รับ **session id** (metavar `SESSION_ID`) ตรง ๆ — โหลดจาก `.agent/sessions/<id>.json`
  เข้า context ผ่าน `SessionStore.load_into` แล้วทำแชตต่อ (ไม่ใช่การ resume เทิร์นล่าสุดอัตโนมัติ)
- เมื่อ resume สำเร็จจะพิมพ์ `[session] resumed <id> (N prior turn(s))`
- id ถูกตรวจกัน path traversal (ห้ามมี `/`, `\`, หรือเป็น `.`/`..`)
- ดูรายการ id ทั้งหมดได้จากไฟล์ใน `.agent/sessions/` (ตั้งชื่อแบบ timestamp นำหน้า เรียงใหม่ก่อน)

---

## วิธีรัน

```bash
pip install -e .

# 1) LLM key
export OPENROUTER_API_KEY="sk-or-..."

# 2) MCP server ของคุณ (รองรับรูปแบบ EliteClaw) — เพิ่มได้หลายตัว
#    URL ที่ลงท้าย /mcp จะถูกตรวจเป็น Streamable HTTP; มิฉะนั้นเป็น SSE แบบคลาสสิก
#    (ชี้ URL ไปที่ path event-stream มักเป็น /sse)
export MCP_SERVER_1_URL="http://127.0.0.1:9000/sse"
export MCP_SERVER_1_NAME="mssql"
export MCP_SERVER_1_PREFIX="db_"      # เครื่องมือกลายเป็น db_<toolname>

# 3) งานแบบ one-shot
pyclaw run "your task here"

# 4) แชตหลายเทิร์น (streaming + persistence) พร้อมธงตามต้องการ
pyclaw chat
pyclaw chat --orchestrator              # auto-route ไป db-agent / pdpa-agent
pyclaw chat --trace                     # เห็น tool call สด ๆ (อาจเผย PII)
pyclaw chat --subagents                 # เปิดเครื่องมือ spawn_subagent
pyclaw chat --no-stream                 # ปิดการสตรีมโทเค็น
pyclaw chat --resume SESSION_ID         # ทำแชตเดิมต่อ

# ตรวจการเชื่อมต่อทุก layer
pyclaw doctor
```

> ธง `--orchestrator`, `--trace`, `--subagents`, `--no-stream`, `--resume` ทั้งหมดอยู่บน
> subcommand **`chat`** เท่านั้น (`pyclaw run` เป็น one-shot ไม่มีธงเหล่านี้)

ทุกเครื่องมือ MCP ผ่าน hook engine, permission policy และ audit log เหมือนเครื่องมือ built-in
server ที่ติดต่อไม่ได้จะถูกข้ามพร้อม warning (ตั้ง `PYCLAW_MCP_STRICT=1` เพื่อให้ fail แทน)

มี `.env` แบบ EliteClaw/OpenClaw อยู่แล้ว? ใช้ซ้ำได้เลย:

```bash
PYCLAW_DOTENV=/path/to/your/.env pyclaw run "your task here"
```

PyClaw อ่านไฟล์นั้นทั้งสำหรับ MCP servers (`MCP_SERVER_1_*`, …) และตั้งค่า LLM:
`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` (เช่น Ollama ที่ `http://host:11434/v1` โดยคีย์เป็น `ollama`)
และโมเดลจาก `OPENROUTER_MODEL` — รัน `PYCLAW_DOTENV=/path/.env pyclaw doctor` ก่อนเพื่อดูว่าเจอ server อะไรบ้าง

### เลือก LLM backend (3 แบบ)

เพราะ `OPENROUTER_BASE_URL` ตั้งค่าได้ PyClaw จึงชี้ไปที่ endpoint แบบ **OpenAI-compatible**
ตัวไหนก็ได้โดยไม่ต้องแก้โค้ด — เปลี่ยนแค่ env vars สามตัว (base URL, API key, model)
ตัวอย่างด้านล่างใช้ `export` ใน shell (ตั้งค่าชั่วคราว) ไม่ต้องแก้ `.env`

**(A) OpenRouter (cloud, จ่ายเงิน)** — คุณภาพสูงสุด:

```bash
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
export OPENROUTER_API_KEY="sk-or-..."        # คีย์จาก openrouter.ai/keys
export PYCLAW_DEFAULT_MODEL="anthropic/claude-sonnet-4.6"
```

**(B) Ollama (local, ฟรี)** — รันบนเครื่องตัวเอง:

```bash
export OPENROUTER_BASE_URL="http://localhost:11434/v1"   # หรือ host ของเครื่องที่รัน Ollama
export OPENROUTER_API_KEY="ollama"                       # ค่าพิเศษ -> ข้าม auth header
export PYCLAW_DEFAULT_MODEL="<ชื่อโมเดลใน Ollama>"
```

ค่า API key ที่เป็น `ollama`, `none`, หรือ `local` (รวมถึงค่าว่าง) จะทำให้ provider
**ข้าม** การส่ง Authorization header — backend แบบ local/Ollama จึงใช้งานได้โดยไม่ต้องมีคีย์จริง
(ดู `pyclaw/core/llm.py`)

**(C) Google Gemini (ฟรีผ่าน Google AI Studio)** — ใช้ endpoint OpenAI-compatible ของ Google:

```bash
export OPENROUTER_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
export OPENROUTER_API_KEY="<Gemini API key จาก aistudio.google.com>"
export PYCLAW_DEFAULT_MODEL="gemini-2.5-flash"   # หรือ gemini-2.5-pro (ฟรี); gemini-3.1-pro-preview ไม่ฟรี
```

> Gemini บน OpenRouter เสียเงินทุกรุ่น แต่ผ่าน Google AI Studio มี free tier (rate limit จำกัด);
> เพราะ base URL ตั้งค่าได้ จึงชี้ไปที่ endpoint OpenAI-compat ของ Google ได้โดยไม่ต้องแก้โค้ด

**ลำดับความสำคัญของโมเดล:** ถ้าตั้งทั้ง `PYCLAW_DEFAULT_MODEL` และ `OPENROUTER_MODEL`,
ค่า `PYCLAW_DEFAULT_MODEL` จะถูกใช้ (override) — ถ้าไม่ได้ตั้งทั้งคู่จะ fallback ไปที่ default ที่ฝังในโค้ด
(ดู `pyclaw/config.py`)

> เคล็ดลับ: การ `export` ใน shell ใหม่เป็นการตั้งค่าชั่วคราว ปิด terminal แล้วกลับค่าเดิม
> เหมาะกับการทดลองสลับโมเดล (A/B) โดยไม่ต้องแก้ `.env`

### โหมดรัน (run modes) — สรุปธงทั้งหมด

| คำสั่ง | ความหมาย | ค่าเริ่มต้น |
|--------|----------|-------------|
| `pyclaw run "task"` | งานแบบ one-shot รอบเดียว ไม่มีธงของ chat | — |
| `pyclaw chat` | แชตหลายเทิร์น (streaming + persistence) | streaming ON |
| `pyclaw chat --orchestrator` | auto-route ไป specialized agents ตาม AGENTS.md | OFF |
| `pyclaw chat --trace` | เห็น tool call สด ๆ (อาจเผย PII) | OFF |
| `pyclaw chat --subagents` | เปิดเครื่องมือ `spawn_subagent` (ใช้ร่วมกับ `--orchestrator` ไม่ได้) | OFF |
| `pyclaw chat --no-stream` | ปิดการสตรีมโทเค็น | streaming ON |
| `pyclaw chat --resume SESSION_ID` | ทำแชตเดิมต่อจาก `.agent/sessions/<id>.json` | — |
| `pyclaw doctor` | ตรวจ config + การเชื่อมต่อทุก layer | — |

---

## โครงสร้าง 5-Layer ADK Spec (+ Layer 0)

| Layer | โมดูล | หน้าที่ |
|-------|-------|---------|
| **0 Runtime** | `pyclaw/runtime` | context mgmt, audit log, HITL approval, session persistence |
| **1 Memory** | `pyclaw/memory` | hierarchy, `@import`, auto-memory |
| **2 Skill** | `pyclaw/skills` | frontmatter, lazy load, auto-detect, chaining |
| **3 Hook** ★ | `pyclaw/hooks` | 8 events, allow/modify/block/notify |
| **4 Subagent** | `pyclaw/subagents` | explore/plan/review/general + parallel + trace bridge |
| **5 Plugin** | `pyclaw/plugins` | plugin.yaml, permissions.yaml, versioning |
| Orchestrator | `pyclaw/orchestrator` | AGENTS.md registry + `route_to_agent` (auto-routing) |
| MCP | `pyclaw/mcp` | `.agent/mcp-servers.yaml` |

★ = แกน deterministic; hook ครอบ **ทุก** tool call ใน `pyclaw/core/loop.py`
