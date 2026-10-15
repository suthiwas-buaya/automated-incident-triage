r"""
Phase 6 งาน B3 - run_latency_batch.py: ยิง fault เป็นชุดเพื่อวัดเวลา Quick Status

ทำไมต้องมีตัวนี้ (ไม่ใช่แค่ขี้เกียจพิมพ์):
  1. create_ticket() ใน lib/db.py จะ **ไม่สร้าง TT ใหม่** ถ้ายังมีใบเปิดค้างของ node+interface เดิม
     -> ทุกรอบต้อง down แล้ว up ให้ครบ ไม่งั้นยิง 10 ครั้งได้ TT ใบเดียว
  2. CLAUDE.md กฎข้อ 2: ต้องเว้น >= 10 วินาทีระหว่าง shutdown กับ no shutdown
     ไม่งั้น IOS ยิง syslog event ไม่ทัน -> ข้อมูลหายเงียบๆ
  3. ระยะห่างที่ไม่สม่ำเสมอทำให้ตัวเลขเทียบกันไม่ได้ เครื่องจับเวลาแม่นกว่าคน

*** สคริปต์นี้ไม่แตะฐานข้อมูลและไม่แตะโค้ดระบบเลย ***
มันแค่เรียก fault_injection.py ซ้ำๆ ตามจังหวะที่กำหนด เหมือนคนพิมพ์เองทุกประการ

วิธีรัน (ต้องเปิด lab + syslog_server.py ค้างไว้):
    py run_latency_batch.py single          10 รอบ ที่ E0/0 อย่างเดียว
    py run_latency_batch.py multi           10 รอบ ทุก Site พร้อมกัน (กระตุ้น correlation)
    py run_latency_batch.py single --rounds 5
    py run_latency_batch.py single --hold 20 --gap 20

ทำตามลำดับ: single ก่อน แล้วค่อย multi (เล่มต้องรายงานแยก 2 สถานการณ์)
กด Ctrl+C หยุดกลางคันได้ แต่ต้องซ่อมสายเองด้วย: py fault_injection.py multi up
"""
import sys
import time
from datetime import datetime

import fault_injection

HOLD_SECONDS = 15      # ปล่อยให้ล่มค้างไว้กี่วินาทีก่อนซ่อม (>= 10 ตาม CLAUDE.md ข้อ 2)
GAP_SECONDS = 15       # เว้นกี่วินาทีหลังซ่อม ก่อนเริ่มรอบถัดไป
DEFAULT_ROUNDS = 10

SCENARIOS = {
    'single': {
        'interfaces': ['Ethernet0/0'],
        'label': 'fault เดี่ยว — Core E0/0 -> Access-Site-A',
    },
    'multi': {
        'interfaces': ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2'],
        'label': 'fault ซ้อนหลายจุด — ทุก Site พร้อมกัน (กระตุ้น correlation)',
    },
}


def countdown(seconds, message):
    """นับถอยหลังให้เห็นบนหน้าจอ จะได้รู้ว่าไม่ได้ค้าง"""
    for remaining in range(seconds, 0, -1):
        print(f"\r     {message} {remaining:>3} วิ ", end='', flush=True)
        time.sleep(1)
    print("\r" + " " * 60 + "\r", end='', flush=True)


def preflight(interfaces):
    """ก่อนเริ่มต้องแน่ใจว่าทุกเส้นอยู่ในสถานะปกติ

    ถ้ามีเส้นไหนล่มค้างอยู่จากรอบก่อน รอบแรกของชุดใหม่จะไม่เกิด TT
    (create_ticket คืนใบเดิม) แล้วชุดข้อมูลจะขาดไป 1 รอบโดยไม่มีอะไรฟ้อง
    """
    print("  ตรวจสถานะก่อนเริ่ม...")
    status = fault_injection.read_status()
    stuck = [i for i in interfaces if status.get(i) == 'down']
    if stuck:
        print()
        print(f"  🔴 มีเส้นที่ล่มค้างอยู่: {', '.join(stuck)}")
        print("     ต้องซ่อมก่อนเริ่มชุดใหม่ ไม่งั้นรอบแรกจะไม่เกิด TT")
        print("     รัน: py fault_injection.py multi up")
        return False
    print("  ✅ ทุกเส้นปกติ พร้อมเริ่ม")
    return True


def main():
    args = sys.argv[1:]
    if not args or args[0] not in SCENARIOS:
        print(__doc__)
        print(f"เลือกสถานการณ์: {' หรือ '.join(SCENARIOS)}")
        sys.exit(1)

    name = args[0]
    scenario = SCENARIOS[name]
    rounds = DEFAULT_ROUNDS
    hold = HOLD_SECONDS
    gap = GAP_SECONDS

    if '--rounds' in args:
        rounds = int(args[args.index('--rounds') + 1])
    if '--hold' in args:
        hold = int(args[args.index('--hold') + 1])
    if '--gap' in args:
        gap = int(args[args.index('--gap') + 1])

    if hold < 10:
        print("🔴 --hold ต่ำกว่า 10 วินาทีไม่ได้ — IOS จะยิง event ไม่ทัน (CLAUDE.md ข้อ 2)")
        sys.exit(1)

    interfaces = scenario['interfaces']
    total_minutes = rounds * (hold + gap) / 60

    print()
    print("=" * 70)
    print(f"  ยิง fault เป็นชุด — {scenario['label']}")
    print("=" * 70)
    print(f"  จำนวนรอบ      : {rounds}")
    print(f"  interface     : {', '.join(interfaces)}")
    print(f"  ล่มค้างไว้      : {hold} วินาที")
    print(f"  เว้นก่อนรอบถัดไป : {gap} วินาที")
    print(f"  ใช้เวลาประมาณ  : {total_minutes:.0f} นาที")
    print()
    print("  ⚠️ ต้องเปิด syslog_server.py ค้างไว้แล้ว ไม่งั้นไม่มี TT เกิดเลย")
    print("  ⚠️ ห้ามรีสตาร์ต node ใน CML ระหว่างชุดนี้ (นาฬิกาจะหาย)")
    print("=" * 70)
    print()

    if not preflight(interfaces):
        sys.exit(1)

    answer = input("\n  พิมพ์ start แล้วกด Enter เพื่อเริ่ม (Enter เฉยๆ = ยกเลิก): ").strip().lower()
    if answer != 'start':
        print("  ยกเลิก")
        return

    started_at = datetime.now()
    print()
    print(f"  ⏱  เริ่มชุดเมื่อ {started_at.strftime('%Y-%m-%dT%H:%M:%S')}")
    print(f"     📌 จดเวลานี้ไว้ ใช้กับ --since ตอนคำนวณผล")
    print()

    try:
        for i in range(1, rounds + 1):
            stamp = datetime.now().strftime('%H:%M:%S')
            print(f"  [{stamp}] รอบ {i}/{rounds} — ทำให้ล่ม")
            conn = fault_injection.connect()
            try:
                fault_injection.apply_state(conn, interfaces, 'down')
            finally:
                conn.disconnect()

            countdown(hold, "ล่มค้างไว้ อีก")

            print(f"  [{datetime.now().strftime('%H:%M:%S')}] รอบ {i}/{rounds} — ซ่อม")
            conn = fault_injection.connect()
            try:
                fault_injection.apply_state(conn, interfaces, 'up')
            finally:
                conn.disconnect()

            if i < rounds:
                countdown(gap, "พักก่อนรอบถัดไป อีก")
            print()

    except KeyboardInterrupt:
        print()
        print("  ⛔ หยุดกลางคัน — กำลังซ่อมสายให้ก่อนออก...")
        try:
            conn = fault_injection.connect()
            try:
                fault_injection.apply_state(conn, interfaces, 'up')
            finally:
                conn.disconnect()
            print("  ✅ ซ่อมเรียบร้อย")
        except Exception as e:
            print(f"  🔴 ซ่อมอัตโนมัติไม่สำเร็จ ({e}) — รันเอง: py fault_injection.py multi up")
        sys.exit(1)

    ended_at = datetime.now()
    print("=" * 70)
    print(f"  ✅ ครบ {rounds} รอบ")
    print(f"     เริ่ม {started_at.strftime('%Y-%m-%dT%H:%M:%S')} → จบ {ended_at.strftime('%H:%M:%S')}")
    print()
    print("  ขั้นต่อไป:")
    print(f"     py measure_latency.py --since {started_at.strftime('%Y-%m-%dT%H:%M')} --csv")
    print()
    print("  ⚠️ ตรวจก่อนว่า TT เกิดครบตามจำนวนรอบจริงไหม (py show_tickets.py)")
    print("     ถ้าขาด แปลว่ามีรอบที่ syslog หาย -> ต้องบันทึกไว้ ไม่ใช่ยิงเพิ่มเงียบๆ")
    print("=" * 70)
    print()


if __name__ == '__main__':
    main()
