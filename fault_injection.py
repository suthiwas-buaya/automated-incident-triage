"""
Fault Injection ผ่าน Netmiko — แยก "ทำให้ล่ม" กับ "ซ่อม" ออกจากกัน

หลักการ: fault จะค้างอยู่จนกว่าคนจะสั่งซ่อม เหมือนงานจริง
ไม่ใช่ตั้งเวลาให้กลับมาเอง (สายขาดจริงไม่หายเองได้)

ต้องทำที่ Core เพราะ:
  - IOL ไม่ propagate carrier loss ไปฝั่งตรงข้าม
  - Core เป็น node เดียวที่ส่ง syslog ออกมาถึง host ได้เมื่อสายล่ม

วิธีรัน:
    py fault_injection.py status          -> ดูว่าตอนนี้อะไรล่มอยู่บ้าง
    py fault_injection.py E0/1 down       -> ทำให้สายไป Site-B ล่ม (ค้างไว้)
    py fault_injection.py E0/1 up         -> ซ่อมสายไป Site-B
    py fault_injection.py loopback down   -> ทำให้ Loopback ของ Core ล่ม
    py fault_injection.py multi down      -> ทำให้ทุก Site ล่มพร้อมกัน
    py fault_injection.py multi up        -> ซ่อมทุกเส้นพร้อมกัน
"""
import re
import sys

from netmiko import ConnectHandler

from lib.lab_secrets import CREDENTIALS   # รหัสแยกไว้ในไฟล์ที่ไม่ขึ้น GitHub

CORE = {
    'device_type': 'cisco_ios',
    'host': '192.168.150.50',
    'port': 22,
    **CREDENTIALS,
}

SITE_INTERFACES = {
    'Ethernet0/0': 'Access-Site-A',
    'Ethernet0/1': 'Access-Site-B',
    'Ethernet0/2': 'Access-Site-C',
}

UPLINK = 'Ethernet0/3'          # ทางเข้า lab — ห้ามแตะเด็ดขาด
LOOPBACK = 'Loopback0'

# interface ที่สนใจติดตามสถานะ (ไม่รวม uplink)
WATCHED = list(SITE_INTERFACES) + [LOOPBACK]


def normalize(name):
    """รับได้ทั้ง E0/1, Ethernet0/1, loopback, multi"""
    name = name.strip()
    low = name.lower()

    if low in ('loopback', 'loopback0', 'lo0', 'lo'):
        return LOOPBACK
    if low in ('multi', 'all', 'core'):
        return 'MULTI'
    if low.startswith('e') and not low.startswith('ethernet'):
        return 'Ethernet' + name[1:]
    return name


def connect():
    conn = ConnectHandler(**CORE)
    conn.enable()
    return conn


def read_status(conn=None):
    """
    อ่านสถานะ interface จริงจาก Core ด้วย show ip interface brief
    คืน dict: {'Ethernet0/0': 'up' | 'down', ...}
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        output = conn.send_command('show ip interface brief')
    finally:
        if own:
            conn.disconnect()

    status = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        name = parts[0]
        if name not in WATCHED:
            continue
        # คอลัมน์ Status อาจเป็น "up" หรือ "administratively down"
        status[name] = 'down' if re.search(r'administratively\s+down|^down', ' '.join(parts[4:])) else 'up'
    return status


def apply_state(conn, interfaces, state):
    """state = 'down' (shutdown) หรือ 'up' (no shutdown)"""
    command = 'shutdown' if state == 'down' else 'no shutdown'
    lines = []
    for itf in interfaces:
        lines.extend([f'interface {itf}', command])
    conn.send_config_set(lines)


def show_status():
    print("สถานะ interface บน Core ตอนนี้")
    print("-" * 52)
    status = read_status()
    for itf in WATCHED:
        state = status.get(itf, '?')
        icon = '🔴 ล่มอยู่' if state == 'down' else '🟢 ปกติ'
        target = SITE_INTERFACES.get(itf, 'Core เอง')
        print(f"  {itf:<14} {icon:<12} ({target})")
    print("-" * 52)
    return status


def run(interfaces, state, label):
    action = 'ทำให้ล่ม' if state == 'down' else 'ซ่อม'
    print(f"{action}: {label}")
    print()

    conn = connect()
    try:
        apply_state(conn, interfaces, state)
    finally:
        conn.disconnect()

    if state == 'down':
        print("✅ สั่งเรียบร้อย — สายจะล่มค้างไว้จนกว่าจะสั่งซ่อม")
        print("   ไปดู TT ที่เพิ่งสร้างบนหน้า Dashboard ได้เลย")
    else:
        print("✅ ซ่อมเรียบร้อย — สัญญาณกลับมาแล้ว")
        print("   TT จะยังไม่ปิดเอง ต้องมีคนทำงานตามขั้นตอนจนจบก่อน")


def main():
    args = sys.argv[1:]

    if not args or args[0].lower() in ('status', 'st', '-s'):
        show_status()
        return

    target = normalize(args[0])
    state = args[1].lower() if len(args) > 1 else 'down'

    if state not in ('down', 'up'):
        print(f"❌ '{state}' ไม่ถูกต้อง — ใช้ได้แค่ down หรือ up")
        sys.exit(1)

    if target == UPLINK:
        print(f"🚫 ห้ามแตะ {UPLINK} เด็ดขาด — เป็นทางเข้า lab ทั้งหมด (SSH + syslog)")
        print("   ถ้าปิดจะตัดขาตัวเอง ต้องไปแก้ผ่าน Console ใน CML Web UI เท่านั้น")
        sys.exit(1)

    if target == 'MULTI':
        run(list(SITE_INTERFACES), state,
            "ทุกเส้นที่ต่อ Site พร้อมกัน (จำลองปัญหาที่ตัวอุปกรณ์แกนกลาง)")
    elif target == LOOPBACK:
        run([LOOPBACK], state, f"Core {LOOPBACK} (จำลองอุปกรณ์แกนกลางมีปัญหา)")
    elif target in SITE_INTERFACES:
        run([target], state, f"Core {target} → {SITE_INTERFACES[target]}")
    else:
        print(f"❌ ใช้ {target} ไม่ได้")
        print(f"   เลือกได้: {', '.join(SITE_INTERFACES)}, {LOOPBACK}, multi")
        sys.exit(1)


if __name__ == '__main__':
    main()
