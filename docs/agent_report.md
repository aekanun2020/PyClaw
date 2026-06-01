# DB Agent Evaluation Report
**วันที่ทดสอบ:** 1 มิถุนายน 2569  
**Database:** TestDB (Star Schema, 6 ตาราง, ~1.4M loans)  
**ชุดโจทย์:** 5 ข้อ ระดับ Medium → Expert

---

## สรุปผลรวม

| รอบ | ผ่าน | ไม่ผ่าน |
|-----|------|---------|
| ก่อน git pull | 2/5 | 3/5 |
| หลัง git pull | 5/5 | 0/5 |

---

## ผลรายโจทย์

### โจทย์ 1 — Bad Loan Rate by Segment (Medium)
**ผล:** ✅ ผ่านทั้งก่อนและหลัง git pull

SQL JOIN ครบ 3 ตาราง, HAVING, avg_loan_amnt ถูกต้อง ตัวเลขตรงกับ ground truth ทุก row recover จาก T-SQL syntax error (FETCH FIRST → TOP) ได้เองโดยไม่ต้องให้ user แจ้ง

---

### โจทย์ 2 — Risk-Adjusted Score by Year (Hard)
**ก่อน git pull:** ❌ ไม่ผ่าน  
**หลัง git pull:** ✅ ผ่าน

**ปัญหา (ก่อน git pull):** orchestrator แปลงโจทย์ก่อนส่งให้ db-agent โดยเพิ่ม step normalize bad_loan_rate ด้วย min-max ทั้งที่โจทย์กำหนดให้ใช้ค่าดิบ 0–1 ส่งผลให้ risk_score พองออกนอกช่วงที่ควรเป็น และ flag ปีน่าเป็นห่วงผิด (3 ปี แทนที่จะเป็น 0 ปี)

**หมายเหตุ:** เมื่อ bypass orchestrator แล้วส่งตรงถึง db-agent ก็ยังเกิด bug เดิม แสดงว่าปัญหาอยู่ทั้งสองชั้น ทั้ง orchestrator และ db-agent

**หลัง git pull:** orchestrator ส่งสูตรถูกต้อง db-agent ใช้ bad_loan_rate ค่าดิบ ผลตรงกับ ground truth ทุกตัว ไม่มีปีไหน > 0.5

---

### โจทย์ 3 — Joint vs Individual Comparison (Hard)
**ผล:** ✅ ผ่านทั้งก่อนและหลัง git pull

recover จาก error ได้ 3 ครั้ง รวมถึง query DISTINCT เพื่อตรวจชื่อ 'Joint App' เองก่อนรันใหม่ ตัวเลขถูกทุก metric สรุปชนะ/แพ้ตามเกณฑ์ที่โจทย์กำหนดถูกต้อง

---

### โจทย์ 4 — Composite Score Segment Ranking (Expert)
**ก่อน git pull:** ❌ ไม่ผ่าน  
**หลัง git pull:** ✅ ผ่าน

**ปัญหา (ก่อน git pull):** operator precedence ผิดในสูตร safety term วงเล็บหายทำให้ `1.0` ถูกบวกตรงๆ โดยไม่คูณ weight ก่อน ส่งผลให้ composite_score พองเกิน 1 (สูงสุด 1.359) ทั้งที่ทุก component อยู่ใน 0–1 และ weight รวม = 1.0 ตัวเลขดิบ (yield/safety/income score) ถูกต้องตลอด แต่ final score ผิด

**หลัง git pull:** สูตรถูกต้อง composite_score ไม่เกิน 1 top 3 segment ตรงกับ ground truth

---

### โจทย์ 5 — Income Quartile vs Bad Loan Rate (Expert)
**ก่อน git pull:** ❌ ไม่ผ่าน  
**หลัง git pull:** ✅ ผ่าน

**ปัญหา (ก่อน git pull):** agent ไม่รัน query เลย แต่แต่งตัวเลขขึ้นมาสรุปว่า "จริง" โดยตัวเลขที่แต่งขึ้น เช่น MORTGAGE Q1 = 20% ผิดจากความเป็นจริง (11.29%) มาก เป็น hallucination ที่อันตรายที่สุดในชุดทดสอบนี้เพราะสรุปถูกโดยบังเอิญ ไม่ใช่จากข้อมูล

**หลัง git pull:** รัน query จริง NTILE partition ถูก กรอง NULL ก่อนคำนวณ Joint App ตัวเลขตรงกับ ground truth และสรุป Q4 < Q1 ถูกต้องทุก group ที่มี sample พอ

---

## Pattern ที่พบตลอดการทดสอบ

### จุดแข็ง
- SQL execution quality ดีตั้งแต่ต้น JOIN หลายตาราง, CTE, window function, NTILE ทำได้ถูกต้อง
- recover จาก SQL error ได้เองโดยไม่ต้องให้ user แจ้ง
- ตรวจสอบ schema และชื่อ column จริงก่อนรัน (เช่น DISTINCT เพื่อหาชื่อ application_type)

### จุดอ่อน (ก่อน git pull)
- Orchestrator แปลงโจทย์ก่อนส่ง และเพิ่ม assumption ที่โจทย์ไม่ได้ระบุ
- Agent hallucinate ตัวเลขแทนการรัน query จริง
- Operator precedence error ใน composite formula

---

## ข้อสังเกตเพิ่มเติม

SOUL.md และ TOOLS.md ที่ปรับในระหว่างการทดสอบช่วยในบางจุด โดยเฉพาะ reasoning integrity (โจทย์ 3 สรุปถูกขึ้น) และ unit awareness แต่ปัญหาหลักของโจทย์ 2, 4, 5 ถูกแก้จาก code patch ใน cli.py ไม่ใช่จาก prompt engineering เพียงอย่างเดียว
