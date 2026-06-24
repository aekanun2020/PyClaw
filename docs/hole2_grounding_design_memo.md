# Design Memo — ปิด Hole 2: grounding ไม่ทำงานใน orchestrator / subagent

สถานะ: ร่างเพื่อตัดสินใจ (ยังไม่แตะโค้ด)
บริบท: ต่อเนื่องจาก commit `bf940c4` (แยก grounding core ↔ PDPA wrapper)
ผูกกับ: Project Instruction "หลักการสถาปัตยกรรม PyClaw — host grounding ทุก domain"

---

## 1. ปัญหา (ยืนยันจากซอร์สจริง)

grounding hook ที่ทำเสร็จแล้ว คุ้มครอง **เฉพาะ flat `pyclaw chat`** เท่านั้น
ในเส้นทาง `--orchestrator` คำตอบเดินผ่าน **HookEngine เปล่า 2 ตัว** ไม่มี
enforce-grounding fire เลย → ม.84 หลุดได้แม้ regex/MCP จะแก้แล้ว

```
pyclaw chat --orchestrator
  └─ _build_orchestrator_loop()              [cli.py:142]
       hooks = HookEngine()                  ← (รู ก) ไม่เรียก loader.load_all()
       loop = AgentLoop(tools=route_to_agent เท่านั้น)
  │
  └─ OrchestratorRunner.route_one()          [runner.py:112]
       └─ SubagentRunner.spawn()             [runner.py:126]  fire PreSubagentSpawn เท่านั้น
            └─ build_isolated_runner._run()  [runner.py:244]
                 loop = AgentLoop(
                     hooks=HookEngine(),      ← (รู ข) "subagents inherit no parent hooks"
                 )
                 └─ pdpa-agent ดึง sec_21/27/39/83 → คืน summary
  │
  └─ orchestrator LLM synthesise summary → คำตอบสุดท้าย (ตาราง ม.84)
       ❌ ไม่มี PreResponse fire-site ในเส้นทางนี้เลย
```

| รู | ตำแหน่ง | อาการ |
|---|---|---|
| **ก** | orchestrator top — `cli.py:169` สร้าง engine เปล่า + runner ไม่มี PreResponse fire-site | คำตอบรวม (ที่ประกอบ ม.84) ไม่ถูก enforce |
| **ข** | subagent isolated — `runner.py:274` `hooks=HookEngine()` | pdpa-agent ที่ retrieve จริง ก็ไม่ถูก enforce |

ปัจจุบัน orchestrator กันด้วย **prompt อย่างเดียว** (system prompt บรรทัด 241
"GROUNDING (critical)... MUST NOT add section from memory") — ซึ่ง benchmark
Qwen3.6 5 รอบพิสูจน์แล้วว่า prompt บังคับได้แค่ 2/5 = "Prompt ≠ Policy"

---

## 2. หลักเกณฑ์ตัดสิน (จาก Project Instruction)

1. กลไกต้อง **domain-agnostic 100%** — ห้าม PDPA หลุดเข้า core
2. domain ใหม่ต้องเสียบได้ด้วยวิธีเดียวกัน
3. **grounding ควรผูกกับ agent ที่เกี่ยวข้อง** ไม่ยัดเข้า global engine
4. แก้ orchestrator/subagent fire-site = แก้ "ชั้นกลไก" เท่านั้น

---

## 3. สองทางเลือก

### ทางเลือก A — parent-inherit (subagent รับ engine จาก parent)

แนวคิด: เปลี่ยน `runner.py:274` จาก `HookEngine()` เปล่า → ส่ง engine ของ
parent (ที่โหลด plugin แล้ว) ลงไปให้ทุก subagent

```
build_isolated_runner(tool_provider, hooks=parent_hooks)
   loop = AgentLoop(hooks=parent_hooks, ...)
```

**ข้อดี**
- แก้จุดเดียว ปิดรู ข ได้ทันที
- subagent ทุกตัวได้ grounding ฟรี

**ข้อเสีย / ขัดหลักเกณฑ์**
- ❌ ขัดข้อ 3 โดยตรง: db-agent / finance-agent จะแชร์ engine เดียวกับ
  PDPA hook → grounding ไม่ได้ "ผูกกับ agent ที่เกี่ยวข้อง" แต่ยัดเข้า global
- ⚠️ พึ่ง scoping no-op + option (ก) Thai-only เป็นเกราะเดียวกันไม่ให้ db-agent
  โดน PDPA enforce — ถ้าวันหน้ามี domain ที่ pattern ชนกัน (เช่น finance ใช้
  "ม." ในความหมายอื่น) จะรั่ว
- ทำลาย isolation ที่ตั้งใจไว้ ("subagents inherit no parent hooks by default"
  เป็น design choice ไม่ใช่บั๊ก)

### ทางเลือก B — per-agent plugin (แต่ละ agent โหลด plugin ของตัวเอง)

แนวคิด: ใช้ `AgentSpec.home` (`agents/<name>/`) ที่มีอยู่แล้ว — โหลด plugin
จาก `agents/<name>/plugins/*/plugin.yaml` ตอน spawn สร้าง engine **เฉพาะของ
agent นั้น**

```
agents/pdpa-agent/plugins/pdpa-grounding/plugin.yaml   → pdpa-agent ได้ grounding
agents/db-agent/   (ไม่มี plugins/)                     → db-agent ไม่มี enforce
```

ตอน spawn:
```
agent_hooks = HookEngine()
PluginLoader(plugins_root=agent.home/"plugins").load_all(hooks=agent_hooks)
loop = AgentLoop(hooks=agent_hooks, ...)
```

**ข้อดี**
- ✅ ตรงข้อ 3 เป๊ะ: grounding ผูกกับ agent ที่เกี่ยวข้องโดยโครงสร้าง
- ✅ domain-agnostic จริง: db-agent ไม่มีทางโดน PDPA แม้ pattern ชนกัน
  (engine แยกกัน ไม่ใช่พึ่ง pattern scoping)
- ✅ reuse `AgentSpec.home` + `PluginLoader` ที่มีอยู่แล้ว — กลไกล้วน
- domain ใหม่: แค่ใส่ `agents/<x>/plugins/` ของตัวเอง (ตรงข้อ 2)

**ข้อเสีย**
- ต้องย้าย/สำเนา plugin manifest ไปไว้ใต้ `agents/pdpa-agent/plugins/`
- เพิ่ม path discovery ตอน spawn (โค้ดมากกว่า A เล็กน้อย)
- ยังต้องแก้ **รู ก** แยก (per-agent plugin แก้แค่ subagent ไม่แก้คำตอบรวม
  ของ orchestrator)

---

## 4. รู ก แก้แยก (จำเป็นไม่ว่าเลือก A หรือ B)

คำตอบสุดท้ายที่ผู้ใช้เห็น **ประกอบโดย orchestrator LLM** ไม่ใช่ subagent
ดังนั้นต่อให้ subagent ถูก ground ครบ orchestrator ก็ยัง synthesise มาตรา
เพิ่มได้ → ต้องมี **PreResponse fire-site ในเส้นทาง orchestrator** ด้วย

แต่ orchestrator enforce อะไร? มันไม่มี retrieval tool ของตัวเอง — grounded
set ของมันคือ **union ของทุก section ที่ routed agents retrieve** ดังนั้น
fire-site นี้ต้อง:
1. โหลด plugin ใน `_build_orchestrator_loop` (ปิดครึ่งแรกของรู ก)
2. รวบ grounded ids จาก RouteResult ของ agents → เป็น turn_state
3. fire PreResponse บนคำตอบรวม ก่อนส่งผู้ใช้

ข้อ 2 คือส่วนที่ต้องออกแบบเพิ่ม — RouteResult ปัจจุบันคืนแค่ `summary`
(text) ไม่ได้คืน grounded ids ออกมา ทางที่สะอาด: ให้ subagent loop เขียน
grounded set ลง turn_state แล้ว bubble กลับมาใน RouteResult เป็น field ใหม่
`grounded: set[str]` (กลไกล้วน ไม่ผูก domain)

---

## 5. ข้อเสนอแนะ

**เลือก B (per-agent plugin) + แก้รู ก** — เพราะ B เป็นทางเดียวที่ตรง
หลักเกณฑ์ข้อ 3 โดยโครงสร้าง ไม่ใช่พึ่ง pattern scoping เป็นเกราะ และทำให้
PyClaw เป็น framework host grounding ได้จริงทุก domain

ลำดับลงมือ (ถ้าอนุมัติ):
1. ย้าย plugin manifest → `agents/pdpa-agent/plugins/pdpa-grounding/`
2. กลไก: spawn โหลด plugin จาก `agent.home/plugins/` → engine ต่อ agent (ปิดรู ข)
3. กลไก: RouteResult เพิ่ม field `grounded` + orchestrator finalize fire
   PreResponse บน union grounded (ปิดรู ก)
4. โหลด plugin ใน `_build_orchestrator_loop` (ปิดครึ่งแรกรู ก)
5. test: (a) subagent ground+enforce, (b) orchestrator คำตอบรวมอ้างมาตรา
   ที่ไม่มี agent ไหน retrieve → BLOCK, (c) db-agent ไม่โดน PDPA (engine แยก)

ทั้ง 5 ขั้นแตะเฉพาะ **ชั้นกลไก** — ไม่มี PDPA vocabulary ลง core (ตรงข้อ 4)

---

## 6. สิ่งที่ยังเปิดให้ถก
- รู ก ข้อ 2: bubble grounded ids ผ่าน RouteResult vs ให้ orchestrator
  re-derive จาก trace — แบบแรกสะอาดกว่าแต่แตะ dataclass
- per-agent engine สร้างใหม่ทุก spawn (parallel routes) — cost เล็กน้อย
  เทียบกับความถูกต้อง ยอมรับได้
