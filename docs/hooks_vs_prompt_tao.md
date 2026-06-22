# Hooks vs Prompt — บังคับ TAO ด้วย Policy ไม่ใช่ Prompt

> หลักการ #1: **Prompt ≠ Policy.** สิ่งที่ "ต้องเกิดทุกครั้ง" ไม่ควรอยู่ใน prompt
> (โมเดลทำตามแบบ non-deterministic) แต่ควรอยู่ใน **Hook** (โค้ดที่บังคับได้จริง).
>
> หลักฐานเชิงประจักษ์: benchmark Q-CCTV-2 บน Qwen3.6 5 รอบ ด้วย input/model/tool
> เดียวกัน ได้ `GROUND_OK` เพียง **2/5 (40%)** — โมเดล *ทำได้* (R5, R6 ผ่าน) แต่
> *ไม่สม่ำเสมอ* (compliance-instability). กฎ grounding ที่อยู่ใน docstring (soft
> prompt) จึงการันตีไม่ได้ → ต้องยกขึ้นเป็น Hook. ดู `pdpa_agent_report.md`.

## TAO = Thought → Action → Observation

Hook บังคับได้เฉพาะ **พฤติกรรมที่สังเกตได้** (Action / Observation / คำตอบ) —
บังคับ **Thought (การคิดในหัวโมเดล)** ไม่ได้ เพราะไม่มี event ให้ดัก.

| ขั้น | prompt บังคับได้แค่ "ขอร้อง" | Hook บังคับได้แบบ "กฎตายตัว" |
|---|---|---|
| **Thought** | ✅ (ต้องใช้ prompt — เป็นการคิดในหัวโมเดล) | ❌ Hook แตะไม่ได้ ไม่มี event |
| **Action** (tool call) | "ควรเรียก get_section_text นะ" | ✅ `PreToolUse` — บังคับ/บล็อก/แก้ args ได้จริง |
| **Observation** (ผลลัพธ์) | "อ่านผลให้ดีนะ" | ✅ `PostToolUse` — ดักผล บันทึก แก้ค่าได้ |
| **คำตอบสุดท้าย** | "อย่าอ้างมาตราที่ไม่ได้ดึงนะ" | ✅ `PreResponse` — BLOCK ได้ถ้าฝ่าฝืน |

**สรุป:** ความฉลาดในการคิดมาจาก prompt; ความแน่นอนของการกระทำมาจาก Hook —
ทำงานคู่กัน ไม่แทนกัน. กฎ "ทุก citation ต้อง grounded" เป็นเรื่องของ Action +
คำตอบสุดท้าย → ยกจาก prompt มาเป็น Hook ได้และควรทำ.

## เส้นทางการทำงานของ Hook: yaml → Engine → Runner → Hook

```
[1] default_hooks.yaml            [2] HookEngine               [3] PythonRunner
  (config: มี hook อะไร)            (ผู้จัดการ)                  (พนักงาน — รัน Hook)
  name / event / runner / target      │                              │
        │ parse                        │ core loop สร้าง payload       │
        ▼                              │ แล้ว fire(payload) ตอน event   │
  HookSpec (ใบสั่ง 1 hook)    register  │                              │
   event=HookEvent.PRE_RESPONSE  ─────► hooks_for(event) เรียง priority │
   runner=RunnerType.PYTHON              runner = self.runners[runner] ─┤ (เลือก runner)
   target='..grounding:enforce..'        runner.run(target, payload) ──►│
                                                                         │ split target ที่ ':'
                                         ตัดสินผล (deterministic):        │ import โหลด grounding.py
                                         • BLOCK ตัวแรกชนะ (fail-closed)   │ getattr หยิบ enforce_grounding
                                         • MODIFY ต่อโซ่ / NOTIFY สะสม     │ result = func(payload)  ◄── เรียก Hook
                                         • ลำดับตายตัว → คาดเดาได้ 100%     │        │
                                                  ▲                       │        ▼
                                                  └── return HookResult ───┴── [4] Hook = ฟังก์ชันของเรา
                                                       (ALLOW/BLOCK)            def enforce_grounding(payload):
                                                                                 อ่าน payload → ตัดสิน → HookResult
```

### คำศัพท์ที่งงบ่อย

- **Hook** = **ฟังก์ชันตัวเดียว** (เช่น `enforce_grounding`) ที่ระบบเรียกตาม event.
  signature ตายตัว: `(HookPayload) -> HookResult`. เป็น **event-driven แบบ
  synchronous** (loop หยุดรอผลก่อนเดินต่อ — ถ้าไม่รอก็ BLOCK ไม่ทัน).
- **HookSpec** = "ใบสั่ง" ที่ห่อ Hook — บอก name / event / runner / target /
  priority. เขียนใน yaml หรือใน code (`engine.register(HookSpec(...))`) ก็ได้.
- **target** = **ที่อยู่ของ Hook** (string `'module:function'`). `:` แบ่ง
  "ไฟล์ (ก่อน :)" ออกจาก "ฟังก์ชันในไฟล์ (หลัง :)". เป็น field หนึ่งใน HookSpec —
  ไม่เกี่ยวกับ determinism เลย แค่ชี้ทางว่าจะเรียกฟังก์ชันไหน.
- **payload** = **กล่องข้อมูลของ event 1 ครั้ง** (`HookPayload`) ที่ core loop
  สร้างแล้วส่งผ่าน Engine → Runner → ถึง Hook. Hook อ่าน `tool` / `result` /
  `arguments` จากกล่องนี้.
- **deterministic** มาจาก **(1) Hook เขียนเป็น pure logic** (regex + set ops
  ไม่มีสุ่ม ไม่ถามโมเดล) + **(2) Engine resolve ตามกฎตายตัว** (priority sort
  stable, BLOCK ตัวแรกชนะ). ไม่ได้มาจาก target.

### ทำไมต้อง PythonRunner ไม่ใช่ LlmRunner

มี 4 runner: `bash` / `python` / `http` (deterministic) และ `llm` (non-deterministic).
`LlmRunner` ถูกบังคับให้ทำได้แค่ ALLOW/NOTIFY — ถ้าโมเดลพยายาม BLOCK/MODIFY จะถูก
downgrade เป็น ALLOW (เพราะนั่นคือการเอา policy ไปไว้ใน prompt อีก = ผิดหลัก #1).
grounding hook ต้อง BLOCK ได้จริง → ต้องเป็น **PythonRunner**.

## สถานะงาน citation-grounding Hook (ค้างอยู่)

- ไฟล์ `pyclaw_hooks/grounding.py` ร่างไว้แล้ว: `record_grounding` (PostToolUse) +
  `enforce_grounding` (PreResponse).
- **BUG ที่ต้องแก้ก่อน wire เข้า yaml:** ร่างปัจจุบันเก็บ state ผ่าน
  `payload.extra["grounded_sections"]` แต่ core loop สร้าง payload ตอน
  PostToolUse / PreResponse โดย `extra = {}` ว่างทุกครั้ง (`loop.py:222, 239`
  ไม่ได้ส่ง extra) → `record_grounding` กับ `enforce_grounding` ส่ง state หากันไม่ได้.
- **ทางแก้ A (แนะนำ):** เก็บ state เป็น module-level set + เพิ่ม hook ตัวที่ 3
  `reset_grounding` (PreSession, fire ที่ `loop.py:95`) เพื่อ clear ทุก session.
  ไม่ต้องแตะ core loop, เข้ากับ pattern `pyclaw_hooks/guards.py` เดิม.
