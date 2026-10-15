"""
lib — อะไหล่ของระบบ (ไม่ต้องรันเอง ถูกเรียกใช้เบื้องหลัง)

    db.py             เก็บ/อ่าน/ปิด TT ใน SQLite
    syslog_parser.py  แปลงข้อความ syslog + กฎตัดสินว่า event ไหนคือ fault

ไฟล์ที่รันเองอยู่ที่โฟลเดอร์หลัก: syslog_server.py, fault_injection.py, show_tickets.py
"""
