r"""
Phase 5 งาน D ขั้นที่ 1 (ยืนยันซ้ำ) — IOL มีตัวนับ duplex/collisions ให้ดูหรือเปล่า

ทำไมต้องมีสคริปต์ที่สอง: test_duplex.py กรองเฉพาะบรรทัดที่มีคำว่า duplex แล้วได้ค่าว่าง
ทั้งก่อนและหลังสั่ง — ซึ่งแปลได้ 2 อย่าง (ก) สั่งแล้วไม่เปลี่ยน หรือ (ข) IOL ไม่พิมพ์บรรทัดนั้น
ตั้งแต่แรก การสรุปว่า "ไม่รองรับ" จากผลกรองที่ว่างเปล่าจึงเร็วเกินไป

สคริปต์นี้ดูของจริงทั้งก้อน ไม่กรองอะไรเลย เพื่อตอบ 2 คำถามที่ตัดสินชะตางาน D:
  1. คำสั่ง duplex ถูกเก็บลง running-config จริงไหม
  2. show interfaces มีตัวนับ collisions ให้ดูไหม  <- สำคัญที่สุด
     ถ้าไม่มี งาน D จบทันที เพราะต่อให้ตั้ง duplex ได้ก็ไม่มีหลักฐานให้ LLM ตีความ

อ่านอย่างเดียว ไม่แก้ config ใดๆ ทั้งสิ้น

วิธีรัน (ต้องเปิด lab):
    py probe_counters.py
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


def main():
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
        print("=" * 70)
        print(f"  1) running-config ของ {INTERFACE} — คำสั่ง duplex ถูกเก็บไว้ไหม")
        print("=" * 70)
        cfg = conn.send_command(f'show running-config interface {INTERFACE}')
        print(cfg)

        print()
        print("=" * 70)
        print(f"  2) show interfaces {INTERFACE} — ของจริงทั้งก้อน ไม่กรอง")
        print("=" * 70)
        out = conn.send_command(f'show interfaces {INTERFACE}')
        print(out)

        print()
        print("=" * 70)
        print("  สรุป")
        print("=" * 70)
        lowered = out.lower()
        has_duplex = 'duplex' in lowered
        has_collision = 'collision' in lowered
        cfg_has_duplex = 'duplex' in cfg.lower()

        print(f"  running-config มีบรรทัด duplex : {'✅ มี' if cfg_has_duplex else '❌ ไม่มี'}")
        print(f"  show interfaces รายงาน duplex  : {'✅ มี' if has_duplex else '❌ ไม่มี'}")
        print(f"  show interfaces มีตัวนับ collisions : {'✅ มี' if has_collision else '❌ ไม่มี'}")
        print()

        if has_collision:
            for line in out.splitlines():
                if 'collision' in line.lower():
                    print(f"      {line.strip()}")
            print()
            print("  ➡️  มีตัวนับให้ดู — งาน D ยังมีความหวัง ไปขั้นที่ 2 ต่อได้")
        else:
            print("  ➡️  ไม่มีตัวนับ collisions เลย = ต่อให้ตั้ง duplex ได้ก็ไม่มีหลักฐานให้ตีความ")
            print("      งาน D ไปต่อไม่ได้ ให้ถอยไปทางเลือก ก.")
        print("=" * 70)
        print()
    finally:
        conn.disconnect()


if __name__ == '__main__':
    main()
