r"""
ตรวจ (และตั้ง) นาฬิกาของ Router ทั้ง 4 เครื่อง — ฉบับปรับปรุง 20 ส.ค. 2026

ทำไมต้องมีเครื่องมือนี้:
  นาฬิกาของ IOL ไม่ persist ผ่านการรีบูต (CLAUDE.md กฎข้อ 5) พอนาฬิกาไม่ถูกตั้ง IOS จะเติม
  เครื่องหมาย * ไว้หน้า timestamp ของทุกข้อความ syslog เช่น "*Aug 12 2026 15:41:31.733"
  แต่ SYSLOG_RE ใน lib/syslog_parser.py ไม่รองรับ * -> parse ไม่ผ่านทุกบรรทัด ->
  **ระบบไม่สร้าง TT เลยสักใบ โดยหน้าจอดูปกติ** (เสียเวลา debug จริงมาแล้ว 2 รอบ)

*** อะไรเปลี่ยนจากฉบับเดิม (20 ส.ค. 2026) ***
ฉบับเดิมทำงานถูกต้อง แต่ตั้งนาฬิกาได้ "ช้ากว่าความจริง" ทุกครั้ง 0.3-1.6 วินาที
ซึ่งกระทบเกณฑ์ข้อ 1 ของเล่มโดยตรง (t0 มาจากนาฬิกา Router · t1 มาจากนาฬิกา PC)

สาเหตุ 3 ชั้นและวิธีแก้:

  1. ต่อ SSH ทีละเครื่องเรียงกัน -> กว่าจะถึงเครื่องที่ 4 เวลาเลื่อนไป 5-6 วินาที
     แก้: ต่อทั้ง 4 เครื่องพร้อมกันด้วย thread

  2. ตัว .bat เดิมเรียกสคริปต์ 3 รอบ (ตรวจ/ตั้ง/ตรวจ) = SSH 12 ครั้ง
     แก้: --set ทำครบทั้งตั้งและตรวจซ้ำในรอบเดียว ใช้ session เดิม

  3. *** ตัวใหญ่ที่สุด *** คำสั่ง clock set รับได้แค่ระดับวินาที ไม่มีทศนิยม
     สั่ง "clock set 15:20:33" ตอนเวลาจริง 15:20:33.780 -> Router ไปอยู่ที่ .000
     = ช้ากว่าความจริง 0.78 วิ ตั้งแต่วินาทีแรก บวกเวลาเดินทางของ SSH อีก
     แก้: รอจนถึงเส้นแบ่งวินาทีพอดี แล้วส่งคำสั่งล่วงหน้าเท่ากับเวลาเดินทางที่วัดได้จริง
          (เตรียมคำสั่งไว้ในหน่วยความจำก่อน ยิงพร้อมกันทั้ง 4 เครื่องเมื่อถึงจังหวะ)

หมายเหตุ: การตั้งนาฬิกาไม่ใช่การแก้ config ที่กระทบ topology หรือ routing ใดๆ

วิธีรัน (ต้องเปิด lab):
    py check_clock.py           ตรวจอย่างเดียว + รายงานส่วนต่างเทียบนาฬิกา PC
    py check_clock.py --set     ตั้งนาฬิกาทุกเครื่องพร้อมกัน แล้วตรวจซ้ำในรอบเดียว
"""
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from netmiko import ConnectHandler

from lib import mapping
from lib.diagnostics import CREDENTIALS, SSH_TIMEOUT

# ผลของ show clock เช่น "15:50:03.123 ICT Thu Aug 20 2026"
SHOW_CLOCK_RE = re.compile(
    r'(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})\.(?P<ms>\d+)\s+\S+\s+\w{3}\s+'
    r'(?P<mon>\w{3})\s+(?P<d>\d{1,2})\s+(?P<y>\d{4})'
)
CISCO_TIME_FMT = '%b %d %Y %H:%M:%S.%f'

# เผื่อเวลาให้ทุก thread ต่อ SSH เสร็จก่อนถึงจังหวะยิงคำสั่ง
SYNC_LEAD_SECONDS = 2.0

_print_lock = threading.Lock()


def say(msg):
    with _print_lock:
        print(msg, flush=True)


def connect(device):
    target = mapping.DEVICE_SSH[device]
    conn = ConnectHandler(
        device_type='cisco_ios', host=target['host'], port=target['port'],
        timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT, **CREDENTIALS,
    )
    conn.enable()
    return conn


def measure_one_way(conn):
    """วัดเวลาเดินทางเที่ยวเดียวแบบหยาบ จากเวลาไป-กลับของคำสั่งเบาๆ

    ใช้ครึ่งหนึ่งของเวลาไป-กลับเป็นค่าประมาณ เพื่อส่งคำสั่ง clock set "ล่วงหน้า"
    ให้ IOS ประมวลผลตอนที่เวลาจริงตรงกับค่าที่เราสั่งพอดี
    """
    samples = []
    for _ in range(2):
        t = time.time()
        conn.send_command('show clock')
        samples.append((time.time() - t) / 2.0)
    return min(samples)


def read_clock(conn):
    """อ่าน show clock พร้อมจับเวลา PC ให้ใกล้ที่สุด คืน (เวลาRouter, เวลาPC, ข้อความดิบ)"""
    output = conn.send_command('show clock').strip()
    pc_now = datetime.now()
    m = SHOW_CLOCK_RE.search(output)
    if not m:
        return None, pc_now, output
    router_now = datetime.strptime(
        f"{m.group('mon')} {m.group('d')} {m.group('y')} "
        f"{m.group('h')}:{m.group('mi')}:{m.group('s')}.{m.group('ms')}",
        CISCO_TIME_FMT,
    )
    return router_now, pc_now, output


def worker(device, do_set, target_epoch, results):
    """งานของ 1 เครื่อง — รันพร้อมกันทั้ง 4 เครื่อง"""
    entry = {'device': device, 'ok': False}
    results[device] = entry

    try:
        conn = connect(device)
    except Exception as e:
        entry['error'] = f"SSH เข้าไม่ได้ ({type(e).__name__})"
        return

    try:
        if do_set:
            lead = measure_one_way(conn)
            entry['lead'] = lead

            # เตรียมข้อความคำสั่งไว้ก่อน จะได้ไม่เสียเวลาสร้าง string ตอนถึงจังหวะ
            target_dt = datetime.fromtimestamp(target_epoch)
            command = (f"clock set {target_dt.strftime('%H:%M:%S')} "
                       f"{target_dt.day} {target_dt.strftime('%b %Y')}")

            # รอจนถึง "จังหวะยิง" = เวลาเป้าหมาย ลบเวลาเดินทาง
            # ผลคือ IOS ประมวลผลคำสั่งตอนเวลาจริงตรงกับ target พอดี ไม่ช้าไปเศษวินาที
            fire_at = target_epoch - lead
            remaining = fire_at - time.time()
            if remaining > 0:
                time.sleep(remaining)
            conn.send_command_timing(command)

        router_now, pc_now, raw = read_clock(conn)
        entry.update({'ok': True, 'raw': raw, 'router': router_now, 'pc': pc_now})
        if router_now:
            entry['delta'] = (router_now - pc_now).total_seconds()
        entry['authoritative'] = not raw.startswith('*')
    except Exception as e:
        entry['error'] = f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


def main():
    do_set = '--set' in sys.argv
    devices = list(mapping.DEVICE_SSH)

    print()
    print("=" * 70)
    print("  ตรวจนาฬิกา Router ทั้ง 4 เครื่อง (ต่อพร้อมกัน)")
    if do_set:
        print("  โหมดตั้งเวลา — ยิงคำสั่งพร้อมกันทุกเครื่องที่เส้นแบ่งวินาที")
        print("  ชดเชยเวลาเดินทางของ SSH ให้อัตโนมัติ")
    print("=" * 70)
    print()

    # จังหวะร่วมของทุกเครื่อง: เส้นแบ่งวินาทีถัดไปหลังจากเผื่อเวลาต่อ SSH เสร็จ
    target_epoch = math.ceil(time.time() + SYNC_LEAD_SECONDS)
    if do_set:
        print(f"  จะตั้งเป็น {datetime.fromtimestamp(target_epoch).strftime('%H:%M:%S %d %b %Y')} "
              f"(อีก {target_epoch - time.time():.1f} วิ)")
        print()

    results = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        for device in devices:
            pool.submit(worker, device, do_set, target_epoch, results)
    elapsed = time.time() - started

    deltas = []
    not_ok = []
    unreachable = []

    for device in devices:
        entry = results.get(device, {})
        if not entry.get('ok'):
            print(f"  ⚠ {device:<15} {entry.get('error', 'ไม่ทราบสาเหตุ')}")
            unreachable.append(device)
            continue

        if not entry.get('authoritative'):
            print(f"  ❌ {device:<15} {entry['raw']}   <- ยังไม่ยืนยัน (มี *)")
            not_ok.append(device)
            continue

        delta = entry.get('delta')
        if delta is None:
            print(f"  ⚠ {device:<15} อ่านผล show clock ไม่ออก: {entry['raw']}")
            continue

        deltas.append(delta)
        icon = '✅' if abs(delta) <= 0.5 else ('🟡' if abs(delta) <= 2 else '⚠️')
        lead_note = f"  (ชดเชย {entry['lead'] * 1000:.0f} ms)" if 'lead' in entry else ''
        print(f"  {icon} {device:<15} Router {entry['router'].strftime('%H:%M:%S.%f')[:-3]}  "
              f"PC {entry['pc'].strftime('%H:%M:%S.%f')[:-3]}  ส่วนต่าง {delta:+.2f} วิ{lead_note}")

    print()
    print("=" * 70)
    print(f"  ใช้เวลาทั้งหมด {elapsed:.1f} วินาที")

    if deltas:
        worst = max(deltas, key=abs)
        print(f"  ส่วนต่างมากที่สุด : {worst:+.2f} วินาที   เฉลี่ย : {sum(deltas) / len(deltas):+.2f} วินาที")
        print()
        print("  📌 จดตัวเลขนี้ลง Phase6_runbook.md งาน B ก่อนเริ่มยิง fault")
        if abs(worst) > 2:
            print("  🔴 เกิน 2 วินาที — ตั้งใหม่อีกรอบ (py check_clock.py --set)")
        elif abs(worst) > 0.5:
            print("  🟡 ยังเหลือเศษวินาที — ยอมรับได้ แต่ต้องประกาศเป็นความคลาดเคลื่อนในเล่ม")

    if unreachable:
        print(f"  ⚠ SSH เข้าไม่ได้ {len(unreachable)} เครื่อง: {', '.join(unreachable)}")
        print("    ตรวจว่า lab เปิดอยู่และบูตเสร็จแล้ว")
    if not_ok:
        print(f"  🔴 ยังไม่ได้ตั้งนาฬิกา {len(not_ok)} เครื่อง: {', '.join(not_ok)}")
        print("     ถ้ายิง fault ตอนนี้ ระบบจะไม่สร้าง TT เลย")
        if not do_set:
            print()
            print("     แก้ด้วยคำสั่งเดียว:  py check_clock.py --set")
    elif not unreachable:
        print("  ✅ นาฬิกาถูกต้องครบทุกเครื่อง — ยิง fault ได้เลย")
    print("=" * 70)
    print()


if __name__ == '__main__':
    main()
