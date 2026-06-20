# Design Proposal: ให้ PyClaw ใช้ skill จาก marketplace ได้

> สถานะ: **ข้อเสนอ (design only)** — ยังไม่เขียนโค้ด รอความเห็นชอบก่อนลงมือ
> เป้าหมาย: อุดจุดอ่อนใหญ่สุดของ PyClaw (ไม่มี ecosystem) โดยให้ดึง/ติดตั้ง skill จากแหล่งภายนอกได้ โดยไม่ทิ้งหลักการ "Prompt ≠ Policy"
> อ้างอิงโค้ดปัจจุบันบน `main` commit `efb4808`

---

## 1. สรุปสั้น (ปัญหา → ทางแก้)

วันนี้ PyClaw มีแค่ `SkillRegistry.scan()` ที่อ่าน `SKILL.md` จาก **โฟลเดอร์ในเครื่อง** เท่านั้น (`pyclaw/skills/registry.py:67-88`) — ไม่มีทางดึง skill จากภายนอกเลย

ทางแก้: เพิ่ม **"skill installer"** ที่ดึง skill จากแหล่งที่เชื่อถือได้ → ตรวจความปลอดภัย → วางลงโฟลเดอร์ local → จากนั้น `scan()` เดิมก็ทำงานต่อได้ทันที **โดยไม่ต้องแตะ runtime หรือ format ที่มีอยู่**

นี่เป็นงาน "เพิ่ม layer ใหม่" ไม่ใช่ "รื้อของเดิม"

---

## 2. แหล่ง skill (balance: ดัง + เชื่อถือได้)

| แหล่ง | ดัง? | เชื่อถือได้? | หมายเหตุ |
|---|---|---|---|
| **GitHub repo (git URL)** | ⭐⭐⭐ | ⭐⭐ (ขึ้นกับเจ้าของ) | ดึงตรง ใครก็ publish ได้ — ต้องตรวจเอง |
| **agentskills.io** | ⭐⭐⭐ | ⭐⭐⭐ | open standard ของ Anthropic, มี spec ทางการ |
| **ClawHub** | ⭐⭐ | ⭐⭐⭐ | **มี security analysis ในตัว** + versioning แบบ semver |
| **skills.sh** | ⭐⭐⭐ | ⭐⭐ | directory ใหญ่ (60,000+ installs) รองรับ 38+ agents |

**ข้อเสนอ:** เริ่มจาก **2 แหล่งที่ balance ดีสุด**:
1. **GitHub repo** (ดังสุด, ครอบคลุมที่สุด — ทุก marketplace สุดท้ายก็ชี้ไป git)
2. **ClawHub** (เชื่อถือสุด — มี security review ฝั่ง server ช่วยเราอีกชั้น)

แหล่งอื่น (skills.sh, agentskills.io) ออกแบบให้เพิ่มทีหลังได้ผ่าน interface เดียวกัน (ดูข้อ 5)

---

## 3. Compatibility กับ format ของ PyClaw

### ✅ เข้ากันได้ทันที
Agent Skills spec ([agentskills.io/specification](https://agentskills.io/specification)) กำหนด required แค่ `name` + `description` — **ตรงกับที่ PyClaw parse อยู่แล้ว** (`registry.py:53-54`) skill จาก marketplace ส่วนใหญ่จึงโหลดได้เลย

### ⚠️ ช่องว่างที่ต้องอุด (ตรวจจากโค้ดจริง)

**3.1 parser อ่าน nested YAML ไม่ได้**
`parse_frontmatter` ปัจจุบันอ่านแค่ `key: value` บรรทัดเดียว (`registry.py:34-38`) แต่ spec มี field แบบ nested เช่น:
```yaml
metadata:
  version: 1.0.0
  copyright: ...
```
parser เดิมจะอ่าน `metadata:` ไม่ออก → **ต้องเปลี่ยนไปใช้ YAML parser จริง** (มี `pyyaml` ใน repo อยู่แล้ว ใช้ที่ `plugins/loader.py`)

**3.2 field ตาม spec ที่ PyClaw ยังไม่มี**
| field (spec) | PyClaw มี? | ควรทำ |
|---|---|---|
| `name`, `description` | ✅ | คงไว้ |
| `license` | ❌ | เพิ่ม (เก็บไว้แสดง provenance) |
| `compatibility` | ❌ | เพิ่ม (ระบุ network/binary ที่ skill ต้องใช้ — ใช้ประกอบการตรวจ security) |
| `allowed-tools` | ❌ | **เพิ่ม — สำคัญ**: map เข้า `PermissionPolicy.allowed_tools` ที่มีอยู่ (`plugins/permissions.py:19`) ได้ตรง ๆ |
| `metadata.version` | ⚠️ มี `version` แบบ flat | รองรับทั้งสองรูปแบบ |

**3.3 mapping invocation**
spec ไม่มีแนวคิด `auto/manual/always` ของ PyClaw → skill ภายนอกที่ไม่ระบุให้ default เป็น `AUTO` (ตรงพฤติกรรม spec ที่ trigger ด้วย description) — ไม่ต้องแก้อะไร เป็น backward-compatible

---

## 4. Security layers (หัวใจของ design — ตรงกับที่อธิบายไป)

skill จากเน็ตคือ "โค้ด/คำสั่งจากคนแปลกหน้า" — Snyk เจอ 36% มีช่องโหว่ เราอุดด้วย **4 ด่าน** เรียงจากตอนดึงถึงตอนรัน:

### ด่านที่ 1 — แหล่งที่เชื่อถือได้ (install time)
- รับเฉพาะ git URL จาก allowlist host (เช่น `github.com`) + ClawHub
- บันทึก provenance (source URL + commit SHA + เวลา) ลง manifest local เพื่อ audit ย้อนหลัง

### ด่านที่ 2 — Static scan ก่อนวาง (install time) ← **อุดช่องว่างที่ระบุไว้ใน critique review**
สแกนทุกไฟล์ใน skill bundle ก่อน activate หา pattern อันตราย:
- `curl ... | bash` / `wget ... | sh` (โหลดโค้ดมารันตอน runtime)
- secret pattern (`sk-`, `AKIA`, `-----BEGIN ... KEY-----`)
- prompt-injection pattern ใน SKILL.md (เช่น "ignore previous", "send ... to http")
- คำสั่งเขียนทับ memory (`AGENT_MEMORY.md`, `AUTO_MEMORY.md`, `CLAUDE.md`)
- เจอ → **BLOCK การติดตั้ง** (fail-closed) ไม่ใช่แค่เตือน

### ด่านที่ 3 — Manual approve (install time)
- แสดงสรุปผล scan + provenance + `allowed-tools` ที่ skill ขอ แล้วให้คนกดยืนยัน
- ใช้ `HITLGate` ที่มีอยู่ (`runtime/hitl.py`) — fail-closed: timeout/error = ไม่ติดตั้ง

### ด่านที่ 4 — Runtime guardrails (run time) ← **มีอยู่แล้ว ไม่ต้องสร้างใหม่**
แม้ skill ร้ายเล็ดลอด 3 ด่านแรกมาได้ เกราะ runtime เดิมยังกันอยู่:
- `allowed-tools` ของ skill → allowlist ใน `PermissionPolicy` (`permissions.py:22-27`)
- guard บล็อก path อันตราย (`pyclaw_hooks/guards.py:25-95`)
- AUTO_MEMORY cap กันการฝังตัวถาวร (`memory/loader.py:127-156`)
- audit ทุก tool call (`runtime/audit.py`)

> **สรุปปรัชญา:** ด่าน 1-3 = "ป้องกันก่อนเข้า", ด่าน 4 = "defense in depth ตอนรัน" — สอดคล้อง Prompt≠Policy ทุกด่านเป็นโค้ด deterministic ไม่ใช่คำขอใน prompt

---

## 5. สถาปัตยกรรมที่เสนอ (ภาพรวม — ยังไม่ใช่โค้ด)

```
ผู้ใช้: pyclaw skill install github.com/user/cool-skill
            │
            ▼
   [SkillSource] ── interface เดียว, มีหลาย backend (Git / ClawHub / ...)
            │  ดึง bundle ลง temp dir
            ▼
   [SkillScanner] ── static scan (ด่าน 2) → ถ้าเจอภัย = BLOCK
            │
            ▼
   [HITLGate] ── แสดงผล scan + ขออนุมัติ (ด่าน 3)  [ใช้ของเดิม]
            │  อนุมัติ
            ▼
   วางลง ./skills/<name>/  + เขียน provenance ลง installed manifest
            │
            ▼
   [SkillRegistry.scan()] ── โหลดเข้า registry  [ของเดิม ไม่แก้]
            │
            ▼
   runtime guardrails (ด่าน 4)  [ของเดิม ไม่แก้]
```

**ไฟล์ใหม่ที่คาดว่าจะเพิ่ม (อยู่ใน `pyclaw/skills/`):**
- `sources.py` — `SkillSource` interface + `GitSource`, `ClawHubSource` (เพิ่มแหล่งใหม่ทีหลังได้)
- `scanner.py` — static scanner (ด่าน 2)
- `installer.py` — ร้อย source → scan → approve → วางไฟล์ + manifest
- (แก้) `registry.py` — เปลี่ยน `parse_frontmatter` ไปใช้ YAML parser จริง + เพิ่ม field `license`/`compatibility`/`allowed-tools`
- (เพิ่ม) `cli.py` — คำสั่ง `pyclaw skill install/list/remove`

**สิ่งที่ไม่แตะ:** core loop, hook engine, permission engine, audit, HITL — ทั้งหมดถูก reuse ไม่แก้

---

## 6. แผนทำเป็นเฟส (ลด risk, แต่ละเฟสเป็น PR เดี่ยว ๆ ทดสอบได้)

| เฟส | เนื้อหา | ผลลัพธ์ที่ verify ได้ |
|---|---|---|
| **0** | เปลี่ยน parser เป็น YAML จริง + เพิ่ม field spec (`license`/`compatibility`/`allowed-tools`) | เดิมยัง pass + parse skill ตาม spec ได้ |
| **1** | `SkillSource` + `GitSource` (ดึงจาก GitHub) | install จาก git ได้ลง local |
| **2** | `SkillScanner` (ด่าน 2) เชื่อมเข้า installer | skill ร้ายถูกบล็อกตอนติดตั้ง (มี test) |
| **3** | HITL approve (ด่าน 3) + `allowed-tools`→PermissionPolicy | คนต้องอนุมัติ + skill ถูกจำกัด tool จริง |
| **4** | `ClawHubSource` + manifest + CLI `skill list/remove` | จัดการ skill ที่ติดตั้งครบวงจร |

แต่ละเฟสเป็น docs-only/code PR แยก ผ่าน leak-scan + pytest ก่อน merge ตามขั้นตอนเดิม

---

## 7. คำถามที่ต้องตัดสินใจก่อนเริ่มโค้ด

1. **ขอบเขตเฟสแรก:** เอาแค่เฟส 0-1 (parse spec + install จาก GitHub) ก่อน แล้วค่อยต่อ security เฟส 2-3 หรืออยากได้ security ครบตั้งแต่แรก?
2. **scanner เข้มแค่ไหน:** บล็อกทันทีเมื่อเจอ pattern (เข้ม, อาจ false-positive) หรือเตือน+ให้คนตัดสิน (ยืดหยุ่นกว่า)?
3. **`allowed-tools` ที่ skill ไม่ได้ระบุ:** ให้ default เป็น "ห้ามใช้ tool ใด ๆ จนกว่าจะอนุมัติ" (เข้ม) หรือ "ใช้ได้ตาม policy รวม" (สะดวก)?

---

*ตรวจสอบกับโค้ด PyClaw บน `main` commit `efb4808` | อ้างอิง spec: [agentskills.io/specification](https://agentskills.io/specification), [github.com/openclaw/clawhub](https://github.com/openclaw/clawhub) | ร่าง 2 มิ.ย. 2569*
