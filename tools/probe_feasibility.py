r"""
Phase 5 งาน D — เช็คว่า C06 (คิวเต็ม) กับ C08 (OSPF หลุด) ทำบน lab นี้ได้ไหม

อ่านอย่างเดียว ไม่แก้ config ใดๆ ทั้งสิ้น

ตอบ 3 คำถาม:
  1. lab รัน OSPF อยู่ไหม และมีเพื่อนบ้านกี่ตัว   -> ตัดสิน C08
  2. interface รองรับ hold-queue ไหม (ค่าปัจจุบันเท่าไหร่) -> ตัดสิน C06
  3. ตอนนี้มี drops สะสมอยู่เท่าไหร่แล้ว          -> ใช้เป็นค่าตั้งต้นก่อนทดลอง

วิธีรัน (ต้องเปิด lab):
    py probe_feasibility.py
"""
from netmiko import ConnectHandler

# --- ย้ายมาอยู่ใต้โฟลเดอร์ย่อย: บอก Python ให้มองหา lib/ ที่โฟลเดอร์แม่ ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# ---------------------------------------------------------------------

from lib import mapping
from lib.diagnostics import CREDENTIALS, SSH_TIMEOUT

DEVICE = 'Core'
INTERFACE = 'Ethernet0/1'


def section(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    target = mapping.DEVICE_SSH[DEVICE]
    try:
        conn = ConnectHandler(
            device_type='cisco_ios', host=target['host'], port=target['port'],
            timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT, **CREDENTIALS,
        )
        conn.enable()
    except Exception as e:
        print(f"❌ SSH เข้า {DEVICE} ไม่ได้: {type(e).__name__} — ตรวจว่า lab เปิดอยู่")
        return

    try:
        # ---------- C08: OSPF ----------
        section("C08 — lab รัน OSPF อยู่ไหม")
        protocols = conn.send_command('show ip protocols')
        print(protocols if protocols.strip() else '(ไม่มีผลลัพธ์)')

        neighbors = conn.send_command('show ip ospf neighbor')
        print()
        print("--- เพื่อนบ้าน OSPF ---")
        print(neighbors if neighbors.strip() else '(ไม่มี)')

        ospf_running = 'ospf' in protocols.lower()
        has_neighbor = bool(neighbors.strip()) and 'FULL' in neighbors.upper()

        # ---------- C06: hold-queue ----------
        section(f"C06 — คิวของ {INTERFACE} ตอนนี้เป็นยังไง")
        cfg = conn.send_command(f'show running-config interface {INTERFACE}')
        print(cfg)

        out = conn.send_command(f'show interfaces {INTERFACE}')
        queue_lines = [l.strip() for l in out.splitlines()
                       if 'queue' in l.lower() or 'drops' in l.lower()]
        print("--- บรรทัดที่เกี่ยวกับคิว ---")
        for line in queue_lines:
            print(f"  {line}")

        # ---------- สรุป ----------
        section("สรุป — ทำเคสไหนได้บ้าง")

        if ospf_running and has_neighbor:
            print("  ✅ C08 ทำได้ — OSPF รันอยู่และมีเพื่อนบ้านสถานะ FULL")
            print("     วิธีทำ: แก้ค่า dead timer ฝั่งเดียวให้ไม่ตรงกัน -> เพื่อนบ้านหลุดทั้งที่สายยังดี")
        elif ospf_running:
            print("  ⚠️ C08 ไม่แน่ — OSPF ตั้งไว้แต่ยังไม่มีเพื่อนบ้านสถานะ FULL")
        else:
            print("  ❌ C08 ทำไม่ได้ — lab ไม่ได้รัน OSPF (ใช้ static route)")
            print("     ถ้าจะทำต้องตั้ง OSPF เพิ่ม = แก้ config lab เยอะ เสี่ยงกระทบของเดิม")

        print()
        if queue_lines:
            print("  ✅ C06 น่าจะทำได้ — interface รายงานตัวนับคิวและ drops จริง")
            print("     วิธีทำ: hold-queue 1 in (ย่อคิวเหลือ 1) แล้ว ping รัวๆ -> drops ขึ้นจริง")
            print("     แก้กลับ: hold-queue 75 in")
        else:
            print("  ❌ C06 ทำไม่ได้ — ไม่มีบรรทัดคิวใน show interfaces เลย")

        print("=" * 70)
        print()
    finally:
        conn.disconnect()


if __name__ == '__main__':
    main()
