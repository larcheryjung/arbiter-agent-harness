# Arbiter repo: อ่านก่อนเริ่ม

1. อ่าน [README.md](README.md) ก่อน (สถานะ, roadmap, โครง repo) อ่าน DIRECTION.md / LESSONS.md / docs/*.html เฉพาะส่วนที่งานต้องใช้
2. งานแก้ arbiter ต้องผ่าน Gate 1 (คุณก้องอนุมัติแผน) และ Gate 2 (อนุมัติผลเทสต์จริง) ห้ามแก้โค้ดก่อน Gate 1
3. ตรวจด้วย `python -m unittest discover -s tests` ทุกครั้งก่อนรายงานผล
4. epolicy เรียก `bin/arbiter.py` จากโฟลเดอร์นี้โดยตรง แก้แล้วมีผลทันที ถามก่อนว่ามีการ์ดกำลังรันอยู่หรือไม่
5. จบงานทุกครั้ง: อัปเดต README.md (สถานะ/roadmap) และคู่มือ docs/ ถ้าเวอร์ชันหรือแผนเปลี่ยน (docs/roadmap.html, ตัวเลขใน docs/index.html, บรรทัดเวอร์ชันใน nav ทุกหน้า, หน้าที่เนื้อหาเปลี่ยน), จด LESSONS.md
