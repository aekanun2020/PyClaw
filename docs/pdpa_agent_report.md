# PDPA Agent Evaluation Report — Grounding Discipline
**วันที่วินิจฉัย:** 21 มิถุนายน 2569
**Agent:** `agents/pdpa-agent/` (SOUL.md + TOOLS.md + skills/pdpa-grounding-discipline)
**MCP tools:** `pdpa_search_pdpa`, `pdpa_get_related_sections`, `pdpa_get_penalty`, `pdpa_get_section_text` *(เพิ่มใหม่ 21 มิ.ย. 2569 — แก้ราก H3)*
**Knowledge base:** PDPA Knowledge Graph (Sections / Exemptions / LawfulBasis / Penalty nodes + edges)
**ชุดทดสอบ:** CCTV Benchmark 3 ระดับ (เก็บ → ใช้สอบสวน → ใช้การตลาด) — spec อยู่ใน [`pdpa_cctv_benchmark.md`](../pdpa_cctv_benchmark.md)

---

## บทสรุปผู้บริหาร (TL;DR)

**ปัญหาหลัก = H3 (missing-tool): agent ไม่มีเครื่องมือดึง "ตัวบทมาตรา" มาอ่าน**

ไม่ใช่ปัญหาที่โมเดลตอบผิดกฎหมาย ไม่ใช่ skill เขียนไม่ครอบคลุม และไม่ใช่โมเดลขี้เกียจเรียก tool — แต่เป็นเพราะ **ไม่มี tool ตัวใดคืน original text ของ node มาตราเป้าหมาย** agent จึงเห็นความเชื่อมโยง (edge) แต่ "ตามรอยไปอ่านเนื้อหาต่อไม่ได้" และต้องเติมตัวบทจากความจำ → ตอบถูกกฎหมายแต่ผิดวินัย grounding ทุกครั้ง

**สิ่งที่ต้องทำ (ตามลำดับ):**
1. เพิ่ม MCP tool **`get_section_text(section_id)`** ที่คืนตัวบทจริงของมาตรา — **แก้ที่ราก ต้องมาก่อน**
2. ผูก **citation-grounding Hook** — บังคับว่าทุกมาตราที่อ้างต้อง map กับ retrieved text (ทำได้หลังมี tool แล้วเท่านั้น)
3. แก้ wording ของ SKILL.md (item 5 + เพิ่ม gate "edge = pointer ไม่ใช่หลักฐาน") — secondary เพราะ legal reasoning ถูกอยู่แล้ว

---

## อาการ: "เห็นแต่ตามรอยต่อไม่ได้" (Q15 → CCTV)

อาการนี้พบครั้งแรกในเคส Q15 (edge `sec_30 → sec_24`) และเกิดซ้ำทุกครั้งในรูปแบบเดียวกัน:

1. agent **เห็น** edge เช่น `sec_27 → sec_39 (REFERENCES_SECTION)` จาก `pdpa_get_related_sections` (เห็น pointer)
2. agent **อยากอ่าน** ตัวบท ม.39 ว่าเขียนว่าอะไร — แต่ **ไม่มี tool ให้เรียก** (ไม่มี `get_section_text`)
3. agent จึง **เติมตัวบท ม.39 จากความจำ** → คำตอบถูกกฎหมาย แต่ตัวบทไม่ได้มาจาก tool result

### ทำไม tool ปัจจุบันไม่พอ

| tool | คืนอะไร | ดึงตัวบทมาตราหลักได้ไหม |
|---|---|:---:|
| `pdpa_search_pdpa` | chunk / exemption ที่ใกล้เคียง (semantic search) | ❌ คืนของใกล้เคียง ไม่ใช่ตัวบทมาตราเป้าหมายเป๊ะ |
| `pdpa_get_related_sections` | **edge เปล่า** เช่น `sec_27 → sec_39` (by design) | ❌ บอกแค่ "โยงไปไหน" ไม่บอก "เขียนว่าอะไร" |
| `pdpa_get_penalty` | ข้อความโทษ (penalty node) | ⚠️ ได้เฉพาะโทษ ไม่ใช่ตัวมาตรา |

ตัวบทที่ "ดึงได้จริง" มีแค่ข้อความโทษ (`get_penalty`) และ `description` ของ exemption node (sec24_ex*) ที่บังเอิญติดมากับ edge — **ไม่มี tool ใดคืนตัวบทของมาตราหลัก** (ม.19, 23, 27, 39 วรรคต่าง ๆ) ได้แบบเจาะจง section_id นี่คือช่องว่างที่ `get_section_text(section_id)` ต้องอุด

---

## ยืนยันจากซอร์สโค้ด MCP server จริง (repo `aekanun2020/2026-GraphRAG-PDPA`)

ตรวจซอร์สของ MCP server (FastMCP, port 8100/mcp) เพื่อยืนยันรากปัญหาก่อนร่าง spec — พบหลักฐานชัดเจน:

### หลักฐาน 1: ตัวบทมาตรา "มีอยู่ครบแล้ว" ใน knowledge graph
`data/pdpa_knowledge_graph.json`: node label `Section` มี **96 ตัว และทุกตัวมี field `text` เป็นตัวบทต้นฉบับ (96/96, ไม่มีตัวไหนว่าง)** ความยาว median ~697 ตัวอักษร (สูงสุด 3000) เช่น `sec_27.text`, `sec_39.text`, `sec_24.text`, `sec_19.text` มีตัวบทเต็ม → **ปัญหาไม่ใช่ "ไม่มีข้อมูล" แต่คือ "ไม่มี tool เปิดอ่านข้อมูลแบบเจาะจง section_id"**

### หลักฐาน 2: tool ที่มีอยู่ "จงใจ" ไม่คืน field `text`
`lightrag/main.py → find_related_sections()` (บรรทัด 115-123) อ่าน `node_data` แต่คืนเฉพาะ `type`/`description`/`label` + edges — **ไม่เคยแตะ field `text`** จึงได้ edge เปล่าตามที่ benchmark เจอ

### หลักฐาน 3: `search_pdpa` คืนตัวบทแบบ semantic top-5 เท่านั้น (ไม่ใช่ by-id)
ตาม `docs/search_pdpa_pipeline.md`: Step 3 (chunk search) คืน original text ของมาตราที่ **ใกล้เคียงเชิงความหมาย** สูงสุด 5 ตัวผ่าน Qdrant similarity — **ไม่รองรับการขอ "ตัวบทของมาตรา X เป๊ะ ๆ"** จึงเป็นเหตุผลที่ค้น "มาตรา 27" แล้วได้ ม.26/79/24 (เพื่อนบ้านเชิง semantic) แทนตัว ม.27

### สรุปข้อยืนยัน
| คำถาม | คำตอบจากซอร์สจริง |
|---|---|
| ตัวบทมาตราอยู่ในระบบไหม? | ✅ มีครบ 96/96 ใน `node.text` |
| `find_related_sections` คืน text ไหม? | ❌ จงใจไม่คืน (คืนแค่ edge + description) |
| `search_pdpa` ขอตัวบทเจาะจง section_id ได้ไหม? | ❌ ได้แค่ top-5 semantic ไม่ใช่ by-id |
| ต้องเขียน tool ใหม่ยากไหม? | 🟢 ง่ายมาก — lookup node by id (reuse fuzzy-match จาก `find_related_sections`) แล้วคืน `node_data['text']` (~10 บรรทัด) |

---

## 🟢 อัปเดต 21 มิ.ย. 2569 — ผลหลังเพิ่ม `get_section_text` (Arm B: prompt เดิม + tool ใหม่)

หลัง implement `get_section_text(section_id)` เป็น MCP Tool ตัวที่ 5 และ rebuild container บนเครื่อง user แล้ว (verified live: `tools/list` แสดง 5 tools, curl test `"27"`→sec_27 และ `sec_999`→`found:false` ผ่านทั้งคู่) — รัน **Q-CCTV-2 ซ้ำ 1 รอบบน `anthropic/claude-sonnet-4.6`** โดย **ยังไม่แก้ TOOLS.md / SKILL.md** (agent ยังไม่ "รู้จัก" tool ใหม่อย่างเป็นทางการ)

### ผลพลิก: Q-CCTV-2 เปลี่ยนจาก ❌ → ✅ ทุกตัวชี้วัด grounding

| ตัวชี้วัด | baseline (ก่อนมี tool) | หลังเพิ่ม tool (Arm B) |
|---|:---:|:---:|
| TRACE_OK | ✅ | ✅ |
| RETRIEVE_OK | ❌ | **✅** |
| GROUND_OK | ❌ | **✅** |
| Legal Correct | ✅ | ✅ |

### หลักฐานสำคัญ (smoking gun ถูกแก้)
รอบ baseline: agent เห็น edge `sec_27 → sec_39` แต่ไม่มี tool ดึงตัวบท → quote "ม.39(6)" จากความจำ
รอบนี้: agent **เรียก `get_section_text` 6 ครั้งเองโดยอัตโนมัติ** (sec_27, sec_24, sec_39, sec_23, sec_32, sec_37, sec_21) ทั้งที่ TOOLS.md/SKILL.md ยังไม่กล่าวถึง tool ตัวนี้เลย — แรงพอจาก docstring ในตัว tool เอง (`★ ใช้ tool นี้ทุกครั้งก่อนอ้างมาตรา`)

- `get_section_text('sec_39')` คืนตัวบทจริงที่มีข้อความ **"(๖) การใช้หรือเปิดเผยตามมาตรา ๒๗ วรรคสาม"** → agent ground ม.39(6) จากหลักฐานจริง (ไม่ใช่ความจำ)
- คำตอบกฎหมายถูกครบ: collect=ม.24(5), use=ม.27วรรคหนึ่ง (compatible purpose), ROPA=ม.27วรรคสาม→ม.39, + ม.23/32/37, อ้าง ม.21 เป็น purpose-limitation ไม่ใช่ฐาน (ผ่าน GATE ม.21)

### ลำดับการเรียก tool (11 calls รอบนี้)
`search_pdpa` ×3 (ม.27/ม.24/CCTV) → `get_related_sections(sec_27)` [เห็น edge sec_27→sec_39] → `get_related_sections(sec_24)` → `get_penalty(sec_27)` → **`get_section_text` ×6** (sec_27, sec_24, sec_39, sec_23, sec_32, sec_37, sec_21)

### นัยต่อ hypothesis
- **H3 (missing-tool): ยืนยัน + แก้สำเร็จด้วย tool อย่างเดียว** — RETRIEVE_OK และ GROUND_OK พลิกเป็น ✅ ทันทีที่มี tool โดยไม่ต้องแตะ prompt เลย
- **H2 (compliance): อ่อนลงอีก** — agent เรียก tool ใหม่เองโดยไม่ถูกสั่งใน skill เลย → ยืนยันว่าไม่ใช่ปัญหา "ขี้เกียจเรียก tool"

### ⚠️ ข้อจำกัดของผลรอบนี้
- รันเพียง **1 รอบ บน Sonnet เท่านั้น** — spec กำหนด **≥ 5 รอบต่อโมเดล (Sonnet + Qwen)** เพื่อยืนยัน consistency ก่อนสรุปเป็น regression baseline ทางการ
- Step 2 (แก้ SKILL.md + TOOLS.md ผูก tool อย่างเป็นทางการ) ยังแนะนำให้ทำต่อ — เพื่อ (ก) documentation, (ข) เสถียรภาพแบบ deterministic ไม่พึ่ง docstring อย่างเดียว
- Step 3 (citation-grounding Hook) ยังจำเป็นตามหลัก "must-happen-every-time = Hook" — docstring แรงพอใน 1 รอบ แต่ไม่การันตีทุกรอบ/ทุกโมเดล

---

## ผลการทดสอบ CCTV Benchmark (baseline — ก่อนแก้ไข)

แต่ละข้อรันแบบ cold start แยก session ประเมิน 2 มิติอิสระ: A = Legal Correctness, B = Grounding Discipline
ตัวชี้วัด: **TRACE_OK** (พยายามเรียก tool retrieve เป้าหมายไหม) · **RETRIEVE_OK** (tool คืน original text เป้าหมายไหม) · **GROUND_OK** (ทุกตัวบทที่อ้าง map กับ retrieved text ไหม)

| คำถาม | TRACE_OK | RETRIEVE_OK | GROUND_OK | Legal | รากปัญหา |
|---|:---:|:---:|:---:|:---:|---|
| Q-CCTV-1 (เก็บ) | ✅* | ❌ | ❌ | ✅ | H3 + H2 (บางมาตราอ้างโดยไม่ call) |
| Q-CCTV-2 (สอบสวน) | ✅ | ❌ | ❌ | ✅ | **H3 ชัดเจน** |
| Q-CCTV-3 (การตลาด) | ✅ | ⚠️ บางส่วน | ❌ | ✅ | **H3 หลัก** |

### Q-CCTV-1 — การเก็บข้อมูล (Collect)
- **Legal ✅:** วินิจฉัยฐานเก็บ = ม.24(5) legitimate interest, ไม่ต้องขอ consent, แยก collect/use ถูก, ไม่ละเมิด GATE ม.21
- **Grounding ❌:** (ก) ค้น "มาตรา 27" แต่ tool คืน ม.26/79/24 แทน = H3 (ค้นด้วยเลขมาตราไม่เสถียร เหมือนเคส ม.82→ม.90 เดิม); (ข) ม.39/23/22/37/32/19 ถูกอ้างโดย **ไม่มี tool call เลย** = H2 (เติมจากความจำ)

### Q-CCTV-2 — การนำไปใช้สอบสวน (Use — compatible purpose)
- **Legal ✅:** การสอบสวน = การใช้ → วินิจฉัยภายใต้ ม.27, compatible purpose ใช้ได้ตาม ม.27 วรรคหนึ่ง, มีหน้าที่บันทึก ROPA ตาม ม.27 วรรคสาม → ม.39(6)
- **Grounding ❌ (smoking gun):** เรียก `get_related_sections(sec_27)` เห็น edge `sec_27 → sec_39` แล้ว **แต่ไม่มี tool retrieve ตัวบท ม.39** → quote "ม.39(6)" จากความจำ = อาการ Q15 ซ้ำเป๊ะ
- รอบนี้ **TRACE ดีกว่า** Q-CCTV-1 (เรียก get_related ครบ) → เป็น **H3 ล้วน ๆ** น้อย H2

### Q-CCTV-3 — การใช้เพื่อการตลาด (Use — new purpose)
- **Legal ✅ (เด่นสุด):** โฆษณา = new purpose, ต้องขอ consent ใหม่ตาม ม.27 วรรคหนึ่ง + ม.19, **GATE ม.21 ผ่าน** (ไม่อ้าง ม.21 เป็นฐานเลย), เพิ่มสิทธิ ม.32(2) direct marketing objection ตรงบริบท
- **Grounding ❌:** เรียก tool **8 ครั้ง** (ขยันที่สุดในสามข้อ) แต่ tool คืนได้แค่ penalty (ม.79) + exemption text ม.24 — ส่วน **ตัวบท ม.19/23/39/30-36/83/84 เติมจากความจำทั้งหมด**

#### Q-CCTV-3: แยกตัวบท grounded vs memory-filled
| ตัวบทในคำตอบ | มาจาก tool? | สถานะ |
|---|:---:|---|
| โทษ ม.79 (6 ด./500k + 1 ปี/1M) | ✅ `get_penalty(sec_27)` | GROUNDED |
| ม.24(5) legitimate interest | ✅ `get_related(sec_24)` description | GROUNDED |
| ม.19 (ชัดแจ้ง/แยกส่วน/ถอนได้/วรรคห้า) | ❌ คืนแค่ `sec_19 → DEFINES → def_consent` | MEMORY |
| ม.23 (รายการแจ้ง) | ❌ ไม่มี tool คืน | MEMORY |
| ม.39 (ROPA 8 รายการ + วรรคสาม) | ❌ ไม่มี original text | MEMORY |
| ม.32(2)/33/34 (สิทธิ) | ❌ คืนแค่ edge `HAS_RIGHT` | MEMORY |
| ม.73 ร้องเรียน | ❌ คืนแค่ `right_complaint → sec_73` | MEMORY |
| ม.83/84 ปรับปกครอง 5M | ❌ ไม่มี tool คืน | MEMORY |

---

## วินิจฉัยรากปัญหา — ตัด hypothesis อื่นทีละตัว

| สมมติฐาน | ผล | เหตุผล |
|---|:---:|---|
| **H4** skill ครอบคลุมไม่พอ (overfit/coverage gap) | ❌ ตัดออก | CCTV คือ pattern เดียวที่ skill ออกแบบ decision tree มาตรง ๆ และ agent ตอบ **ถูกกฎหมายทั้ง 3 ข้อ** (แยก collect/use, compatible vs new purpose, GATE ม.21 ผ่าน) → skill ครอบคลุมแล้ว |
| **H2** โมเดลไม่ยอมเรียก tool (compliance) | ⚠️ อ่อนลงมาก | Q-CCTV-3 เรียก tool 8 ครั้ง, Q-CCTV-2 ตามรอย edge ครบ → ไม่ใช่ "ขี้เกียจ" แต่คือ "เรียกแล้วไม่มีตัวบทให้ดึง" (เหลือ H2 เฉพาะกรณี Q-CCTV-1 ที่อ้างบางมาตราโดยไม่ call) |
| **H1** prompt มีช่องว่าง (item 5 บอก "cite" ไม่ใช่ "retrieve") | ⚠️ รอง | มีส่วนจริง แต่แก้ wording อย่างเดียวไม่พอถ้าไม่มี tool ให้ดึง |
| **H3** ไม่มี tool ดึงตัวบทมาตรา (missing-tool) | ✅ **ยืนยัน** | เกิดซ้ำ **4 รอบติด**: ม.82→90, CCTV-1, CCTV-2, CCTV-3 — `search` คืน chunk ใกล้เคียง, `get_related` คืน edge เปล่า, ไม่มีตัวไหนคืน node text |

### ตรรกะการแยก H2 vs H3 (เส้นแบ่งที่คมที่สุด)
- **TRACE_OK ❌** → โมเดลเลือกไม่เรียก tool เอง = H2 compliance
- **TRACE_OK ✅ แต่ RETRIEVE_OK ❌** → โมเดลพยายามแล้วแต่เครื่องมือไม่เอื้อ = **H3 missing-tool**

ผลทั้ง 3 ข้อตกในแถว H3 (TRACE ✅ / RETRIEVE ❌)

---

## ข้อเสนอการแก้ไข (fix priority)

1. **`get_section_text(section_id)` — ต้องมาก่อนทุกอย่าง (root H3)**
   - คืน original text ของ node มาตรา (รวมวรรคย่อย) ตาม section_id
   - เหตุผล: แม้ agent จะ trace ครบ (8 calls ใน Q3) ก็ยัง ground ไม่ได้ถ้าไม่มีตัวบทให้ดึง
   - พิจารณา retrofit `pdpa_get_related_sections` ให้แนบ node text ของ target ด้วย (ลดจำนวน round-trip)

2. **citation-grounding Hook (Layer 3)**
   - บังคับ: ทุก section ที่ปรากฏในคำตอบต้อง map กับ retrieved text ในรอบเดียวกัน
   - ตามหลัก PyClaw #1 ("anything that must happen every time = Hook, not prompt")
   - ต้องมาหลัง tool พร้อมแล้ว มิฉะนั้น hook จะบล็อกคำตอบที่ ground ไม่ได้เพราะไม่มี tool ให้ใช้

3. **แก้ prompt (SKILL.md) — secondary**
   - แก้ wording item 5 ("ใช้ relation ที่เจอ" → ต้อง retrieve ก่อน cite) ให้สอดคล้องกับ item 7
   - เพิ่ม gate ทั่วไป: "edge = pointer ไม่ใช่หลักฐาน — ต้องเปิดอ่าน node ก่อนอ้าง"

---

## หมายเหตุการใช้เป็น regression baseline
- ตารางผลด้านบนคือ **baseline ก่อนแก้** — รันซ้ำชุดเดิมหลังแต่ละการแก้ (เพิ่ม tool / ผูก hook / แก้ prompt)
- เป้าหมายหลัง improve: **GROUND_OK = ✅ ทุกข้อ** (วัด consistency ≥ 5 รอบต่อโมเดล)
- ablation 2 arm เพื่อแยก H1 vs H3: (A) แก้ prompt อย่างเดียว, (B) แก้ prompt + เพิ่ม tool — เทียบ RETRIEVE_OK
