"""
ดู TT ทั้งหมดใน SQLite (อัปเดตให้แสดงผลการจำแนกของ Phase 2 ด้วย)

วิธีรัน:
    py show_tickets.py
"""
from lib import db

PRIORITY_ICON = {'P1': '🔥', 'P2': '🟠', 'P3': '🟡', 'P4': '⚪'}

db.init_db()   # เผื่อ db เก่าจาก Phase 1 ยังไม่มี column ของ Phase 2
tickets = db.list_all_tickets()

if not tickets:
    print("ยังไม่มี TT ในฐานข้อมูล")
else:
    print(f"พบ TT ทั้งหมด {len(tickets)} ใบ\n")
    for t in tickets:
        status_icon = '🟢' if t['status'] == 'Resolved' else '🔴'
        prio = t['priority'] or '-'
        prio_icon = PRIORITY_ICON.get(prio, '  ')

        print(f"{status_icon} {t['incident_number']}  [{t['status']}]  {prio_icon} {prio}")
        print(f"   Node       : {t['node']}  ({t['interface']})")
        print(f"   Summary    : {t['summary']}")

        if t['owner_group']:
            print(f"   Owner Group: {t['owner_group']}  (มั่นใจ {t['confidence']}%)")
            print(f"   เหตุผล      : {t['classify_reason']}")
        else:
            print(f"   Owner Group: - ยังไม่ถูกจำแนก -")

        print(f"   เริ่ม       : {t['event_start_time']}")
        print(f"   สิ้นสุด     : {t['event_end_time'] or '- ยังไม่ปิด -'}")
        print()

    # สรุปภาพรวมแบบที่ Dashboard จะใช้ใน Phase 4
    print("--- สรุปตาม Owner Group ---")
    groups = {}
    for t in tickets:
        key = t['owner_group'] or '(ยังไม่จำแนก)'
        groups[key] = groups.get(key, 0) + 1
    for g, n in sorted(groups.items(), key=lambda x: -x[1]):
        print(f"   {g}: {n} ใบ")

    print("\n--- สรุปตาม Priority ---")
    prios = {}
    for t in tickets:
        key = t['priority'] or '(ยังไม่จำแนก)'
        prios[key] = prios.get(key, 0) + 1
    for p in sorted(prios):
        print(f"   {p}: {prios[p]} ใบ")
