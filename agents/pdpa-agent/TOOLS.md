# TOOLS.md — PDPA Agent

## กฎการใช้ tools
- call `pdpa_get_penalty` ก่อนระบุตัวเลขโทษปรับ
- call `pdpa_search_pdpa` ก่อนอ้างมาตรา
- call `pdpa_get_related_sections` เมื่อต้องการดูมาตราที่เกี่ยวข้อง
- ถ้า tool ไม่คืนข้อมูล ระบุว่า "ไม่มีข้อมูลใน Graph" แล้วตอบจากข้อมูลที่มี
- ห้ามเรียก tool เดิมด้วย query เดิมซ้ำ
