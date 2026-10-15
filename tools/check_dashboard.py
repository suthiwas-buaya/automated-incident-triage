"""
เครื่องมือวินิจฉัย: ทำไม TT ไม่โผล่บน Dashboard

ตรวจ 4 อย่างในครั้งเดียว:
  1. tickets.db อยู่ที่ไหน มี TT กี่ใบ
  2. TT แต่ละใบถูกจำแนกเข้ากลุ่มไหน  <- ถ้าอยู่คนละกลุ่มกับแท็บที่เปิดดู จะไม่เห็น
  3. มี TT ที่ไม่มี Owner Group ไหม (พวกนี้ไปกองที่หน้า Triage ไม่ได้อยู่ในกลุ่ม)
  4. Dashboard API เปิดอยู่ไหม

รัน:  py check_dashboard.py
(ไม่ต้องเปิด lab ไม่แก้ข้อมูลใดๆ อ่านอย่างเดียว)
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ขึ้นไป 1 ชั้นเพราะไฟล์นี้อยู่ใน tools/
DB_PATH = os.path.join(PROJECT_ROOT, 'tickets.db')
API_BASE = 'http://127.0.0.1:8000'

# 4 กลุ่มที่ Dashboard รู้จัก (ตรงกับ DB_OWNER_GROUPS ใน app.py)
DASHBOARD_GROUPS = ['Network-Core', 'Network-Access', 'Transmission', 'NOC-Monitoring']

# แท็บที่ Dashboard เปิดมาเป็นค่าเริ่มต้น (script.js: detailTag)
DEFAULT_TAB = 'Network-Core'

line = '=' * 68


def check_db():
    print(line)
    print(" 1) ฐานข้อมูล")
    print(line)
    print(f"   path : {DB_PATH}")

    if not os.path.exists(DB_PATH):
        print("   ❌ ไม่พบไฟล์ tickets.db ที่ตำแหน่งนี้")
        print("      แปลว่า syslog_server.py เขียนไฟล์ไว้ที่อื่น (รันจากคนละโฟลเดอร์)")
        return None

    size_kb = os.path.getsize(DB_PATH) / 1024
    print(f"   ขนาด : {size_kb:,.1f} KB")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    print(f"   จำนวน TT ทั้งหมด : {total}")

    if total == 0:
        print("   ❌ ไม่มี TT เลยสักใบ — ปัญหาอยู่ที่ syslog ไม่ถึงเครื่อง ไม่ใช่ Dashboard")
        conn.close()
        return None

    return conn


def check_groups(conn):
    print()
    print(line)
    print(" 2) TT แยกตาม Owner Group")
    print(line)

    rows = conn.execute("""
        SELECT COALESCE(owner_group, '(ไม่มีกลุ่ม)') AS grp, COUNT(*) AS n
        FROM tickets GROUP BY owner_group ORDER BY n DESC
    """).fetchall()

    for r in rows:
        grp, n = r['grp'], r['n']
        if grp == DEFAULT_TAB:
            note = '  <-- แท็บที่ Dashboard เปิดมาเป็นค่าเริ่มต้น'
        elif grp == '(ไม่มีกลุ่ม)':
            note = '  <-- ไม่อยู่ในกลุ่มไหนเลย ไปอยู่หน้า Triage'
        elif grp not in DASHBOARD_GROUPS:
            note = '  <-- ⚠️ ชื่อกลุ่มนี้ Dashboard ไม่รู้จัก จะตกไปหน้า Triage'
        else:
            note = '  <-- ต้องคลิกเปลี่ยนแท็บถึงจะเห็น'
        print(f"   {grp:<20} {n:>4} ใบ{note}")

    in_default = next((r['n'] for r in rows if r['grp'] == DEFAULT_TAB), 0)
    if in_default == 0:
        print()
        print("   💡 ไม่มี TT อยู่ในแท็บเริ่มต้นเลย — เปิด Dashboard มาจะเห็นหน้าว่าง")
        print("      ให้คลิกเปลี่ยนกลุ่มทางซ้ายเป็นกลุ่มที่มีเลขข้างบน")


def check_latest(conn):
    print()
    print(line)
    print(" 3) TT 8 ใบล่าสุด")
    print(line)

    rows = conn.execute("""
        SELECT incident_number, node, interface, status,
               owner_group, priority, confidence, event_start_time
        FROM tickets ORDER BY incident_number DESC LIMIT 8
    """).fetchall()

    for r in rows:
        grp = r['owner_group'] or '(ไม่มีกลุ่ม)'
        pri = r['priority'] or '-'
        conf = r['confidence'] if r['confidence'] is not None else '-'
        print(f"   {r['incident_number']}  {r['status']:<10} {grp:<16} {pri:<3} conf={conf}")
        print(f"      {r['node']} {r['interface'] or ''}  |  {r['event_start_time']}")

    missing = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE owner_group IS NULL OR owner_group = ''"
    ).fetchone()[0]
    if missing:
        print()
        print(f"   ⚠️ มี TT ที่ยังไม่มี Owner Group อยู่ {missing} ใบ")
        print("      รัน  py backfill_classify.py  เพื่อจำแนกย้อนหลัง")


def check_api():
    print()
    print(line)
    print(" 4) Dashboard API")
    print(line)

    try:
        # ยิงโดยไม่มี token — ถ้า API เปิดอยู่จะตอบ 401 ซึ่งแปลว่ามีชีวิต
        urllib.request.urlopen(API_BASE + '/api/tickets', timeout=3)
        print(f"   ✅ API ตอบที่ {API_BASE} (แต่ไม่ควรผ่านโดยไม่มี token — ตรวจ auth ด้วย)")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"   ✅ API เปิดอยู่ที่ {API_BASE} (ตอบ 401 = ต้อง login ก่อน ถูกต้องแล้ว)")
        else:
            print(f"   ⚠️ API ตอบ HTTP {e.code} — ไม่ใช่ค่าที่คาดไว้")
    except Exception as e:
        print(f"   ❌ ติดต่อ API ไม่ได้: {e}")
        print("      → ดับเบิลคลิก 5_start_dashboard_api.bat แล้วปล่อยหน้าต่างค้างไว้")
        return

    print()
    print("   💡 ถ้าเพิ่ง restart API ต้อง logout แล้ว login ใหม่")
    print("      (token เก็บในหน่วยความจำ ไม่ persist — token เดิมใช้ไม่ได้แล้ว)")


def main():
    print()
    print(" ตรวจสอบว่าทำไม TT ไม่ขึ้นบน Dashboard")
    print()

    conn = check_db()
    if conn is None:
        check_api()
        return

    check_groups(conn)
    check_latest(conn)
    conn.close()
    check_api()

    print()
    print(line)
    print(" สรุป: เทียบเลขในข้อ 2 กับแท็บกลุ่มที่เปิดดูอยู่บนหน้าเว็บ")
    print(line)
    print()


if __name__ == '__main__':
    sys.exit(main())
