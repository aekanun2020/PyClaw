# PyClaw — Requirements & Reference Specs

เอกสารต้นทาง (source-of-truth) ที่ใช้กำหนดสถาปัตยกรรมของ PyClaw เก็บไว้ที่นี่
เพื่อให้ requirement ติดไปกับโค้ดในทุก clone/checkout และตรวจสอบย้อนหลังได้ว่า
การออกแบบมาจากที่ใด

> หมายเหตุ: ไฟล์เหล่านี้คือ **เอกสารอ้างอิง** ไม่ใช่โค้ดที่ถูกรัน — เก็บไว้เพื่อ
> ใช้ตรวจสอบว่า implementation ตรงตาม spec

## ไฟล์ในโฟลเดอร์นี้

| ไฟล์ | คำอธิบาย |
|------|----------|
| [`agent-spec-claude.md`](./agent-spec-claude.md) | **Specification ต้นฉบับ** — Agent Architecture แบบ Claude-Style 5-Layer ADK (อ้างอิงแนวทาง Agent Development Kit ของ Anthropic) เอกสารหลักที่ใช้กำหนดว่าระบบ Agent ต้องมีองค์ประกอบอะไรบ้าง |
| [`agent-spec-revised.md`](./agent-spec-revised.md) | **Specification ฉบับปรับปรุง (พ.ค. 2026)** — เวอร์ชันที่ขยาย/ปรับจากฉบับต้นฉบับ ใช้เป็น spec ที่ implementation ของ PyClaw ยึดตามจริง |
| [`agent-loop-explained.md`](./agent-loop-explained.md) | **บทความอธิบายแนวคิด "Agent Loop คืออะไร"** — เอกสารพื้นหลัง/แนวคิดที่ใช้ทำความเข้าใจหลักการของ agent loop และที่มาของการออกแบบ |

## ความสัมพันธ์กับโค้ด

PyClaw implement สถาปัตยกรรม 5/6 เลเยอร์ตาม spec ข้างต้น โดยจุดที่ทุกเลเยอร์
มาบรรจบกันคือ `AgentLoop` ใน [`pyclaw/core/loop.py`](../../pyclaw/core/loop.py):

- **L0 runtime** — `pyclaw/runtime/` (context, audit, HITL)
- **L1 memory** — `pyclaw/memory/`
- **L2 skills** — `pyclaw/skills/`
- **L3 hooks** — `pyclaw/hooks/`
- **L4 subagents** — `pyclaw/subagents/`
- **L5 plugins/permissions** — `pyclaw/plugins/`
- **MCP** — `pyclaw/mcp/` (เชื่อมต่อ MCP servers ตามแบบ EliteClaw)

ดูภาพรวมการพิสูจน์ว่า implementation ตรงตาม spec ได้ที่ [`../demos/`](../demos/).
