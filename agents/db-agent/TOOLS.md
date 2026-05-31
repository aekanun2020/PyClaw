# TOOLS.md — DB Agent

## กฎการใช้ tools
- ใช้ `db_get_database_info_tool` ก่อนเพื่อดูโครงสร้าง DB
- ใช้ `db_preview_table` เพื่อดูตัวอย่างข้อมูลก่อน query จริง
- SELECT เท่านั้น — ห้าม DML/DDL
- ถ้า query ซับซ้อน ให้แสดง SQL ให้ user ดูก่อนรัน
- ใช้ `db_refresh_db_cache` เมื่อ user บอกว่าข้อมูลไม่ตรง
- ดึง schema/column metadata เท่าที่จำเป็นต่อ query ที่จะเขียนเท่านั้น อย่าดึง internal/system fields (เช่น `sys.columns` แบบเต็ม) ที่ไม่ได้ใช้ — ประหยัด token และ latency
- เวลานับจำนวน "การเปลี่ยนแปลง" (เช่น จำนวนครั้งที่พนักงานเปลี่ยนตำแหน่ง) อย่า assume ว่าทุก row = 1 การเปลี่ยน ให้ count distinct transition จริง โดยใช้ `LAG` เทียบ row ก่อนหน้า แทน `COUNT(*) - 1` แบบ naive (กัน duplicate/data dirty)

```sql
-- นับจำนวนครั้งที่เปลี่ยนตำแหน่งจริง (ไม่นับ row ที่ตำแหน่งซ้ำกับครั้งก่อน)
WITH ph AS (
  SELECT employee_id, position,
         LAG(position) OVER (PARTITION BY employee_id ORDER BY start_date) AS prev_position
  FROM position_history
)
SELECT employee_id, COUNT(*) AS position_changes
FROM ph
WHERE prev_position IS NOT NULL AND position <> prev_position
GROUP BY employee_id;
```

หมายเหตุ: ปรับชื่อ column (`position` / `start_date` / `employee_id`) ให้ตรง schema จริงที่ได้จาก `db_get_database_info_tool` ก่อนรัน
