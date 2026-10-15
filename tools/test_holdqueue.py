r"""
Phase 5 งาน D — เช็คว่า IOL ยอมเก็บคำสั่ง hold-queue จริงไหม (ตัดสินว่า C06 ทำได้หรือไม่)

ทำไมต้องเช็คแบบนี้: เจอมาแล้วกับ duplex ที่ IOS "รับคำสั่งโดยไม่ฟ้อง error แต่ไม่เก็บลง
running-config" ซึ่งอันตรายกว่าปฏิเสธตรงๆ เพราะดูเหมือนสำเร็จ — ตัวชี้ขาดจึงไม่ใช่ว่า
สั่งแล้วไม่ error แต่คือ **คำสั่งไปโผล่ใน running-config หรือเปล่า**

สคริปต์นี้:
  1. อ่านค่าคิวปัจจุบัน
  2. สั่ง hold-queue 1 in  (ย่อห้องรอจาก 75 เหลือ 1)
  3. เช็คว่า running-config เก็บคำสั่งไว้ไหม + show interfaces รายงานค่าใหม่ไหม
  4. คืนค่าเป็น hold-queue 75 in เสมอก่อนจบ แม้เกิดข้อผิดพลาดระหว่างทาง

🚫 ไม่แตะ Ethernet0/3 (ทางเข้า lab)

วิธีรัน (ต้องเปิด lab):
    py test_holdqueue.py
"""
import re

from netmiko import ConnectHandler

# --- ย้ายมาอยู่ใต้โฟลเดอร์ย่อย: บอก Python ให้มองหา lib/ ที่โฟลเดอร์แม่ ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# ---------------------------------------------------------------------

from lib import mapping
from lib.diagnostics import CREDENTIALS, SSH_TIMEOUT

DEVICE = 'Core'
INTERFACE = 'Ethernet0/1'
DEFAULT_QUEUE = 75

QUEUE_RE = re.compile(r'Input queue:\s*(\d+)/(\d+)/(\d+)/(\d+)')


def read_queue(conn):
    """คืน (size, max, drops, flushes) จากบรรทัด Input queue ของ show interfaces"""
    out = conn.send_command(f'show interfaces {INTERFACE}')
    m = QUEUE_RE.search(out)
    return tuple(int(g) for g in m.groups()) if m else None


def main():
    assert INTERFACE != 'Ethernet0/3', "ห้ามแตะ Ethernet0/3"

    target = mapping.DEVICE_SSH[DEVICE]
    try:
        conn = ConnectHandler(
            device_type='cisco_ios', host=target['host'], port=target['port'],
            timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT, **CREDENTIALS,
        )
        conn.enable()
    except Exception as e:
        print(f"❌ SSH เข้า {DEVICE} ไม่ได้: {type(e).__name__}")
        return

    try:
        print()
        print("=" * 68)
        print(f"  ทดสอบ hold-queue บน {DEVICE} {INTERFACE}")
        print("  จะคืนค่าเป็น hold-queue 75 in ให้เสมอก่อนจบ")
        print("=" * 68)
        print()

        before = read_queue(conn)
        print(f"  ก่อนสั่ง : Input queue = {before}   (size/max/drops/flushes)")

        result = conn.send_config_set([f'interface {INTERFACE}', 'hold-queue 1 in'])
        rejected = '% ' in result and 'invalid' in result.lower()
        if rejected:
            print()
            print("  --- IOS ตอบกลับ ---")
            print(result)

        cfg = conn.send_running_config = conn.send_command(
            f'show running-config interface {INTERFACE}')
        cfg_has_holdqueue = 'hold-queue' in cfg.lower()

        after = read_queue(conn)
        print(f"  หลังสั่ง : Input queue = {after}")
        print()
        print("  --- running-config หลังสั่ง ---")
        print(cfg)

        max_changed = bool(before and after and after[1] != before[1])

        print("=" * 68)
        print(f"  running-config เก็บ hold-queue ไว้ : {'✅ เก็บ' if cfg_has_holdqueue else '❌ ไม่เก็บ'}")
        print(f"  ค่า max ของคิวเปลี่ยนจริง          : {'✅ เปลี่ยน' if max_changed else '❌ ไม่เปลี่ยน'}")
        print()

        if cfg_has_holdqueue and max_changed:
            print("  ✅ IOL รองรับ — C06 ทำได้ ไปทำต่อได้เลย")
            print("     ขั้นต่อไป: ย่อคิวแล้ว ping รัวๆ จาก Site เข้าหา Core ให้ drops ขึ้นจริง")
        elif cfg_has_holdqueue and not max_changed:
            print("  ⚠️  เก็บคำสั่งไว้แต่ค่าจริงไม่เปลี่ยน — อาการเดียวกับ duplex")
            print("     ถือว่าไม่รองรับ ให้ตัด C06 ทิ้งเหมือน C05")
        else:
            print("  ❌ IOL ไม่เก็บคำสั่ง — C06 ทำไม่ได้ ตัดทิ้งเหมือน C05")
        print("=" * 68)
        print()

    finally:
        try:
            conn.send_config_set([f'interface {INTERFACE}', f'hold-queue {DEFAULT_QUEUE} in'])
            print(f"🔄 คืนค่า {INTERFACE} เป็น hold-queue {DEFAULT_QUEUE} in แล้ว")
        except Exception as e:
            print(f"⚠️ คืนค่าไม่สำเร็จ ({type(e).__name__}) — ตรวจด้วยตนเองว่าคิวกลับเป็น {DEFAULT_QUEUE}")
        conn.disconnect()


if __name__ == '__main__':
    main()
