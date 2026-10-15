"""
ตัวช่วยตรวจสอบด้วยมือ — ดูว่าอาการที่เห็นตรงกับเงื่อนไขไหนใน 4 แบบ

⚠️ นี่ยังไม่ใช่ "ตัวคัดแยก" ที่จะฝังในระบบ
   มันคือเครื่องมือให้คนดูหลักฐานดิบทั้งหมดพร้อมกันในหน้าเดียว แล้วตัดสินเอง
   ใช้ระหว่างที่ยังพัฒนา เพื่อยืนยันว่า fault ที่ inject ไปสร้างหลักฐานได้ถูกต้องจริง

หลักฐานที่ใช้ทั้งหมดดูจาก Core ที่เดียว ไม่ต้องขอสิทธิ์เพิ่ม:
    E1  สถานะ admin ของพอร์ต Core ที่ชี้ไป Site นั้น
    E2  ping IP ฝั่ง Access ของลิงก์ /30
    E3  ping LAN ลูกค้า (Loopback1)
    E4  ร่องรอยใน show logging

ตารางตัดสิน:
    E1 admin down                       -> Configuration   ส่งต่อ L2 (มี PW)
    E1 up · E2 ไม่ผ่าน                  -> Physical        ส่งช่างหน้างาน
    E1 up · E2 ผ่าน · E3 ไม่ผ่าน        -> Customer-side   แจ้งลูกค้า
    E1 up · E2 ผ่าน · E3 ผ่าน · มี log  -> No-fault        ปิดงานพร้อมหลักฐาน
    นอกเหนือจากนี้                       -> ระบุไม่ได้      ให้คนตัดสิน

วิธีรัน:
    py check_disposition.py            -> ตรวจทั้ง 3 Site
    py check_disposition.py A          -> ตรวจเฉพาะ Site A
"""
import sys

import fault_injection as fi
from lib import fault_profiles as fp


def gather(conn, site_key):
    """เก็บหลักฐานดิบของ site หนึ่ง — ไม่ตีความอะไรทั้งนั้นในขั้นนี้"""
    cfg = fp.SITES[site_key]

    brief = conn.send_command(f"show ip interface brief | include {cfg['core_if']}")
    admin_down = 'administratively down' in brief

    if admin_down:
        # พอร์ตปิดอยู่ ping ยังไงก็ไม่ผ่าน ไม่ต้องเสียเวลา
        peer_ok = lan_ok = False
        peer_raw = lan_raw = '(ข้าม — พอร์ตถูกปิดอยู่)'
    else:
        peer_raw = conn.send_command(f"ping {cfg['peer_ip']} repeat 5", read_timeout=40)
        lan_raw = conn.send_command(f"ping {cfg['lan_ip']} repeat 5", read_timeout=40)
        peer_ok = 'Success rate is 0' not in peer_raw and '!' in peer_raw
        lan_ok = 'Success rate is 0' not in lan_raw and '!' in lan_raw

    logs = conn.send_command(f"show logging | include {cfg['core_if']}")
    log_lines = [l.strip() for l in logs.splitlines() if 'LINK' in l or 'LINEPROTO' in l]

    return {
        'cfg': cfg,
        'brief': brief.strip(),
        'admin_down': admin_down,
        'peer_ok': peer_ok,
        'lan_ok': lan_ok,
        'peer_raw': peer_raw,
        'lan_raw': lan_raw,
        'log_lines': log_lines[-6:],
    }


def decide(ev):
    """
    เทียบหลักฐานกับตาราง คืน (ข้อสรุป, ปลายทาง, เหตุผล, รายการที่ตัดออก)
    ถ้าไม่ตรงแถวไหนเลย ต้องตอบว่าระบุไม่ได้ ห้ามเดาเอาอันที่ใกล้เคียงที่สุด
    """
    cfg = ev['cfg']

    if ev['admin_down']:
        return ('Configuration', 'ส่งต่อ L2 — ต้องสร้าง PW',
                [f"พอร์ต {cfg['core_if']} รายงานตัวเองว่า administratively down",
                 "สถานะนี้เกิดจากคำสั่ง shutdown เท่านั้น สายขาดทำให้เกิดไม่ได้"],
                ["Physical — ถ้าสายขาด พอร์ตจะเป็น up/down ไม่ใช่ administratively down",
                 "Customer-side — ปัญหาอยู่ที่พอร์ตของเราเอง ยังไม่ถึง demarcation",
                 "No-fault — ยังผิดปกติอยู่ ณ เวลาที่ตรวจสอบ"])

    if not ev['peer_ok']:
        return ('Physical', 'ส่งช่างหน้างาน',
                [f"พอร์ต {cfg['core_if']} ฝั่งเราสถานะปกติ (ไม่ได้ถูกสั่งปิด)",
                 f"แต่ ping {cfg['peer_ip']} ซึ่งเป็นปลายอีกด้านของสายเส้นเดียวกันไม่ผ่าน",
                 "แปลว่าขาดที่ตัวสายหรืออุปกรณ์ปลายทาง ไม่ใช่ที่ config ของเรา"],
                [f"Configuration — {cfg['core_if']} ไม่ได้อยู่ในสถานะ administratively down",
                 "Customer-side — ลิงก์ของเราเองยังคุยไม่ได้ ปัญหาอยู่ก่อนถึง demarcation",
                 "No-fault — ยังผิดปกติอยู่ ณ เวลาที่ตรวจสอบ"])

    if not ev['lan_ok']:
        return ('Customer-side', 'แจ้งลูกค้า',
                [f"พอร์ตเราปกติ และ ping {cfg['peer_ip']} ผ่าน = ลิงก์ของเราสมบูรณ์",
                 f"แต่ ping {cfg['lan_ip']} ซึ่งเป็น LAN ฝั่งลูกค้าไม่ผ่าน",
                 "จุดที่เสียอยู่เลย demarcation point ออกไป นอกความรับผิดชอบของเรา"],
                [f"Configuration — {cfg['core_if']} สถานะปกติ",
                 "Physical — ลิงก์ /30 ของเรายัง ping ถึงกันได้ตามปกติ",
                 "No-fault — ยังมีจุดที่ใช้งานไม่ได้อยู่"])

    if ev['log_lines']:
        return ('No-fault', 'ปิดงานพร้อมหลักฐาน',
                ["ทุกจุดที่ตรวจสอบกลับมาปกติหมดแล้ว ณ เวลาที่ตรวจ",
                 f"แต่ show logging ยังมีร่องรอยว่าเคยผิดปกติ ({len(ev['log_lines'])} บรรทัด)",
                 "เข้าข่ายเหตุที่หายเอง ต้องแนบ log เป็นหลักฐานก่อนปิดงาน"],
                ["Configuration / Physical / Customer-side — "
                 "ไม่มีจุดใดผิดปกติอยู่ ณ เวลาที่ตรวจสอบ"])

    return ('ระบุไม่ได้', 'ให้ agent ตัดสิน',
            ["ทุกจุดปกติ และไม่พบร่องรอยความผิดปกติใน log",
             "หลักฐานไม่พอจะสรุปเป็นแบบใดแบบหนึ่ง"],
            ["ไม่ตัดอะไรออก — ต้องให้คนดูเพิ่ม"])


def report(ev):
    cfg = ev['cfg']
    verdict, route, because, ruled_out = decide(ev)

    print()
    print("=" * 72)
    print(f"  {cfg['node']}   (Core {cfg['core_if']})")
    print("=" * 72)
    print()
    print("  หลักฐานที่เก็บได้")
    print("  " + "-" * 68)
    print(f"  E1  สถานะพอร์ต Core     : "
          f"{'🔴 administratively down' if ev['admin_down'] else '🟢 ปกติ (ไม่ได้ถูกสั่งปิด)'}")
    print(f"      {ev['brief'] or '(ไม่มีข้อมูล)'}")
    if not ev['admin_down']:
        print(f"  E2  ping {cfg['peer_ip']:<15}: "
              f"{'🟢 ผ่าน' if ev['peer_ok'] else '🔴 ไม่ผ่าน'}   (ปลายอีกด้านของสาย)")
        print(f"  E3  ping {cfg['lan_ip']:<15}: "
              f"{'🟢 ผ่าน' if ev['lan_ok'] else '🔴 ไม่ผ่าน'}   (LAN ฝั่งลูกค้า)")
    print(f"  E4  ร่องรอยใน log       : {len(ev['log_lines'])} บรรทัด")
    for line in ev['log_lines']:
        print(f"      {line}")
    print()
    print("  " + "-" * 68)
    print(f"  สรุป: {verdict}   →  {route}")
    print("  " + "-" * 68)
    print("  เพราะ")
    for b in because:
        print(f"    • {b}")
    print("  ตัดออกเพราะ")
    for r in ruled_out:
        print(f"    • {r}")
    print()
    print("  ⚠️  คนต้องเป็นผู้ยืนยันเสมอ — นี่เป็นเพียงการรวบรวมหลักฐานให้ดูง่ายขึ้น")


def main():
    keys = [sys.argv[1].upper()] if len(sys.argv) > 1 else list(fp.SITES)
    for k in keys:
        if k not in fp.SITES:
            print(f"❌ ไม่รู้จัก site '{k}' — เลือกได้: {', '.join(fp.SITES)}")
            sys.exit(1)

    conn = fi.connect()
    try:
        for k in keys:
            report(gather(conn, k))
    finally:
        conn.disconnect()


if __name__ == '__main__':
    main()
