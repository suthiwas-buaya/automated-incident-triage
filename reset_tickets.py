"""
ลบ TT ทั้งหมดเพื่อเริ่มทดสอบใหม่จากศูนย์

⚠️ ลบเฉพาะ TT และไทม์ไลน์การทำงาน — **ไม่แตะบัญชีผู้ใช้** (ไม่ต้องสมัคร/อนุมัติใหม่)

วิธีรัน:
    py reset_tickets.py
"""
from lib import db

db.init_db()

count = len(db.list_all_tickets())

if count == 0:
    print("ไม่มี TT อยู่แล้ว — ฐานข้อมูลว่างเปล่า")
else:
    print(f"กำลังจะลบ TT ทั้งหมด {count} ใบ")
    answer = input("ยืนยันหรือไม่? พิมพ์ y แล้วกด Enter: ").strip().lower()

    if answer == 'y':
        deleted = db.clear_all_tickets()
        print(f"\n✅ ลบเรียบร้อย {deleted} ใบ — บัญชีผู้ใช้ยังอยู่ครบ")
        print("   ถ้าเปิดหน้าเว็บอยู่ ให้กด Refresh หรือรอ 5 วิให้มันดึงใหม่เอง")
    else:
        print("\nยกเลิก — ไม่ได้ลบอะไร")
