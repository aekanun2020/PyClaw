---
name: live-schema-discipline
description: ใช้เสมอเมื่อ db-agent จะเขียนหรือรัน SQL query ผ่าน db_execute_query_tool. บังคับให้เรียก db_get_database_context ดึง schema ปัจจุบันก่อนทุกครั้ง และห้ามอ้างชื่อตาราง/คอลัมน์จากความจำหรือบทสนทนาก่อนหน้า.
invocation: always
---

# Live Schema Discipline

กฎเหล็ก: ห้ามเขียน SQL จากความจำ — ทุก query ต้องอิง schema สดที่เพิ่งดึงมาเท่านั้น.

## Gotcha (อ่านก่อนทุกครั้ง)
schema ของฐานข้อมูลนี้ไม่คงที่ — เปลี่ยนได้ และเคยเปลี่ยนชุดตาราง/คอลัมน์มาแล้วจริงระหว่างการใช้งาน. ห้ามสมมติว่ามีตารางหรือคอลัมน์ใดอยู่ ห้ามอ้างชื่อจากความจำหรือจากบทสนทนาก่อนหน้าโดยเด็ดขาด.

## Workflow (บังคับ)
1. ถ้ายังไม่ได้ดึง schema สดในบทสนทนานี้ → เรียก `db_get_database_context` ก่อน (tool นี้ให้ schema เต็ม + relationships + T-SQL syntax guide)
2. ใช้เฉพาะ table/column ที่ context ส่งกลับมาเท่านั้น แล้วค่อยรันผ่าน `db_execute_query_tool`
3. ถ้า entity ที่ผู้ใช้ถามไม่มีใน schema ปัจจุบัน → บอกตรงๆ ว่าไม่มี อย่าเดา อย่าสร้างตารางสมมติ
4. ถ้า schema เปลี่ยนระหว่างบทสนทนา → ยึด schema ล่าสุดที่ดึงมาเสมอ

## Validation (ก่อนส่ง query)
ตรวจว่าทุกชื่อตารางและคอลัมน์ใน query ปรากฏใน schema ที่ `db_get_database_context` ส่งกลับ. ถ้ามีชื่อใดไม่ตรง → หยุด แก้ให้ตรง หรือแจ้งผู้ใช้ว่าไม่มีข้อมูลนั้น.

## Recovery (เมื่อ query ล้มเหลว)
ถ้า query error เพราะอ้างตาราง/คอลัมน์ที่ไม่มี (เช่น schema เปลี่ยนกลางทาง) → เรียก `db_refresh_db_cache` แล้ว `db_get_database_context` ใหม่เพื่อดึง schema ล่าสุด จากนั้นเขียน query ใหม่จาก schema ที่อัปเดต. ห้ามเดาชื่อที่ถูกเพื่อแก้ error.
