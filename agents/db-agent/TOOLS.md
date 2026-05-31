# TOOLS.md — DB Agent

## กฎการใช้ tools
- ใช้ `db_get_database_info_tool` ก่อนเพื่อดูโครงสร้าง DB
- ใช้ `db_preview_table` เพื่อดูตัวอย่างข้อมูลก่อน query จริง
- SELECT เท่านั้น — ห้าม DML/DDL
- ถ้า query ซับซ้อน ให้แสดง SQL ให้ user ดูก่อนรัน
- ใช้ `db_refresh_db_cache` เมื่อ user บอกว่าข้อมูลไม่ตรง
