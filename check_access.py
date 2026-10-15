"""
ตรวจว่า Access router ทั้ง 3 ตัวยังติดต่อได้ไหม และพอร์ตอยู่ในสภาพใด

ใช้เมื่อ check_disposition บอกว่า ping ไป 10.0.x.2 ไม่ผ่านตั้งแต่ baseline
เพื่อแยกว่าเป็นเพราะอะไรใน 3 อย่าง:
    - node ไม่ได้ boot อยู่ใน CML
    - E0/0 ถูกปิดค้างไว้จากการทดสอบรอบก่อน
    - มีประกัน reload ค้างอยู่

วิธีรัน:
    py check_access.py            ตรวจอย่างเดียว
    py check_access.py fix        ตรวจแล้วเปิดพอร์ตคืน + ยกเลิกประกัน reload ให้ด้วย
"""
import sys

from netmiko import ConnectHandler

import fault_injection as fi
from lib import fault_profiles as fp


def probe(site_key, fix=False):
    cfg = fp.SITES[site_key]
    print()
    print(f"  {cfg['node']}  (SSH port {cfg['ssh_port']})")
    print("  " + "-" * 62)

    device = dict(fi.CORE)
    device['port'] = cfg['ssh_port']
    device['conn_timeout'] = 10
    try:
        conn = ConnectHandler(**device)
        conn.enable()
    except Exception as exc:
        print(f"    🔴 SSH เข้าไม่ได้ — {type(exc).__name__}")
        print(f"       {str(exc).splitlines()[0][:70]}")
        print("       ตรวจใน CML ว่า node นี้ state เป็น Booted อยู่หรือเปล่า")
        print(f"       ถ้า boot อยู่ ให้เปิด Console ของ {cfg['node']} แล้วสั่ง:")
        print(f"         enable / configure terminal / interface {cfg['access_if']}"
              f" / no shutdown / end")
        return False

    try:
        brief = conn.send_command('show ip interface brief')
        reload_info = conn.send_command('show reload')

        print("    🟢 SSH เข้าได้")
        for line in brief.splitlines():
            if line.split() and line.split()[0] in (cfg['access_if'],
                                                    'Loopback0', 'Loopback1'):
                mark = '🔴' if 'down' in line.lower() else '🟢'
                print(f"       {mark} {line.strip()}")
        if 'Loopback1' not in brief:
            print("       ⚠️  ไม่มี Loopback1 — ยังไม่ได้รัน setup_customer_lan.py")

        pending = 'reload' in reload_info.lower() and 'no reload' not in reload_info.lower()
        if pending:
            print(f"       🛡️  มีประกัน reload ค้างอยู่: {reload_info.strip()[:60]}")

        acl = conn.send_command(f'show running-config interface {cfg["access_if"]}')
        if fp.PHYSICAL_ACL in acl:
            print(f"       🔴 มี ACL จำลอง {fp.PHYSICAL_ACL} ค้างอยู่บน "
                  f"{cfg['access_if']} (ICMP ถูกทิ้ง)")

        if fix:
            lines = [f'interface {cfg["access_if"]}',
                     f'no ip access-group {fp.PHYSICAL_ACL} in',
                     'no shutdown']
            if 'Loopback1' in brief:
                lines += ['interface Loopback1', 'no shutdown']
            conn.send_config_set(lines)
            conn.send_config_set([f'no ip access-list extended {fp.PHYSICAL_ACL}'])
            if pending:
                out = conn.send_command_timing('reload cancel')
                if 'onfirm' in out:
                    conn.send_command_timing('\n')
            print("       ✅ ถอน ACL เปิดพอร์ตคืน และยกเลิกประกัน reload แล้ว")
        return True
    finally:
        conn.disconnect()


def main():
    fix = len(sys.argv) > 1 and sys.argv[1].lower() == 'fix'

    print()
    print("=" * 66)
    print("  ตรวจสภาพ Access router ทั้ง 3 ตัว" + ("  (โหมดซ่อม)" if fix else ""))
    print("=" * 66)

    reachable = [k for k in fp.SITES if probe(k, fix)]

    print()
    print("=" * 66)
    print(f"  ติดต่อได้ {len(reachable)} จาก {len(fp.SITES)} ตัว")
    if len(reachable) < len(fp.SITES):
        print("  ตัวที่เข้าไม่ได้ต้องแก้ผ่าน Console ใน CML ก่อน")
    elif not fix:
        print("  ถ้ามีพอร์ตไหนเป็น 🔴 ให้รัน  py check_access.py fix  เพื่อเปิดคืน")
    print()

    if reachable:
        print("  ตรวจจาก Core อีกที:")
        conn = fi.connect()
        try:
            for k, cfg in fp.SITES.items():
                peer = fp.ping_from_core(conn, cfg['peer_ip'])
                lan = fp.ping_from_core(conn, cfg['lan_ip'])
                print(f"    {cfg['node']:<16} "
                      f"ping {cfg['peer_ip']:<12} {'🟢' if peer else '🔴'}   "
                      f"ping {cfg['lan_ip']:<14} {'🟢' if lan else '🔴'}")
        finally:
            conn.disconnect()
    print()


if __name__ == '__main__':
    main()
