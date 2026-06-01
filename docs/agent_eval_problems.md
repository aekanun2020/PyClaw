# DB Agent Evaluation — โจทย์ทดสอบ (TestDB / Loan Domain)

ชุดโจทย์ที่ใช้ประเมิน DB Agent บนฐานข้อมูล TestDB (โดเมนสินเชื่อ: `loans_fact` ~1.4M แถว + dimension tables: `application_type_dim`, `emp_length_dim`, `home_ownership_dim`, `issue_d_dim`, `loan_status_dim`)

ผลการประเมินอยู่ใน [`docs/agent_report.md`](./agent_report.md)

---

## โจทย์ที่ 1 (Medium)

หา segment ที่มี bad loan rate สูงสุด โดย:
- bad loan rate = จำนวน loan ที่มี status เป็น **Charged Off** หรือ **Default** หารด้วย total loan ของ segment นั้น × 100
- segment คือ **home_ownership × emp_length** โดย JOIN `home_ownership_dim` ผ่าน `home_ownership_id` และ JOIN `emp_length_dim` ผ่าน `emp_length_id` แล้ว GROUP BY ทั้งสอง column
- แสดงเฉพาะ segment ที่มี loan ≥ 1,000 รายการ
- แสดง top 5 segment พร้อม `avg_loan_amnt`
- เปรียบเทียบกับ overall bad loan rate ของ portfolio ทั้งหมด

---

## โจทย์ที่ 2 (Hard)

หาว่าปีไหนมี volume สูงและ bad loan rate สูงด้วย โดย:
- bad loan คือ status = **Charged Off**, **Default**, หรือ **Late (31-120 days)**
- high volume คือปีที่มี loan มากกว่า avg loan ต่อปีของ portfolio
- คำนวณ `risk-adjusted score = (bad_loan_rate × 0.6) + (volume_percentile × 0.4)` โดย `bad_loan_rate` และ `volume_percentile` ทั้งคู่ต้องอยู่ในช่วง **0–1** ก่อนนำมาคำนวณ
  - `bad_loan_rate` คือค่าดิบที่คำนวณได้ซึ่งอยู่ใน 0–1 อยู่แล้ว (ไม่ต้อง normalize ซ้ำ)
  - `volume_percentile` ใช้ `PERCENT_RANK()` เรียงตาม `total_loans`
- จัด ranking ทุกปีจากสูงไปต่ำ และระบุปีที่ "น่าเป็นห่วง" คือปีที่มี score > 0.5

---

## โจทย์ที่ 3 (Hard)

เปรียบเทียบว่า Joint application ดีกว่า Individual จริงไหม โดยวัดจาก 4 metric:
1. **bad loan rate** (bad = Charged Off หรือ Default)
2. **avg int_rate**
3. **avg dti** — Individual ใช้ `dti`, Joint App ใช้ `dti_joint` (กรอง NULL ออกก่อน)
4. **funded_amnt / loan_amnt ratio**

นับว่า Joint ชนะกี่ metric จาก 4 โดย "ชนะ" หมายถึง:
- bad loan rate **ต่ำกว่า**
- int_rate **ต่ำกว่า**
- dti **ต่ำกว่า**
- ratio **สูงกว่า**

แล้วสรุปว่าชนะหรือแพ้โดยรวม (ชนะ = ชนะ ≥ 3 metric จาก 4)

---

## โจทย์ที่ 4 (Expert)

ธนาคารต้องการหา segment ผู้กู้ที่คุ้มค่าที่สุด โดยวัดจาก 3 มิติ:
- **yield** — `avg_int_rate` สูง
- **safety** — `bad_loan_rate` ต่ำ (bad = 'Charged Off' หรือ 'Default')
- **income** — `avg_annual_inc` สูง

วิธี:
- JOIN `loans_fact` กับ `home_ownership_dim`, `emp_length_dim`, `loan_status_dim` ผ่าน `_id` columns
- GROUP BY `home_ownership` และ `emp_length`
- กรองเฉพาะ segment ที่มี loan ≥ 500
- normalize ทุก metric ด้วย max ของทุก segment ผ่าน CROSS JOIN
- คำนวณ `composite_score = (yield_score × 0.4) + (safety_score × 0.4) + (income_score × 0.2)` โดย `safety_score = 1 − (bad_loan_rate / max_bad_loan_rate)`
- แสดง top 3 segment พร้อม score breakdown

---

## โจทย์ที่ 5 (Expert)

ธนาคารต้องการพิสูจน์ว่า "รายได้สูง ผิดนัดน้อยกว่า" จริงไหม โดย:
- JOIN `loans_fact` กับ `application_type_dim`, `home_ownership_dim`, `loan_status_dim` ผ่าน `_id` columns
- แยก Individual ใช้ `annual_inc` และ Joint App ใช้ `annual_inc_joint` (กรอง NULL ออกก่อน)
- แบ่งเป็น income quartile Q1–Q4 ด้วย `NTILE(4) OVER (PARTITION BY application_type ORDER BY income)`
- คำนวณ `bad_loan_rate = bad loans / COUNT(*)` โดย bad = 'Charged Off' หรือ 'Default'
- ทำแยกทุก `home_ownership`
- สรุปว่า Q4 < Q1 จริงไหมในแต่ละ group
