# PyClaw เทียบกับเสียงวิจารณ์ Agent Skills จากชุมชน

> เอกสารนี้นำ "จุดที่ขาด / ถูกวิจารณ์บ่อยที่สุด" ของ Agent Skills (รวบรวมจากคอมเมนต์/รีวิวคนไทยและต่างชาติบน Facebook, Reddit, Blog + รายงาน Snyk ToxicSkills ก.พ. 2026) มาเทียบกับสิ่งที่ PyClaw ทำจริงในโค้ด
>
> **หลักการตรวจสอบ:** ทุกข้ออ้างอิง `path:line` ของโค้ดจริงบน `main` (commit `601dd7c`) — ไม่ใช่คำกล่าวอ้างลอย ๆ
>
> **ปรัชญาแกนของ PyClaw ที่ทำให้ต่างจาก Agent Skills ทั่วไป:** *"Prompt ≠ Policy"* — นโยบายความปลอดภัย/การควบคุมอยู่ใน **โค้ดที่ deterministic** ไม่ใช่ในข้อความ prompt ที่โมเดลอาจไม่ทำตามหรือถูกหลอกได้

---

## ส่วนที่ 1 — เทียบกับ 5 จุดที่ชุมชนวิจารณ์โดยตรง

### จุดที่ 1 — Security (จุดอ่อนอันดับ 1)

ชุมชนกังวล: prompt injection (91% ของ malicious skill), malicious skill จาก marketplace, hardcoded API keys (10.9%), `curl | bash` runtime (2.9%), เขียนทับ MEMORY.md เพื่อฝังตัวถาวร

| ภัยที่ถูกวิจารณ์ | PyClaw จัดการอย่างไร (โค้ดจริง) |
|---|---|
| **Prompt Injection** | LLM hook เป็น **advisory เท่านั้น** — ผล `BLOCK/MODIFY` ที่โมเดลพยายามสั่งจะถูก downgrade เป็น `ALLOW` เสมอ (`pyclaw/hooks/runners.py:175-216`). การบล็อกจริงทำในโค้ดที่ `PermissionPolicy.is_allowed` ซึ่ง `blocked_tools` ชนะเสมอ (`pyclaw/plugins/permissions.py:22-27`) |
| **Malicious / destructive ops** | guard `block_destructive` บล็อก destructive tool บน protected path (`.env`, `secrets/`, `.git/`, `*.pem`, `id_rsa`) แบบ outright และบังคับ HITL บน path ปกติ (`pyclaw_hooks/guards.py:25-95`) |
| **Hardcoded API keys** | key อ่านจาก env เท่านั้น ไม่มี default ที่ฝัง secret (`pyclaw/config.py:47`) |
| **เขียนทับ MEMORY.md ฝังตัวถาวร** | `AUTO_MEMORY.md` ถูก cap 200 บรรทัด / 25 KB และ trim แบบ FIFO ทุกครั้งที่เขียน (`pyclaw/memory/loader.py:127-156`) |
| **Hook ที่ error แล้วเงียบ ปล่อยผ่าน** | runner ทุกตัว fail-closed: Bash non-zero exit/timeout = BLOCK (`runners.py:108-112`), HTTP network error/non-2xx = BLOCK (`runners.py:165-169`); engine เจอ BLOCK แรก short-circuit ทันที ไม่มี hook ภายหลังปลดบล็อกได้ (`pyclaw/hooks/engine.py:96-102`) |

**ช่องว่างที่เหลือจริง:** ยังไม่มี static scanner ตรวจเนื้อ `SKILL.md` หา `curl | bash` หรือ payload อันตราย **ก่อนโหลด** (PyClaw บล็อกที่ชั้น tool-execution แต่ไม่ได้ตรวจเนื้อ skill ตอนติดตั้ง)

### จุดที่ 2 — Description / Triggering ยาก

- การ trigger ผูกกับ **description จริง** ไม่ใช่ instruction body — `SkillLoader.detect` ให้คะแนนจาก keyword overlap ระหว่าง request กับ `name + description` (`pyclaw/skills/loader.py:25-42`) ตรงกับคำเตือนของ agentskills.io ว่า "ถ้า skill ไม่ trigger ปัญหาอยู่ที่ description"
- มี 3 โหมด invocation: `AUTO` / `MANUAL` / `ALWAYS` (`pyclaw/skills/registry.py:43-46`) — โหมด `ALWAYS` แก้ปัญหา "skill สำคัญไม่ถูกเรียก" โดยฉีดเข้า system prompt เสมอ (`pyclaw/orchestrator/registry.py:87-112`)

### จุดที่ 3 — Skill Stacking / Overlap

- **chaining มี cycle-guard**: `expand_chain` ทำ DFS พร้อม `seen` กันวนซ้ำ (`pyclaw/skills/loader.py:58-74`)
- **กัน tool ชนกัน**: agent แต่ละตัวเป็นเจ้าของ tool เฉพาะ prefix ของตน (`AgentSpec.owns`, `pyclaw/orchestrator/registry.py:61-67`); auto-register จะ **ไม่ทับ** agent ที่มีอยู่ (`registry.py:318-325`)
- **Progressive disclosure**: parse เฉพาะ frontmatter ตอน startup, body หนักโหลด on-demand (`registry.py:67-88` + `loader.py:49-56`)

### จุดที่ 4 — Marketplace / trust ยังขาด

- ไม่พึ่ง marketplace ภายนอก — ใช้ **plugin self-host** ที่ตรวจสอบได้: ทุก plugin มี `plugin.yaml` ระบุ name/version/requires และ **มี permission policy ต่อ plugin** (`pyclaw/plugins/loader.py:140-194`)
- มี **semver dependency check** ที่ fail loudly เมื่อ requirement ไม่ครบ (`loader.py:156-168`)
- ทุก tool/hook ที่ plugin ให้มาไหลผ่าน chokepoint เดียวกัน คือ permission + hook engine (`loader.py:9-11`)

### จุดที่ 5 — restart session / token cost / versioning

| ข้อกังวล | PyClaw |
|---|---|
| ต้อง restart ทุกครั้งที่ติดตั้ง skill | ไม่ต้อง — registry `scan()` walk `**/SKILL.md` ตอน runtime (`registry.py:67-88`); orchestrator scan agent skills แบบ live (`orchestrator/registry.py:101-105`) |
| skill ซับซ้อนกิน token | lazy disclosure: prompt catalog เป็นแค่ name+description (`registry.py:96-98`) |
| ไม่มี versioning ที่ดี | `SkillMeta.version` อยู่ใน frontmatter (`registry.py:56`) + plugin มี semver check (`loader.py:58-84`) |

---

## ส่วนที่ 2 — มิติอื่นนอกเหนือ Security (ที่ชุมชนพูดถึงน้อยแต่สำคัญต่อการใช้งานจริง)

### 2.1 Deterministic Chokepoint — tool ไม่เคยถูกเรียกจาก output ดิบของโมเดล

ทุก tool call ผ่าน `_invoke_tool()` ซึ่งบังคับลำดับตายตัว: **permission → PreToolUse hook → HITL → execute → PostToolUse hook → audit** (`pyclaw/core/loop.py:162-234`). โมเดลไม่มีทางข้ามชั้นเหล่านี้ — แก้ปัญหา "agent ทำตาม prompt ไม่ครบ" ที่เราเจอเองตอนทดสอบ qwen (2-3/5)

### 2.2 Observability / Audit — ตรวจสอบย้อนหลังได้ และเป็นมิตรกับ PDPA

ทุกการเรียก tool ถูกบันทึกลง `audit.jsonl` แบบ append-only โดย **hash input/output ไม่เก็บค่าดิบ** (ปลอดภัยกับข้อมูล PII) เขียนแบบ flush + fsync กัน record หายตอน crash (`pyclaw/runtime/audit.py:27-79`) — บันทึกเสมอจากโค้ด ไม่ขึ้นกับว่าโมเดล "ตัดสินใจ" อย่างไร

### 2.3 Human-In-The-Loop (HITL) — fail-closed

tool อันตราย (`delete_file`, `deploy_to_production`, `modify_secrets`) ต้องได้รับการอนุมัติจากคนก่อน มี timeout (ค่าเริ่มต้น 60 วินาที) และ **timeout/ข้อผิดพลาด = DENIED เสมอ** ไม่เคยกลายเป็นอนุมัติโดยบังเอิญ (`pyclaw/runtime/hitl.py:58-77`)

### 2.4 MCP Integration — สาเหตุที่ Langflow ตก 0/5 อยู่ที่จุดนี้

- MCP tool ถูก adapt เข้า ToolRegistry เดียวกัน จึงไหลผ่าน hook + permission **chokepoint เดียวกับ native tool** (`pyclaw/mcp/client.py:18-22`)
- เชื่อมต่อ fail loudly: ถ้า connect ไม่ได้ทั้ง primary และ fallback transport จะ raise (`client.py:214-238`); เรียก tool ก่อน connect ก็ raise (`client.py:305-310`); ผล `isError` จาก server จะ raise ไม่กลืนเงียบ (`client.py:300-301`)
- รองรับ EliteClaw `.env` เดิมไม่ต้องแก้ (`client.py:419-531`)

นี่คือเหตุผลเชิงโครงสร้างว่าทำไม **Langflow ตก 0/5 เป็นปัญหา integration ไม่ใช่ปัญหา model** — เมื่อ MCP ไม่ได้ต่อกับ TestDB จริง agent ก็ไม่มี tool จริงให้เรียกและไปแต่งตัวเลขแทน ส่วน PyClaw บังคับให้ tool จริงทำงานผ่าน chokepoint จึงตรวจจับสภาพ "ไม่มี tool" ได้

### 2.5 Multi-agent Isolation — context เป็นทรัพยากร, จำกัดความลึก

- subagent แต่ละตัวรันใน AgentLoop ของตัวเองด้วย context ใหม่ — ประวัติ parent ไม่รั่ว มีแค่ `summary` ที่ข้ามกลับ (`pyclaw/subagents/runner.py:6-9`, `95-100`)
- **ห้าม nesting**: subagent ห้าม spawn subagent ต่อ มีทั้ง `is_nested` guard และการถอด subagent tool ออก (`runner.py:116-145`)
- subagent ถูก allowlist เฉพาะ tool ที่ resolve แล้ว ถ้าไม่มี tool provider จะได้ registry ว่าง = ไม่มี tool ให้เรียก (ปลอดภัยสำหรับ subagent อ่านอย่างเดียว) (`runner.py:229-284`)
- รันขนานได้จริง (ThreadPoolExecutor) แต่คืนผลตามลำดับ specs เพื่อความ deterministic (`runner.py:180-226`)

---

## สรุป

PyClaw ครอบคลุม **4 จาก 5** จุดวิจารณ์หลักด้วยกลไกในโค้ดจริง โดยจุด security (อันดับ 1) แข็งที่สุดเพราะวางนโยบายไว้ในโค้ด fail-closed ไม่ใช่ใน prompt — ซึ่งตรงกับสาเหตุที่ skill ส่วนใหญ่ในรายงาน Snyk ถูกเจาะ (prompt injection)

นอกเหนือ security ยังมี 5 มิติที่ชุมชนพูดถึงน้อยแต่ PyClaw ทำจริง: deterministic chokepoint, audit ที่เป็นมิตรกับ PDPA, HITL fail-closed, MCP integration ที่ fail loudly, และ multi-agent isolation ที่จำกัดความลึก

**ช่องว่างที่ยังเหลือ (อ้างอิงจากการตรวจโค้ดจริง):**
1. ไม่มี static scanner ตรวจเนื้อ SKILL.md ก่อนโหลด (เช่น หา `curl | bash`)
2. cross-platform/cross-model compatibility — สอดคล้องผลทดสอบเราเองที่ qwen ทำได้ 2-3/5 ส่วน claude 5/5 ย้ำว่า "Prompt ≠ Policy" จริง: rule เดียวกัน โมเดลอ่อนกว่าก็ยังทำตามได้ไม่ครบ

---

*แหล่งอ้างอิงเสียงวิจารณ์ชุมชน:* [Snyk ToxicSkills report](https://snyk.io/fr/blog/toxicskills-malicious-ai-agent-skills-clawhub/), [OWASP Agentic Skills Top 10](https://owasp.org/www-project-agentic-skills-top-10/), [blog.codingthailand.com](https://blog.codingthailand.com/agent-skills/), [thepexcel.com](https://www.thepexcel.com/agent-skills/), [thadaw.com](https://thadaw.com/posts/agent-skill-teach-ai-to-do-what-it-cant/)

*ตรวจสอบกับโค้ด PyClaw บน `main` commit `601dd7c` — บันทึก 2 มิ.ย. 2569*
