r"""
Phase 6 งาน B - measure_latency.py: วัดเวลา Quick Status (เกณฑ์ข้อ 1 ของเล่ม: <= 5 วินาที)

นิยามที่ใช้วัด (ทาง ก - ประกาศในเล่มตามนี้):
    t0 = event_start_time  = เวลาที่ Router ฝังมาในข้อความ syslog
    t1 = classified_at     = เวลาที่ระบบจำแนกทีม+ความด่วนเสร็จ (นาฬิกาเครื่อง PC)
    เวลาที่วัด = t1 - t0   *** ไม่รวม Deep Diagnostic (SSH) ตามที่เล่มระบุ ***

*** ข้อควรระวังที่ต้องประกาศในเล่ม ***
t0 กับ t1 มาจากคนละนาฬิกา ถ้าเหลื่อมกัน N วินาที ตัวเลขจะเพี้ยน N วินาที
ยืนยันแล้วว่าเกิดขึ้นจริง: ข้อมูลย้อนหลังก่อน 20 ส.ค. 2026 มีค่าติดลบ (-0.008 วิ)
ซึ่งเป็นไปไม่ได้ทางกายภาพ และมีค่าโดด 47 วิ / 1829 วิ จากนาฬิกาเหลื่อมหลังรีบูต lab

จึงต้อง:
  1. รัน --check-clock ก่อนยิงชุด แล้วจดส่วนต่างไว้
  2. ยิง fault ครบชุด
  3. รัน --check-clock อีกครั้ง เทียบว่านาฬิกาเลื่อนระหว่างทางไหม
  4. รายงานผลพร้อมความคลาดเคลื่อนของนาฬิกา ไม่ใช่รายงานตัวเลขลอยๆ

รูปแบบเวลาในฐานข้อมูล (สำคัญ - เคยทำให้สคริปต์พังทุกแถว):
    event_start_time = รูปแบบ Cisco  "Aug  6 2026 16:17:31.088"   <- ไม่ใช่ ISO
    classified_at    = รูปแบบ ISO     "2026-08-06 16:17:33.000"

วิธีรัน:
    py measure_latency.py --check-clock       ตรวจส่วนต่างนาฬิกา Router กับ PC (ต้องเปิด lab)
    py measure_latency.py                     คำนวณจากทุกใบในฐานข้อมูล
    py measure_latency.py --since 2026-08-20  คำนวณเฉพาะชุดที่ยิงตั้งแต่เวลานั้น
    py measure_latency.py --since 2026-08-20T14:56 --until 2026-08-20T15:15 --csv   <- ชุดวัดจริงของงาน B
    py measure_latency.py --since 2026-08-20 --offset -1.2   หักส่วนต่างนาฬิกาออก

🔴 ทำไมต้องมี --until (เพิ่ม 20 ส.ค. 2026):
   --since อย่างเดียวไม่มีขอบบน พอยิง fault ชุดใหม่ทีหลัง (งาน D ยิงเพื่อวัด "การจำแนก"
   ไม่ใช่ "เวลา") คำสั่งเดิมจะกวาดชุดใหม่เข้ามาปนโดยไม่มีอะไรฟ้อง
   -> รันคำสั่งเดิมซ้ำแล้วได้คนละตัวเลข จาก 40 ใบกลายเป็น 80 ใบ ทั้งที่ตั้งใจวัดชุดเดียว
   เกณฑ์ข้อ 1 จึงต้องระบุช่วงเวลาทั้งต้นและปลายเสมอ
   (ชุดของงาน B คือ 2026-08-20T14:56 ถึง 15:15 = 40 ใบ)
"""
import csv
import os
import re
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'tickets.db')
CSV_PATH = os.path.join(BASE_DIR, 'docs', 'latency_results.csv')

TARGET_SECONDS = 5.0

# "Aug  6 2026 16:17:31.088" - วันที่หลักเดียวมีช่องว่าง 2 ตัว จึงต้อง split/join ก่อน parse
CISCO_TIME_FMT = '%b %d %Y %H:%M:%S.%f'

# ผลของ show clock เช่น "15:50:03.123 ICT Thu Aug 20 2026"
SHOW_CLOCK_RE = re.compile(
    r'(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})\.(?P<ms>\d+)\s+\S+\s+\w{3}\s+'
    r'(?P<mon>\w{3})\s+(?P<d>\d{1,2})\s+(?P<y>\d{4})'
)


# ---------------------------------------------------------------- parse เวลา

def parse_event_time(value):
    """event_start_time เก็บเป็นรูปแบบ Cisco เผื่อ ISO ไว้ด้วยกันพลาด"""
    if not value:
        return None
    text = ' '.join(value.split())          # ยุบช่องว่างซ้ำของวันที่หลักเดียว
    try:
        return datetime.strptime(text, CISCO_TIME_FMT)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def scenario_of(row):
    """แยกสถานการณ์ตามที่เล่มต้องรายงานแยก (B3): fault เดี่ยว vs fault ซ้อนหลายจุด

    ตัดสินจาก classify_reason เพราะ correlation.py เขียนเหตุผลกำกับไว้ตอนรวมเหตุ
    ถ้าคำในเหตุผลเปลี่ยนไปในอนาคต ให้แก้ตรงนี้ที่เดียว
    """
    reason = (row.get('classify_reason') or '')
    if 'พร้อมกัน' in reason or 'correlat' in reason.lower():
        return 'fault ซ้อนหลายจุด'
    return 'fault เดี่ยว'


# ---------------------------------------------------------------- โหมดตรวจนาฬิกา

def check_clock():
    """SSH เข้าทั้ง 4 เครื่อง อ่าน show clock แล้วเทียบกับนาฬิกาเครื่อง PC

    ไม่ตั้งเวลาให้ (นั่นเป็นหน้าที่ของ check_clock.py --set) แค่ "วัดส่วนต่าง"
    เพื่อเอาตัวเลขไปประกาศเป็นความคลาดเคลื่อนของการวัดในเล่ม
    """
    from netmiko import ConnectHandler
    from lib import mapping
    from lib.diagnostics import CREDENTIALS, SSH_TIMEOUT

    print()
    print("=" * 68)
    print("  วัดส่วนต่างนาฬิกา Router เทียบกับเครื่อง PC")
    print("  (ค่าบวก = Router เดินเร็วกว่า PC -> ทำให้เวลาที่วัดได้ 'สั้นลง' เท่านั้น)")
    print("=" * 68)
    print()

    deltas = []
    for device in mapping.DEVICE_SSH:
        target = mapping.DEVICE_SSH[device]
        try:
            conn = ConnectHandler(
                device_type='cisco_ios', host=target['host'], port=target['port'],
                timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT, **CREDENTIALS,
            )
        except Exception as e:
            print(f"  ⚠ {device:<15} SSH เข้าไม่ได้ ({type(e).__name__})")
            continue

        try:
            conn.enable()
            output = conn.send_command('show clock').strip()
            pc_now = datetime.now()          # อ่านนาฬิกา PC ให้ใกล้ที่สุดกับตอนอ่าน show clock
        finally:
            conn.disconnect()

        m = SHOW_CLOCK_RE.search(output)
        if not m:
            print(f"  ⚠ {device:<15} อ่านผล show clock ไม่ออก: {output}")
            continue

        router_now = datetime.strptime(
            f"{m.group('mon')} {m.group('d')} {m.group('y')} "
            f"{m.group('h')}:{m.group('mi')}:{m.group('s')}.{m.group('ms')}",
            CISCO_TIME_FMT,
        )
        delta = (router_now - pc_now).total_seconds()
        deltas.append(delta)

        flag = '✅' if abs(delta) <= 2 else '⚠️'
        print(f"  {flag} {device:<15} Router {router_now.strftime('%H:%M:%S')}  "
              f"PC {pc_now.strftime('%H:%M:%S')}  ส่วนต่าง {delta:+.2f} วิ")

    print()
    if deltas:
        worst = max(deltas, key=abs)
        print(f"  ส่วนต่างมากที่สุด : {worst:+.2f} วินาที")
        print(f"  ค่าเฉลี่ย         : {sum(deltas) / len(deltas):+.2f} วินาที")
        print()
        print("  📌 จดตัวเลขนี้ลง Phase6_runbook.md งาน B ก่อนเริ่มยิง fault")
        if abs(worst) > 2:
            print("  🔴 ส่วนต่างเกิน 2 วินาที — ตั้งนาฬิกาใหม่ก่อน (py check_clock.py --set)")
            print("     ไม่งั้นตัวเลขเกณฑ์ข้อ 1 จะเพี้ยนเกินกว่าจะอธิบายได้")
    print("=" * 68)
    print()


# ---------------------------------------------------------------- โหมดคำนวณ

def load_rows(since=None, until=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tickets")]
    conn.close()

    results = []
    skipped = []
    for r in rows:
        # เฉพาะใบที่มาจาก syslog จริง — ใบแจ้งเอง (MAN-*) ไม่มีเส้นทางจำแนกอัตโนมัติ
        if not r.get('raw_syslog'):
            continue
        t0 = parse_event_time(r.get('event_start_time'))
        t1 = parse_iso(r.get('classified_at'))
        if t0 is None or t1 is None:
            skipped.append((r['incident_number'], 'เวลาอ่านไม่ออก / ยังไม่ถูกจำแนก'))
            continue
        if since and t1 < since:
            continue
        if until and t1 > until:
            continue
        results.append({
            'incident_number': r['incident_number'],
            'node': r.get('node'),
            'interface': r.get('interface'),
            'owner_group': r.get('owner_group'),
            'scenario': scenario_of(r),
            'event_start_time': r.get('event_start_time'),
            'classified_at': r.get('classified_at'),
            'seconds': round((t1 - t0).total_seconds(), 3),
        })
    results.sort(key=lambda x: x['classified_at'])
    return results, skipped


def summarize(label, values):
    if not values:
        return f"  {label:<22} ไม่มีข้อมูล"
    values = sorted(values)
    n = len(values)
    avg = sum(values) / n
    p95 = values[min(n - 1, int(round(0.95 * (n - 1))))]
    over = sum(1 for v in values if v > TARGET_SECONDS)
    return (f"  {label:<22} {n:>4}  {avg:>8.3f}  {values[-1]:>8.3f}  "
            f"{p95:>8.3f}  {over:>6}")


def main():
    args = sys.argv[1:]

    if '--check-clock' in args:
        check_clock()
        return

    since = None
    if '--since' in args:
        since = datetime.fromisoformat(args[args.index('--since') + 1])

    until = None
    if '--until' in args:
        until = datetime.fromisoformat(args[args.index('--until') + 1])

    offset = 0.0
    if '--offset' in args:
        offset = float(args[args.index('--offset') + 1])

    rows, skipped = load_rows(since, until)
    for r in rows:
        r['seconds_adjusted'] = round(r['seconds'] + offset, 3)

    print()
    print("=" * 74)
    print("  เกณฑ์ข้อ 1 — เวลา Quick Status (เป้า <= 5.0 วินาที)")
    print("=" * 74)
    print(f"  นิยาม  : t1 (classified_at) - t0 (event_start_time)  ไม่รวม Deep Diagnostic")
    if since or until:
        print(f"  ช่วง   : {since or '(ไม่จำกัด)'}  ถึง  {until or '(ไม่จำกัด)'}")
    if since and not until:
        print("  ⚠️ ไม่ได้ระบุ --until — ถ้ามีการยิง fault ชุดอื่นทีหลัง จะถูกกวาดมาปนด้วย")
    if offset:
        print(f"  offset : {offset:+.3f} วินาที (หักส่วนต่างนาฬิกาแล้ว)")
    print()

    if not rows:
        print("  ไม่มีข้อมูลในช่วงที่เลือก")
        print()
        return

    print(f"  {'สถานการณ์':<22} {'ครั้ง':>4}  {'เฉลี่ย':>8}  {'สูงสุด':>8}  {'p95':>8}  {'เกิน5':>6}")
    print("  " + "-" * 66)
    for label in ('fault เดี่ยว', 'fault ซ้อนหลายจุด'):
        vals = [r['seconds_adjusted'] for r in rows if r['scenario'] == label]
        print(summarize(label, vals))
    print("  " + "-" * 66)
    print(summarize('รวมทั้งหมด', [r['seconds_adjusted'] for r in rows]))
    print()

    over = [r for r in rows if r['seconds_adjusted'] > TARGET_SECONDS]
    negative = [r for r in rows if r['seconds_adjusted'] < 0]

    if negative:
        print(f"  🔴 พบค่าติดลบ {len(negative)} ใบ — เป็นไปไม่ได้ทางกายภาพ = นาฬิกาเหลื่อม")
        for r in negative:
            print(f"      {r['incident_number']}  {r['seconds_adjusted']:+.3f} วิ")
        print("      อย่าเอาชุดนี้ลงเล่ม ให้ตั้งนาฬิกาใหม่แล้วยิงชุดใหม่")
        print()

    if over:
        print(f"  ⚠️  เกินเป้า 5 วินาที {len(over)} ใบ — ต้องหาสาเหตุแล้วบันทึก ห้ามตัดทิ้ง (ข้อห้าม 2)")
        for r in over:
            print(f"      {r['incident_number']}  {r['seconds_adjusted']:.3f} วิ  "
                  f"({r['scenario']} · {r['owner_group']})")
        print()
    elif not negative:
        print(f"  ✅ ไม่มีใบไหนเกิน {TARGET_SECONDS} วินาที")
        print()

    if skipped:
        print(f"  ℹ️  ข้ามไป {len(skipped)} ใบ (อ่านเวลาไม่ออก หรือยังไม่ถูกจำแนก)")
        for inc, why in skipped:
            print(f"      {inc} — {why}")
        print()

    if '--csv' in args:
        os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
        fields = ['incident_number', 'node', 'interface', 'owner_group', 'scenario',
                  'event_start_time', 'classified_at', 'seconds', 'seconds_adjusted']
        with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k) for k in fields})
        print(f"  💾 เขียนแล้ว: {CSV_PATH}  ({len(rows)} แถว)")
        print("     ไฟล์นี้ทั้งไฟล์ -> ภาคผนวก E-2")
        print()


if __name__ == '__main__':
    main()
