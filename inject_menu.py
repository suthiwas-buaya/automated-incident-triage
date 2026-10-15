"""
เมนูจำลอง fault — เลือก 2 ชั้น: เลือกเคส แล้วเลือกว่าจะให้เป็น fault ประเภทไหน

ชั้นที่ 1  เลือกว่าจะทำที่ไหน (Site A / B / C / Loopback ของ Core / ทุก Site)
ชั้นที่ 2  เลือกว่าจะให้เป็นปัญหาแบบไหนใน 4 แบบ
              1. Configuration   -> ต้องแก้ config  ส่งต่อ L2 (มี PW)
              2. Physical        -> สายขาดระหว่างทาง  ส่งช่างหน้างาน
              3. Customer-side   -> ปัญหาอยู่เลย demarcation  แจ้งลูกค้า
              4. No-fault        -> หายเอง  ปิดงานพร้อมหลักฐาน

ประเภทที่เลือกไม่ได้ถูกส่งเข้าไปในระบบวิเคราะห์ — มันถูกบันทึกลง fault_ledger.jsonl
ไว้เทียบตอนวัดผลเท่านั้น ตัวคัดแยกต้องหาคำตอบเองจากหลักฐานบนอุปกรณ์จริง

ทุกแบบเป็น fault ของจริง IOS สร้างข้อความ syslog เอง ไม่ได้แต่งข้อความ

ข้อ 6 = คิวขาเข้าเต็ม (C06) -> พอร์ตยัง up ไม่มี syslog -> ต้องเปิด TT เอง

ต้องตั้งค่าแล็บก่อนใช้ครั้งแรก:
    py setup_customer_lan.py

วิธีรัน:
    py inject_menu.py
"""
import sys

import c06_congestion as c06
import fault_injection as fi
from lib import fault_profiles as fp
from lib import ticket_draft

# ---------------------------------------------------------------------------
# ชั้นที่ 1 — เคสที่เลือกได้
#   kind = 'site'  -> ใช้ fault_profiles ได้ครบทั้ง 4 ประเภท
#   kind = 'core'  -> เป็นปัญหาที่ตัวอุปกรณ์แกนกลาง เลือกได้แค่ 2 ประเภท
# ---------------------------------------------------------------------------
CASES = {
    '1': {'kind': 'site', 'site': 'A', 'label': 'Site A (Core E0/0 ↔ Access-Site-A)'},
    '2': {'kind': 'site', 'site': 'B', 'label': 'Site B (Core E0/1 ↔ Access-Site-B)'},
    '3': {'kind': 'site', 'site': 'C', 'label': 'Site C (Core E0/2 ↔ Access-Site-C)'},
    '4': {'kind': 'core', 'interfaces': ['Loopback0'],
          'label': 'Loopback0 ของ Core (อุปกรณ์แกนกลางมีปัญหา)'},
    '5': {'kind': 'core', 'interfaces': list(fi.SITE_INTERFACES),
          'label': 'ทุก Site หลุดพร้อมกัน (ปัญหาที่ตัว Core)'},
}

CONGESTION_KEY = '6'

# ชั้นที่ 2 — ประเภทที่เลือกได้ในแต่ละ kind
CHOICES_SITE = [
    ('1', 'configuration', 'Configuration  → ส่งต่อ L2 (สร้าง PW)',
     'ปิดพอร์ตฝั่ง Core — พอร์ตจะรายงานตัวเองว่า administratively down'),
    ('2', 'physical', 'Physical  → ส่งช่างหน้างาน',
     'ปิดพอร์ตฝั่ง Access — Core เห็นพอร์ตตัวเองปกติ แต่คุยกับปลายสายไม่ได้'),
    ('3', 'customer', 'Customer-side  → แจ้งลูกค้า',
     'ปิด LAN ลูกค้าจำลอง — ลิงก์ของเราดีหมด แต่ปลายทางไม่ตอบ'),
    ('4', 'nofault', 'No-fault  → ปิดงานพร้อมหลักฐาน',
     'ปิดแล้วเปิดคืนเองใน 20 วินาที เหลือไว้แต่ร่องรอยใน syslog'),
]

CHOICES_CORE = [
    ('1', 'configuration', 'Configuration  → ส่งต่อ L2 (สร้าง PW)',
     'ปิดค้างไว้จนกว่าจะสั่งซ่อม'),
    ('4', 'nofault', 'No-fault  → ปิดงานพร้อมหลักฐาน',
     'ปิดแล้วเปิดคืนเองใน 20 วินาที'),
]


# ---------------------------------------------------------------------------
# อ่านสภาพจริงทั้งหมดในการเชื่อมต่อครั้งเดียว
# ---------------------------------------------------------------------------
def read_all_status():
    conn = fi.connect()
    try:
        core = fi.read_status(conn)
        queues = c06.status_all(conn)
        sites = {}
        for key, cfg in fp.SITES.items():
            if core.get(cfg['core_if']) == 'down':
                sites[key] = {'core_admin': 'down', 'peer_ok': False, 'lan_ok': False}
                continue
            sites[key] = {
                'core_admin': 'up',
                'peer_ok': fp.ping_from_core(conn, cfg['peer_ip']),
                'lan_ok': fp.ping_from_core(conn, cfg['lan_ip']),
            }
    finally:
        conn.disconnect()
    return {'core': core, 'queues': queues, 'sites': sites}


def is_healthy(status, case):
    if case['kind'] == 'site':
        s = status['sites'][case['site']]
        return s['core_admin'] == 'up' and s['peer_ok'] and s['lan_ok']
    return all(status['core'].get(i) != 'down' for i in case['interfaces'])


# ---------------------------------------------------------------------------
# แสดงผล
# ---------------------------------------------------------------------------
def print_header():
    print()
    print("=" * 72)
    print("  เครื่องมือจำลอง fault บนเครือข่ายทดสอบ")
    print("  เลือกเคส → เลือกประเภทของปัญหา → ระบบไปทำที่อุปกรณ์จริง")
    print("=" * 72)


def print_menu(status):
    print()
    print("  เลือกเคสที่จะจำลอง:")
    for key, case in CASES.items():
        if case['kind'] == 'site':
            mark = fp.describe(status['sites'][case['site']])
        else:
            down = [i for i in case['interfaces'] if status['core'].get(i) == 'down']
            if not down:
                mark = '🟢 ปกติ'
            elif len(down) == len(case['interfaces']):
                mark = '🔴 ล่มอยู่'
            else:
                mark = f'🟠 ล่มบางส่วน ({len(down)}/{len(case["interfaces"])})'
        print(f"   {key}. {case['label']}")
        print(f"      {mark}")

    print(f"   {CONGESTION_KEY}. คิวขาเข้าเต็ม (C06 — ลูกค้าบ่นว่าช้า) เลือก Site ได้ทั้ง 3")
    marks = []
    for key, cfg in c06.SITES.items():
        q = (status['queues'] or {}).get(cfg['interface'])
        icon = '🔴' if q == c06.SMALL_QUEUE else '🟢'
        marks.append(f"{icon} {key.upper()}={q}")
    print("      " + "   ".join(marks) + "   (hold-queue · 1 = อาการค้างอยู่)")
    print("      →  ไม่มี syslog · ระบบไม่สร้าง TT ให้ ต้องเปิดเอง")
    print()
    print("   r. ซ่อมทุกอย่างให้กลับมาปกติ")
    print("   s. รีเฟรชสถานะ")
    print("   q. ออกจากโปรแกรม")
    print("-" * 72)


def ask_disposition(case):
    choices = CHOICES_SITE if case['kind'] == 'site' else CHOICES_CORE
    print()
    print(f"  เคส: {case['label']}")
    print("  จะให้เป็นปัญหาแบบไหน?")
    print()
    for key, _, title, how in choices:
        print(f"   {key}. {title}")
        print(f"      วิธีทำ: {how}")
    if case['kind'] == 'core':
        print()
        print("   (Physical กับ Customer-side ใช้กับเคสนี้ไม่ได้ —")
        print("    Loopback เป็นพอร์ตเสมือนในตัวอุปกรณ์เอง ไม่มีสายและไม่มีฝั่งลูกค้า)")
    print()
    pick = input("   เลือกประเภท (Enter = ยกเลิก): ").strip()
    for key, name, title, _ in choices:
        if pick == key:
            return name, title
    if pick:
        print(f"   ❌ ไม่มีตัวเลือก '{pick}'")
    return None, None


# ---------------------------------------------------------------------------
# ลงมือ
# ---------------------------------------------------------------------------
def do_site(case, disposition, title):
    site = case['site']
    cfg = fp.SITES[site]
    print()
    print("-" * 72)
    try:
        result = fp.inject(site, disposition)
    except Exception as exc:
        print(f"❌ ทำไม่สำเร็จ: {exc}")
        print("-" * 72)
        return
    print(f"🔴 {cfg['node']} — {title}")
    print(f"   {result['note']}")
    if result.get('auto_recover_at'):
        print(f"   🛡️  ประกัน reload: ถ้าเข้าไม่ถึง router จะกู้ตัวเองตอน "
              f"{result['auto_recover_at'][11:]}")
    if result.get('self_healed'):
        print("   สถานะตอนนี้กลับเป็นปกติแล้ว — ให้ไปตรวจสอบว่าระบบสรุปว่า 'หายเอง' ได้ไหม")
    else:
        print("   จะค้างไว้จนกว่าจะสั่งซ่อม")
    print(f"   บันทึกเฉลยไว้ที่ fault_ledger.jsonl แล้ว (expected = {disposition})")
    if result.get('ticket_draft'):
        ticket_draft.show(result['ticket_draft'], result.get('draft_path'))
    print("-" * 72)


def do_core(case, disposition, title):
    print()
    print("-" * 72)
    conn = fi.connect()
    try:
        fi.apply_state(conn, case['interfaces'], 'down')
        if disposition == 'nofault':
            print(f"   ปิดแล้ว — รอ {fp.NOFAULT_HOLD_SECONDS} วินาทีให้ syslog ยิงออกมา")
            import time
            time.sleep(fp.NOFAULT_HOLD_SECONDS)
            fi.apply_state(conn, case['interfaces'], 'up')
    finally:
        conn.disconnect()

    fp.record(action='inject', site='CORE', node='Core',
              core_if=','.join(case['interfaces']),
              expected_disposition=disposition,
              self_healed=(disposition == 'nofault'))

    print(f"🔴 Core — {title}")
    print(f"   พอร์ตที่ทำ: {', '.join(case['interfaces'])}")
    if disposition == 'nofault':
        print("   เปิดคืนให้แล้ว — สถานะตอนนี้ปกติ เหลือแต่ร่องรอยใน show logging")
    else:
        print("   จะค้างไว้จนกว่าจะสั่งซ่อม")
    print(f"   บันทึกเฉลยไว้ที่ fault_ledger.jsonl แล้ว (expected = {disposition})")
    print("-" * 72)


def do_repair(case):
    print()
    print("-" * 72)
    if case['kind'] == 'site':
        for line in fp.repair(case['site']):
            print(f"   {line}")
    else:
        conn = fi.connect()
        try:
            fi.apply_state(conn, case['interfaces'], 'up')
        finally:
            conn.disconnect()
        fp.record(action='repair', site='CORE', node='Core',
                  core_if=','.join(case['interfaces']))
        print(f"   Core: เปิดคืน {', '.join(case['interfaces'])} แล้ว")
    print("🟢 ซ่อมเรียบร้อย — TT ที่ค้างอยู่จะยังไม่ปิดเอง")
    print("   ต้องมีคนกด Acknowledge แล้วทำตามขั้นตอนจนจบก่อน")
    print("-" * 72)


def do_congestion(status):
    print()
    for i, key in enumerate(c06.ORDER, start=1):
        cfg = c06.SITES[key]
        q = (status['queues'] or {}).get(cfg['interface'])
        mark = '🔴 อาการค้างอยู่' if q == c06.SMALL_QUEUE else '🟢 ปกติ'
        print(f"   {i}. Core {cfg['interface']} → {cfg['site']}   {mark}")
    pick = input(f"เลือก Site (1 - {len(c06.ORDER)}): ").strip()
    if not pick.isdigit() or not (1 <= int(pick) <= len(c06.ORDER)):
        print(f"\n❌ ไม่มีตัวเลือก '{pick}'")
        return
    cfg = c06.SITES[c06.ORDER[int(pick) - 1]]

    already = (status['queues'] or {}).get(cfg['interface']) == c06.SMALL_QUEUE
    print()
    print(f"   จะเตรียมอาการคิวขาเข้าเต็มบน Core {cfg['interface']} → {cfg['site']}")
    if already:
        print("   🔴 เส้นนี้มีอาการค้างอยู่แล้ว — ทำใหม่จะล้างตัวนับแล้วยิง traffic รอบใหม่")
    if input("ยืนยันไหม? (Enter = ยืนยัน, n = ยกเลิก): ").strip().lower() == 'n':
        print("   ยกเลิกแล้ว")
        return
    c06.prepare(cfg['interface'])
    fp.record(action='inject', site=cfg['site'], node='Core',
              core_if=cfg['interface'], expected_disposition='configuration',
              variant='c06_congestion')


# ---------------------------------------------------------------------------
def main():
    print_header()

    try:
        status = read_all_status()
    except Exception as e:
        print(f"\n❌ เชื่อมต่อ Core ไม่ได้: {e}")
        print("   ตรวจว่า lab เปิดอยู่ และ IP ใน fault_injection.py ถูกต้อง")
        sys.exit(1)

    while True:
        print_menu(status)
        choice = input(f"เลือก (1-5 / {CONGESTION_KEY} / r / s / q): ").strip().lower()

        if choice in ('q', 'quit', 'exit'):
            print("\nปิดโปรแกรม")
            print("อย่าลืมกด r ซ่อมให้ครบก่อนเลิกใช้แล็บ "
                  "ไม่งั้นการทดสอบรอบหน้าจะเริ่มจากสภาพที่ไม่สะอาด")
            break

        if choice == 's':
            status = read_all_status()
            continue

        if choice == 'r':
            print("\nกำลังซ่อมทุกจุด ...")
            for line in fp.repair_everything():
                print(f"   {line}")
            conn = fi.connect()
            try:
                # คืนค่าคิวด้วย ไม่งั้นอาการ C06 จะค้างไว้เงียบๆ แล้วทำให้การทดสอบ
                # รอบอื่นเพี้ยนหมดโดยไม่มีอะไรฟ้อง
                c06.restore_all(conn=conn, quiet=True)
            finally:
                conn.disconnect()
            print("✅ ซ่อมครบทุกจุดแล้ว")
            status = read_all_status()
            continue

        if choice == CONGESTION_KEY:
            do_congestion(status)
            status = read_all_status()
            continue

        if choice not in CASES:
            print(f"\n❌ ไม่มีตัวเลือก '{choice}'")
            continue

        case = CASES[choice]

        # ถ้าเคสนี้มีอะไรล่มค้างอยู่ -> เสนอซ่อมก่อน ไม่ให้ซ้อน fault
        if not is_healthy(status, case):
            print()
            print(f"   เคสนี้ยังมีปัญหาค้างอยู่: {case['label']}")
            if input("   จะซ่อมให้กลับปกติไหม? (Enter = ซ่อม, n = ยกเลิก): "
                     ).strip().lower() != 'n':
                do_repair(case)
            status = read_all_status()
            continue

        disposition, title = ask_disposition(case)
        if disposition is None:
            continue

        if case['kind'] == 'site':
            do_site(case, disposition, title)
        else:
            do_core(case, disposition, title)

        status = read_all_status()


if __name__ == '__main__':
    main()
