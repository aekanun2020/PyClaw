# PyClaw — Live Demo Captures

ภาพและ log เหล่านี้บันทึกจากการ **รันจริง** ของ PyClaw (ไม่ใช่ mock) โดยต่อกับ
OpenRouter LLM จริง (model `openai/gpt-oss-120b`) API key อ่านจาก
`OPENROUTER_API_KEY` ตอนรันเท่านั้น — ไม่เคย commit ลง repo

## 1. Layer 1 Memory ป้อนเข้า LLM จริง

![Memory demo](./memory_live.png)

สคริปต์: [`demo_memory_live.py`](../../demo_memory_live.py) · log: [`memory_live_output.log`](./memory_live_output.log)

- **Part 1 (deterministic):** `MemoryLoader.load()` เดินไล่ไดเรกทอรีจาก working dir
  ขึ้น root, รวมไฟล์ memory แบบ global-first → local-last, ขยาย `@import`, และ cap
  `AUTO_MEMORY.md` ไว้ที่ 200 บรรทัด
- **Part 2 (real LLM):** ป้อน memory ที่รวมแล้วเป็น system prompt แล้วถามข้อเท็จจริงที่
  **มีอยู่แค่ใน memory** เท่านั้น — LLM ตอบ canary `BLUE-PANGOLIN-42` (จาก `@import`) และ
  เคารพ local-scope rule ของ billing service ได้ถูกต้อง

## 2. AgentLoop end-to-end + block-destructive hook

![AgentLoop demo](./agentloop_live.png)

สคริปต์: [`demo_live_loop.py`](../../demo_live_loop.py) · log: [`agentloop_live_output.log`](./agentloop_live_output.log)

- **Scenario A:** LLM ตัดสินใจเรียก `write_file("notes.txt")` เอง → tool ทำงาน → audit `tool_call`
- **Scenario B:** LLM พยายามเขียน `secrets/prod.key` → PreToolUse hook **BLOCK** จริง →
  ไม่มีอะไรถูกเขียน → audit `tool_blocked_hook`

ข้อ 2 คือหลักฐานของหลัก **"Prompt ≠ Policy"**: แม้โมเดลจะ "ยอมทำ" ตามคำสั่ง แต่ guardrail
แบบ deterministic (code) กั้นได้ — โมเดลข้ามไม่ได้

---

วิธีสร้างภาพใหม่: รัน demo พร้อม `tee` เก็บ log แล้วเรนเดอร์ด้วย
[`tools/render_terminal.py`](../../tools/render_terminal.py)
