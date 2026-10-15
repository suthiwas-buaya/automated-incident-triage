"""
ตั้งค่า LAN ลูกค้าจำลอง (Customer LAN) — รันครั้งเดียวพอ

ทำไมต้องมี:
    แล็บเดิมมีแค่ Core <-> Access (/30) กับ Loopback0 ของแต่ละ Access
    ไม่มีอะไรที่แทน "ฝั่งลูกค้า" ที่อยู่เลย demarcation point ออกไป
    ทำให้แยกไม่ออกระหว่าง
        Physical      — สายระหว่าง Core กับ Access ขาด
        Customer-side — สายเราดี แต่ปลายทางฝั่งลูกค้าใช้งานไม่ได้

    สคริปต์นี้เพิ่ม Loopback1 บน Access แต่ละตัวเพื่อแทน LAN ฝั่งลูกค้า
    และเพิ่ม static route บน Core ให้มองเห็น LAN นั้น
    จากนั้น Core จะ ping แยกได้ 2 จุด = แยก 2 กรณีนี้ออกจากกันได้

แผน IP:
    Site        Link /30        Access Lo0    Customer LAN (Lo1)
    Site-A      10.0.1.0/30     1.1.1.1       192.168.10.1/24
    Site-B      10.0.2.0/30     3.3.3.3       192.168.20.1/24
    Site-C      10.0.3.0/30     4.4.4.4       192.168.30.1/24

วิธีรัน:
    py setup_customer_lan.py            -> ตั้งค่า + ตรวจสอบ
    py setup_customer_lan.py verify     -> ตรวจสอบอย่างเดียว ไม่แก้อะไร
    py setup_customer_lan.py rollback   -> ถอนการตั้งค่าออกทั้งหมด

ปลอดภัย:
    - ไม่แตะ Ethernet0/3 (ทางเข้า lab) และไม่แตะ Loopback0 เดิม
    - ไม่แตะ interface ที่ใช้ SSH อยู่
    - เขียนทับได้ รันซ้ำได้ ไม่พัง (idempotent)
"""
import sys

from netmiko import ConnectHandler

import fault_injection as fi

# ---------------------------------------------------------------------------
# แผนผัง: Site -> (SSH port, IP ฝั่ง Access ของ /30, Customer LAN)
# ---------------------------------------------------------------------------
SITES = {
    'Access-Site-A': {
        'ssh_port': 2201,
        'peer_ip': '10.0.1.2',          # IP ฝั่ง Access ของลิงก์ /30
        'lan_ip': '192.168.10.1',       # IP ของ Loopback1 = gateway ฝั่งลูกค้า
        'lan_net': '192.168.10.0',
        'lan_mask': '255.255.255.0',
    },
    'Access-Site-B': {
        'ssh_port': 2202,
        'peer_ip': '10.0.2.2',
        'lan_ip': '192.168.20.1',
        'lan_net': '192.168.20.0',
        'lan_mask': '255.255.255.0',
    },
    'Access-Site-C': {
        'ssh_port': 2203,
        'peer_ip': '10.0.3.2',
        'lan_ip': '192.168.30.1',
        'lan_net': '192.168.30.0',
        'lan_mask': '255.255.255.0',
    },
}

CUSTOMER_IF = 'Loopback1'


def connect(port):
    """ต่อเข้า node ผ่าน port forwarding — user/password ชุดเดียวกับ Core"""
    device = dict(fi.CORE)
    device['port'] = port
    device['conn_timeout'] = 15
    conn = ConnectHandler(**device)
    conn.enable()
    return conn


# ---------------------------------------------------------------------------
# ขั้นที่ 1 — เพิ่ม Loopback1 บน Access ทั้ง 3 ตัว
# ---------------------------------------------------------------------------
def setup_access(name, info):
    print(f"  [{name}] กำลังต่อ (port {info['ssh_port']}) ...")
    conn = connect(info['ssh_port'])
    try:
        conn.send_config_set([
            f'interface {CUSTOMER_IF}',
            f' description Simulated Customer LAN - {name}',
            f' ip address {info["lan_ip"]} {info["lan_mask"]}',
            ' no shutdown',
        ])
        conn.save_config()
        out = conn.send_command(f'show ip interface brief | include {CUSTOMER_IF}')
        print(f"  [{name}] ✅ {out.strip() or CUSTOMER_IF + ' สร้างแล้ว'}")
    finally:
        conn.disconnect()


# ---------------------------------------------------------------------------
# ขั้นที่ 2 — เพิ่ม static route บน Core ให้เห็น LAN ลูกค้าทั้ง 3 วง
# ---------------------------------------------------------------------------
def setup_core():
    print("  [Core] กำลังต่อ (port 22) ...")
    conn = fi.connect()
    try:
        lines = []
        for name, info in SITES.items():
            lines.append(
                f'ip route {info["lan_net"]} {info["lan_mask"]} {info["peer_ip"]}'
            )
        conn.send_config_set(lines)
        conn.save_config()
        out = conn.send_command('show ip route static')
        print("  [Core] ✅ static route ที่มีอยู่ตอนนี้:")
        for line in out.splitlines():
            if '192.168.' in line:
                print(f"         {line.strip()}")
    finally:
        conn.disconnect()


# ---------------------------------------------------------------------------
# ขั้นที่ 3 — ตรวจสอบจาก Core ว่า ping ได้ครบทั้ง 2 จุดของทุก Site
# ---------------------------------------------------------------------------
def ping_from_core(conn, target):
    """คืน True ถ้า ping สำเร็จ (IOS ตอบ !!!!! = success rate ไม่เป็น 0)"""
    out = conn.send_command(f'ping {target} repeat 5', read_timeout=30)
    return 'Success rate is 0' not in out and '!' in out


def verify():
    print()
    print("ตรวจสอบจาก Core — ต้องผ่านครบทุกช่องถึงจะพร้อมใช้งาน")
    print("-" * 68)
    print(f"  {'Site':<16}{'ping peer /30':<18}{'ping LAN ลูกค้า':<20}")
    print("-" * 68)

    conn = fi.connect()
    all_ok = True
    try:
        for name, info in SITES.items():
            peer_ok = ping_from_core(conn, info['peer_ip'])
            lan_ok = ping_from_core(conn, info['lan_ip'])
            all_ok = all_ok and peer_ok and lan_ok
            mark = lambda ok: '🟢 ผ่าน' if ok else '🔴 ล้มเหลว'
            print(f"  {name:<16}"
                  f"{info['peer_ip']:<12}{mark(peer_ok):<10}"
                  f"{info['lan_ip']:<16}{mark(lan_ok)}")
    finally:
        conn.disconnect()

    print("-" * 68)
    if all_ok:
        print("✅ พร้อมใช้งาน — ตอนนี้ Core แยกได้แล้วว่าปัญหาอยู่ก่อนหรือหลัง demarcation")
    else:
        print("❌ ยังไม่ครบ — ดูช่องที่เป็น 🔴 แล้วแก้ก่อน")
        print("   ถ้า ping peer ไม่ผ่าน = ลิงก์ /30 มีปัญหา ตรวจ E0/x บน Core ก่อน")
        print("   ถ้า ping peer ผ่านแต่ LAN ไม่ผ่าน = Loopback1 หรือ static route ยังไม่ถูก")
    return all_ok


# ---------------------------------------------------------------------------
# ถอนออก (เผื่ออยากกลับไปสภาพเดิม)
# ---------------------------------------------------------------------------
def rollback():
    print("ถอนการตั้งค่า Customer LAN ออกทั้งหมด")
    print()
    for name, info in SITES.items():
        print(f"  [{name}] ลบ {CUSTOMER_IF} ...")
        conn = connect(info['ssh_port'])
        try:
            conn.send_config_set([f'no interface {CUSTOMER_IF}'])
            conn.save_config()
        finally:
            conn.disconnect()

    print("  [Core] ลบ static route ...")
    conn = fi.connect()
    try:
        conn.send_config_set([
            f'no ip route {i["lan_net"]} {i["lan_mask"]} {i["peer_ip"]}'
            for i in SITES.values()
        ])
        conn.save_config()
    finally:
        conn.disconnect()
    print("✅ ถอนเรียบร้อย กลับสู่สภาพก่อนหน้า")


def main():
    mode = (sys.argv[1].lower() if len(sys.argv) > 1 else 'setup')

    if mode == 'verify':
        verify()
        return
    if mode == 'rollback':
        rollback()
        return
    if mode != 'setup':
        print(f"❌ '{mode}' ไม่ถูกต้อง — ใช้ได้: setup (ค่าตั้งต้น), verify, rollback")
        sys.exit(1)

    print("=" * 68)
    print("ตั้งค่า LAN ลูกค้าจำลอง — เพิ่ม Loopback1 + static route")
    print("=" * 68)
    print()
    print("ขั้นที่ 1: เพิ่ม Loopback1 บน Access ทั้ง 3 ตัว")
    for name, info in SITES.items():
        setup_access(name, info)

    print()
    print("ขั้นที่ 2: เพิ่ม static route บน Core")
    setup_core()

    print()
    print("ขั้นที่ 3: ตรวจสอบ")
    ok = verify()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
