"""
ชุดทดสอบอัตโนมัติ — ยืนยันว่า fault ทั้ง 4 ประเภทสร้างหลักฐานที่แยกออกจากกันได้จริง

ทำอะไร:
    วนทีละประเภท ทำตามลำดับนี้เสมอ
        ล้าง log -> ซ่อมให้สะอาด -> ตรวจ baseline -> inject -> รอ -> เก็บหลักฐาน
        -> เทียบกับที่คาดไว้ -> ซ่อมคืน
    จบแล้วสรุปเป็นตาราง ผ่าน/ไม่ผ่าน และบันทึกผลลง disposition_test_results.json
    เพื่อเอาไปใช้เป็นหลักฐานประกอบเล่มได้เลย

ทำไมต้อง 'clear logging' ก่อนทุกรอบ:
    show logging เก็บสะสมไปเรื่อยๆ ถ้าไม่ล้าง ร่องรอยจากการทดสอบรอบก่อนจะค้างอยู่
    แล้วทำให้รอบถัดไปอ่านผิดว่าเป็น No-fault ทั้งที่ยังไม่ได้ทำอะไร
    (ในระบบจริงล้าง log แบบนี้ไม่ได้ ต้องเทียบ timestamp กับเวลาที่ TT เปิดแทน —
     เป็นสิ่งที่ lib/disposition.py ต้องทำในขั้นถัดไป)

วิธีรัน:
    py test_dispositions.py              ทดสอบ Site A ครบ 4 ประเภท
    py test_dispositions.py B            ทดสอบ Site B
    py test_dispositions.py A config     ทดสอบเฉพาะประเภทเดียว
                                         (config / physical / customer / nofault)
"""
import json
import sys
import time
from datetime import datetime

import check_disposition as cd
import fault_injection as fi
from lib import fault_profiles as fp

# ลำดับการทดสอบ — เริ่มจากอันที่ปลอดภัยที่สุดไปหาอันที่เสี่ยงที่สุด
ORDER = [
    ('configuration', 'Configuration'),
    ('customer', 'Customer-side'),
    ('nofault', 'No-fault'),
    ('physical', 'Physical'),          # ไว้ท้ายสุด เพราะตัดเส้นทาง SSH ของตัวเอง
]

ALIASES = {
    'config': 'configuration', 'configuration': 'configuration',
    'physical': 'physical', 'phy': 'physical',
    'customer': 'customer', 'cust': 'customer',
    'nofault': 'nofault', 'no-fault': 'nofault',
}

SETTLE_SECONDS = 5          # รอให้สถานะนิ่งและ syslog ยิงออกมาครบ


def clear_log():
    conn = fi.connect()
    try:
        out = conn.send_command_timing('clear logging')
        if 'onfirm' in out:
            conn.send_command_timing('\n')
    finally:
        conn.disconnect()


def look(site_key):
    """เก็บหลักฐาน + ตัดสิน — ใช้ตรรกะชุดเดียวกับ check_disposition.py"""
    conn = fi.connect()
    try:
        ev = cd.gather(conn, site_key)
    finally:
        conn.disconnect()
    verdict, route, because, ruled_out = cd.decide(ev)
    return ev, verdict, route, because


def line(ev):
    """ย่อหลักฐานให้เหลือบรรทัดเดียว สำหรับใส่ในตารางสรุป"""
    if ev['admin_down']:
        return 'E1 admin down'
    return (f"E1 up · E2 {'ผ่าน' if ev['peer_ok'] else 'ไม่ผ่าน'}"
            f" · E3 {'ผ่าน' if ev['lan_ok'] else 'ไม่ผ่าน'}"
            f" · E4 {len(ev['log_lines'])} บรรทัด")


def run_one(site_key, disposition, title):
    print()
    print("=" * 72)
    print(f"  ทดสอบ: {title}   ({fp.SITES[site_key]['node']})")
    print("=" * 72)

    # --- เตรียมสภาพให้สะอาดก่อน -------------------------------------------
    print("  [1/5] ซ่อมทุกจุดและล้าง log ให้สะอาดก่อน")
    fp.repair(site_key)
    clear_log()
    time.sleep(2)

    ev0, v0, _, _ = look(site_key)
    print(f"  [2/5] baseline: {line(ev0)}  ->  {v0}")
    if v0 != 'ระบุไม่ได้':
        print()
        print(f"  ❌ หยุดก่อน — baseline ควรเป็น 'ระบุไม่ได้' แต่ได้ '{v0}'")
        print("     แปลว่ายังมีอะไรผิดปกติค้างอยู่ ทดสอบต่อไปผลจะเชื่อถือไม่ได้")
        if not ev0['lan_ok'] and not ev0['admin_down']:
            print("     ping LAN ลูกค้าไม่ผ่าน — น่าจะยังไม่ได้รัน setup_customer_lan.py")
        return {'disposition': disposition, 'result': 'baseline ไม่สะอาด',
                'baseline': v0, 'passed': False}

    # --- ลงมือ -------------------------------------------------------------
    print(f"  [3/5] inject ประเภท {disposition}")
    t0 = datetime.now()
    try:
        info = fp.inject(site_key, disposition)
    except Exception as exc:
        print(f"  ❌ inject ไม่สำเร็จ: {exc}")
        return {'disposition': disposition, 'result': f'inject error: {exc}',
                'passed': False}
    if info.get('auto_recover_at'):
        print(f"        🛡️  ประกัน reload ทำงาน — กู้เองตอน {info['auto_recover_at'][11:]}")

    time.sleep(SETTLE_SECONDS)

    # --- ตรวจ --------------------------------------------------------------
    ev, verdict, route, because = look(site_key)
    expected = title
    passed = (verdict == expected)
    print(f"  [4/5] หลักฐาน: {line(ev)}")
    print(f"        คาดไว้  : {expected}")
    print(f"        ตรวจได้ : {verdict}   {'✅ ตรงกัน' if passed else '❌ ไม่ตรง'}")
    if passed:
        for b in because:
            print(f"          • {b}")

    # --- เก็บกวาด ----------------------------------------------------------
    print("  [5/5] ซ่อมคืน")
    fp.repair(site_key)

    return {
        'disposition': disposition,
        'expected': expected,
        'detected': verdict,
        'route': route,
        'evidence': line(ev),
        'admin_down': ev['admin_down'],
        'peer_ok': ev['peer_ok'],
        'lan_ok': ev['lan_ok'],
        'log_lines': len(ev['log_lines']),
        'injected_at': t0.isoformat(timespec='seconds'),
        'passed': passed,
    }


def main():
    site_key = (sys.argv[1].upper() if len(sys.argv) > 1 else 'A')
    if site_key not in fp.SITES:
        print(f"❌ ไม่รู้จัก site '{site_key}' — เลือกได้: {', '.join(fp.SITES)}")
        sys.exit(1)

    if len(sys.argv) > 2:
        pick = ALIASES.get(sys.argv[2].lower())
        if pick is None:
            print(f"❌ ไม่รู้จักประเภท '{sys.argv[2]}' — "
                  f"เลือกได้: config, physical, customer, nofault")
            sys.exit(1)
        plan = [(d, t) for d, t in ORDER if d == pick]
    else:
        plan = ORDER

    print()
    print("ชุดทดสอบ fault 4 ประเภท")
    print(f"  Site        : {fp.SITES[site_key]['node']}")
    print(f"  จะทดสอบ     : {', '.join(t for _, t in plan)}")
    print()
    print("  ⚠️  ประเภท Physical จะปิดพอร์ตฝั่ง Access ซึ่งตัดเส้นทาง SSH ของตัวเอง")
    print("     มีประกัน reload รออยู่ แต่ถ้าซ่อมอัตโนมัติไม่สำเร็จ")
    print("     ต้องรอ router reboot เองประมาณ 15 นาที")
    if input("\n  จะเริ่มไหม? (Enter = เริ่ม, n = ยกเลิก): ").strip().lower() == 'n':
        print("  ยกเลิกแล้ว")
        return

    results = []
    for disposition, title in plan:
        results.append(run_one(site_key, disposition, title))

    # --- สรุป -----------------------------------------------------------------
    print()
    print("=" * 72)
    print("  สรุปผล")
    print("=" * 72)
    print("  ประเภทที่ทำ        ตรวจได้            ผล")
    print("  " + "-" * 68)
    for r in results:
        print(f"  {r.get('expected', r['disposition']):<18}"
              f"{r.get('detected', r.get('result', '-')):<18}"
              f"{'✅ ผ่าน' if r['passed'] else '❌ ไม่ผ่าน'}")
        print(f"      หลักฐาน: {r.get('evidence', '-')}")
    print("  " + "-" * 68)

    passed = sum(1 for r in results if r['passed'])
    print(f"  ผ่าน {passed} จาก {len(results)}")
    if passed == len(results):
        print("  🎉 fault ทั้งหมดสร้างหลักฐานที่แยกออกจากกันได้จริง")
        print("     พร้อมย้ายตรรกะเข้า pipeline เป็น lib/disposition.py แล้ว")
    else:
        print("  ดูรายละเอียดของอันที่ไม่ผ่านด้านบน แล้วแก้ก่อนไปขั้นถัดไป")

    out = {
        'site': fp.SITES[site_key]['node'],
        'tested_at': datetime.now().isoformat(timespec='seconds'),
        'results': results,
    }
    with open('disposition_test_results.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print()
    print("  บันทึกผลไว้ที่ disposition_test_results.json (ใช้อ้างอิงในเล่มได้)")


if __name__ == '__main__':
    main()
