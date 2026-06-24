# Design Memo — ปิด Hole 3: enforce BLOCK จบ run ทันที ไม่มี feedback-retry

สถานะ: ร่างเพื่อตัดสินใจ (ยังไม่แตะโค้ด)
บริบท: ต่อเนื่องจาก `45dc09b` (Fix B-observability: bubble block_detail) และ
`e961d09` (SKILL.md retrieve-each-section). ทั้งสองช่วยแล้ว แต่ trace ล่าสุด
(session ที่ส่ง 2026-06-24 20:09) พิสูจน์ว่ายังล่ม — root cause อยู่ที่ **กลไก loop**
ไม่ใช่ skill
ผูกกับ: Project Instruction ข้อ 1+4 — แก้ชั้นกลไกเท่านั้น ห้าม vocabulary หลุดเข้า core

---

## 1. ปัญหา (ยืนยันจาก trace + ซอร์สจริง)

SKILL.md patch ทำให้ agent **retrieve ถูกมาตราแล้ว** แต่ยังถูก block เพราะตอน
*เขียนคำตอบ* หลุดไป cite มาตราที่เห็นใน `search_pdpa` context (ไม่ได้ get_section_text):

| Route | get_section_text (grounded) | คำตอบ cite | missing → ผล |
|---|---|---|---|
| R1 | `[23,24,27,39]` ✓ ตรง benchmark | `[22,26,37]` | `[22,26,37]` → **BLOCK** |
| R2-a | `[21,23,24,27,39]` | `[1,19,26]` | `[1,19,26]` → BLOCK |
| R2-b | `[21,23,24,27,39]` | `[24,26,27,30,31,32,35,36,37]` | 6+ ตัว → BLOCK |
| R3,R4 | — | — | breaker ตัด (`blocked-limit`) → chain ล่ม |

ปลายทาง: orchestrator หันไปถาม rag-agent → ได้คำตอบ **"ตอบไม่ได้ ปรึกษาทนาย"**
ทั้งที่ R1 retrieve มาตราถูกต้องครบ (24/27/39) อยู่แล้ว — แค่มี 22/26/37 ปนมา

### กลไกที่ทำให้ล่ม (core/loop.py)

```
AgentLoop._run()                              [loop.py:144]
  for round in range(max_tool_rounds=20):
    response = llm.complete(...)
    if not response.tool_calls:                ← LLM ตัดสินใจ "จะตอบ"
        final = self._finalize(text, ...)      [loop.py:164]
            └─ PreResponse hook → enforce_grounding
                 missing = cited - grounded ≠ ∅ → HookAction.BLOCK
            └─ _finalize: stash block_detail, return RESPONSE_BLOCKED  [loop.py:295-302]
        return final                           ← ❌ ออกจาก loop ทันที จบ run
```

**จุดตาย:** ไม่มี retrieve-then-retry **ภายใน agent run เลย**. block = `return` ออก
ทันที (บรรทัด 164-166). agent ไม่เคยได้รับ feedback "คุณ cite [22,26,37] ที่ไม่
retrieve" เพื่อแก้ตัวใน run เดียวกัน — `block_detail` ที่เพิ่ง ship ไปโผล่ที่
**orchestrator (ชั้นนอกสุด)** ซึ่งทำได้แค่ตัด breaker ไม่ใช่ป้อนกลับให้ agent แก้

```
loop block → SubagentResult.blocked=True → orchestrator breaker +1
           → ครั้งที่ 2 → breaker ตัด (block_breaker_limit=2) → fallback → "ตอบไม่ได้"
```

| รู | ตำแหน่ง | อาการ |
|---|---|---|
| **ค** | `loop.py:164-166` block path = `return` ทันที | agent ไม่เคยเห็น missing-list ของตัวเอง ไม่มีโอกาสแก้ |

---

## 2. ทำไม prompt/skill อย่างเดียวปิดไม่ได้

`e961d09` (SKILL.md) คือ "Prompt" — ลดโอกาส*หลุด*ตั้งแต่แรกได้ แต่ benchmark เดิม
(Qwen 2/5) + trace นี้ยืนยันซ้ำว่า LLM ยังหลุด cite จาก context เป็นครั้งคราว.
นี่คือ **"Prompt ≠ Policy"** เดิม — Policy ที่หายไปคือ *feedback loop เชิงกลไก* ที่
บังคับให้ agent แก้ตัวก่อนคืนคำตอบ ไม่ใช่ skill ที่เข้มขึ้น

---

## 3. ตัวเลือก

### B — strip-not-block (ใน hook)
hook ลบเฉพาะประโยคที่อ้างมาตรา ungrounded แล้วปล่อยที่เหลือผ่าน
- ✗ regex ดึงได้แค่ "เลข" ไม่รู้ขอบเขตประโยค → strip สะอาดยาก คำตอบขาดวิ่น
- ✗ ความหมายเชิง redaction เป็น domain concern เริ่มรั่วเข้า core
- ✗ ไม่บังคับให้ agent ไป retrieve ของจริง — ได้คำตอบที่ "ถูกตัด" ไม่ใช่ "ถูกต้อง"

### C — in-loop retry-on-block (แนะนำ) ✅
เมื่อ `_finalize` คืน BLOCK → แทนที่จะ `return` → **inject hook.message กลับเข้า
context แล้ววน round ต่อ** ภายใน max_tool_rounds เดิม. agent เห็น
"คุณ cite [22,26,37] ที่ไม่ retrieve — retrieve หรือลบออก" → แก้ตัว → ตอบใหม่ → ผ่าน

```
if block:
    if block_retry_count >= BLOCK_RETRY_LIMIT:   ← guard กัน infinite (เช่น 2)
        return RESPONSE_BLOCKED                  ← ยอมแพ้ คืน sentinel เดิม
    context.append(USER/SYSTEM: hook.message)    ← feedback เชิงกลไก (opaque string)
    block_retry_count += 1
    continue                                     ← วน round เดิมต่อ
```

- ✓ loop เห็นแค่ `HookAction.BLOCK + message` (opaque) — **ไม่รู้ว่าเป็น PDPA**
  → กลไกล้วน ไม่มี vocabulary หลุดเข้า core (ข้อ 1+4)
- ✓ ใช้ได้ทุก domain ที่มี enforce hook (medical/finance) โดยไม่แก้เพิ่ม (ข้อ 2)
- ✓ breaker แทบไม่ทำงาน — agent แก้ตัวจบใน run เดียวก่อนคืน blocked
- ✓ SKILL.md ยังมีค่า: ลดจำนวนรอบ retry ที่ต้องใช้

### E — ขยาย breaker / soft-block ที่ orchestrator
แก้ที่ orchestrator ให้ทน block มากขึ้น
- ✗ แก้ผิดชั้น — orchestrator ไม่มีทางป้อน feedback ให้ leaf agent retrieve
- ✗ เพิ่ม block_breaker_limit แค่เลื่อนปัญหา ไม่แก้ root

---

## 4. ขอบเขตการแก้ (ถ้าเลือก C)

1. **`core/loop.py` `_run`** — เปลี่ยน block path จาก `return` เป็น inject+continue
   พร้อม `block_retry_count` guard. ค่าคงที่ `BLOCK_RETRY_LIMIT` (เสนอ 2) —
   mechanism-only ไม่มี domain meaning
2. **`_finalize`** — ต้องให้ `_run` แยก "BLOCK" ออกจาก "text ปกติ" ได้.
   ปัจจุบันกลืนเป็น sentinel string. เสนอ: `_finalize` คืน `(text, blocked: bool,
   detail: str|None)` หรือ `_run` เช็ค `turn_state[BLOCK_DETAIL_KEY]` ที่ถูก stash
   (มีอยู่แล้ว loop.py:300-301) — ทางหลังแตะ API น้อยกว่า
3. **feedback message** — ใช้ `res.message` ดิบ (hook เป็นคนเขียน, opaque ต่อ loop).
   inject เป็น `Role.USER` หรือ `Role.SYSTEM` (เลือก USER เพื่อให้ LLM ตอบสนอง
   เหมือน user แก้คำสั่ง)
4. **เทสต์ใหม่** (`tests/test_inloop_retry.py`):
   - block → inject → agent retrieve เพิ่ม → pass ใน 1 run (fake llm 2-step)
   - block ติดกัน ≥ LIMIT → คืน sentinel (กัน infinite)
   - non-block path ไม่เปลี่ยนพฤติกรรม
   - retry message ไม่รั่ว domain vocabulary (เช็คว่า loop ไม่ parse เนื้อหา)
5. **regression** — 236 passed เดิมต้องไม่พัง; ดูเฉพาะเทสต์ที่ assert
   "block → return ทันที" (ถ้ามี) ต้องปรับให้ตรง semantic ใหม่

---

## 5. ความเสี่ยง / ข้อควรระวัง

- **Infinite loop:** ถ้า agent ดื้อ cite ผิดซ้ำ — guard ด้วย BLOCK_RETRY_LIMIT +
  max_tool_rounds เดิมเป็นเพดานสองชั้น
- **Token cost:** retry กินรอบเพิ่ม — แต่ < cost ของการล่มทั้ง chain + fallback ไป
  rag-agent (trace นี้ใช้ ~6 route, 2 agent, จบด้วยตอบไม่ได้)
- **chat path เดิม:** `pyclaw chat` (flat) ใช้ loop เดียวกัน → ได้ feedback-retry
  ฟรี เป็นผลพลอยได้ที่ดี
- **ไม่แตะ hook/wrapper:** enforce_grounding, PDPA wrapper, breaker คงเดิมทั้งหมด —
  Fix C อยู่ใน loop ล้วน

---

## 6. คำถามตัดสินใจ

1. เลือก C ไหม? (vs B strip / E breaker)
2. BLOCK_RETRY_LIMIT = 2 พอไหม (รวม max_tool_rounds=20 เป็นเพดานนอก)
3. inject เป็น Role.USER หรือ Role.SYSTEM?
4. `_finalize` API: เปลี่ยน return signature หรืออ่าน turn_state[BLOCK_DETAIL_KEY]?
   (เสนอทางหลัง — แตะ API น้อยสุด)
