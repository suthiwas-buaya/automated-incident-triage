r"""
Phase 6 งาน H4 - replay_syslog.py: ส่งข้อความ syslog "บรรทัดเดิม" ซ้ำเข้าระบบ

ทำไมต้องมี: H4 ต้องทดสอบว่า "syslog มาซ้ำแล้วระบบเปิด TT ใบใหม่หรือเกาะใบเดิม"
แต่ IOS จะไม่ส่ง %LINK-3-UPDOWN ซ้ำถ้าสถานะ interface ไม่เปลี่ยน — สั่ง shutdown ซ้ำ
บนพอร์ตที่ปิดอยู่แล้วจึงไม่มีข้อความออกมาเลย ทดสอบด้วยการยิง fault ซ้ำไม่ได้

ไฟล์นี้จึงหยิบ raw_syslog **ของจริง** ที่เคยเข้าระบบมาแล้วจาก tickets.db (อ่านอย่างเดียว)
แล้วส่งซ้ำเข้า UDP 514 แบบ byte ต่อ byte เหมือนเดิมทุกตัวอักษร
= จำลองกรณี syslog UDP มาซ้ำ (duplicate) ซึ่งเกิดได้จริงบนเครือข่าย

*** ไม่แก้ฐานข้อมูลเอง *** ผลที่เกิดขึ้นมาจาก syslog_server.py ตัดสินล้วนๆ

วิธีรัน:
    py replay_syslog.py INC-20260821-0001            ส่งซ้ำ 2 ครั้ง (ค่าตั้งต้น)
    py replay_syslog.py INC-20260821-0001 --times 3  ส่งซ้ำ 3 ครั้ง
    py replay_syslog.py INC-20260821-0001 --show     ดูข้อความอย่างเดียว ไม่ส่ง

⚠️ ต้องเปิด syslog_server.py ค้างไว้ก่อน ไม่งั้นข้อความจะหายไปเฉยๆ (UDP ไม่มีการตอบกลับ)
"""
import os
import socket
import sqlite3
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'tickets.db')

TARGET_HOST = '127.0.0.1'
TARGET_PORT = 514
GAP_SECONDS = 1.0          # เว้นระยะให้ server ประมวลผลใบก่อนหน้าจบก่อน


def load_raw_syslog(incident_number):
    if not os.path.exists(DB_PATH):
        print(f"\n  ❌ ไม่พบ {DB_PATH}\n")
        sys.exit(1)
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT raw_syslog, node, interface FROM tickets WHERE incident_number = ?",
        (incident_number,)
    ).fetchone()
    conn.close()

    if row is None:
        print(f"\n  ❌ ไม่พบ {incident_number} ในฐานข้อมูล\n")
        sys.exit(1)
    if not row['raw_syslog']:
        print(f"\n  ❌ {incident_number} ไม่มี raw_syslog เก็บไว้ (น่าจะเป็นใบที่คนแจ้งเอง MAN-*)")
        print("     เลือกใบที่ขึ้นต้นด้วย INC- แทน\n")
        sys.exit(1)
    return dict(row)


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith('-'):
        print(__doc__)
        sys.exit(1)

    incident_number = args[0].strip()
    times = 2
    if '--times' in args:
        times = int(args[args.index('--times') + 1])
    show_only = '--show' in args

    row = load_raw_syslog(incident_number)
    message = row['raw_syslog']

    print()
    print('=' * 74)
    print(f"  ข้อความต้นฉบับจาก {incident_number}  ({row['node']} {row['interface'] or '-'})")
    print('=' * 74)
    print(f"  {message}")
    print('=' * 74)

    if show_only:
        print("\n  โหมด --show : ไม่ได้ส่งอะไรออกไป\n")
        return

    print(f"\n  กำลังส่งซ้ำ {times} ครั้ง ไปที่ UDP {TARGET_HOST}:{TARGET_PORT}")
    print("  (ถ้า syslog_server.py ไม่ได้เปิดอยู่ ข้อความจะหายไปเงียบๆ — UDP ไม่มีการตอบกลับ)")
    print()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for i in range(1, times + 1):
            sock.sendto(message.encode('utf-8'), (TARGET_HOST, TARGET_PORT))
            print(f"  ส่งครั้งที่ {i}/{times} แล้ว")
            if i < times:
                time.sleep(GAP_SECONDS)
    finally:
        sock.close()

    print()
    print('-' * 74)
    print("  ต่อไปให้ตรวจที่หน้าต่าง syslog_server.py และสั่ง:")
    print(f"      py check_h.py itf {row['node']} {row['interface']}")
    print("  ✅ ผ่าน = จำนวน TT ของ node+interface นี้ **ไม่เพิ่ม** (เกาะใบเดิม)")
    print("  ❌ ไม่ผ่าน = มี TT ใบใหม่โผล่มาทุกครั้งที่ส่ง")
    print('-' * 74)
    print()


if __name__ == '__main__':
    main()
