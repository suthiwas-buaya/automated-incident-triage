r"""
Phase 6 งาน D - run_ground_truth_batch.py: ยิงชุดเคสอ้างอิงสำหรับวัดความแม่นยำการจำแนก

*** ต่างจาก run_latency_batch.py ตรงไหน ***
  run_latency_batch  = ยิง fault แบบเดียวซ้ำๆ เพื่อวัด "เวลา"     -> เกณฑ์ข้อ 1
  run_ground_truth   = ยิง fault หลายแบบตามรายการ เพื่อวัด "การจำแนก" -> เกณฑ์ข้อ 3
ห้ามเอาผลของสองตัวนี้มาปนกัน (Phase6_runbook.md งาน D ย่อหน้าแรก)

*** สคริปต์นี้ไม่ยืนยันกลุ่มให้ และจะไม่มีวันทำ ***
`confirmed_by` ต้องแปลว่า "คนดูแล้วเห็นด้วย" ถ้าเครื่องเขียนแทน ตัวเลขเกณฑ์ข้อ 2 และข้อ 3
พังพร้อมกันทั้งคู่ (ดู tools/review_worksheet.py บรรทัด 3) การกดยืนยันต้องทำใน Dashboard เท่านั้น

*** สคริปต์นี้ไม่บอกคำตอบที่คาดหวัง ***
ตามกฎ D3 ของ Phase 6 — ต้องเขียน expected_group เองก่อนดูคำตอบของระบบ
รายการเคสด้านล่างจึงบอกแค่ "ยิงอะไรที่ไหน" ไม่บอกว่าควรเป็นกลุ่มไหน

วิธีรัน (ต้องเปิด lab + syslog_server.py ค้างไว้):
    py run_ground_truth_batch.py --list          ดูรายการเคสเฉยๆ ไม่ยิง
    py run_ground_truth_batch.py --preflight     ตรวจความพร้อม (สถานะ interface + logging host)
    py run_ground_truth_batch.py                 ยิงชุด A (GT01-GT13)
    py run_ground_truth_batch.py --batch b       ยิงชุด B (GT15-GT28 · 20 Ticket · ~21 นาที)
    py run_ground_truth_batch.py --list --batch b   ดูรายการชุด B เฉยๆ
    py run_ground_truth_batch.py --only GT13     ยิงเคสเดียว
    py run_ground_truth_batch.py --from GT07     ยิงต่อจากเคสที่ค้าง

หลังยิงเสร็จ:
    py show_tickets.py                           ดูว่าเกิด TT ครบตามจำนวนเคสไหม
    py tools\review_worksheet.py                 สร้างใบงานแบบปิดคำตอบ
    (กรอก my_group ใน docs\review_worksheet.csv ให้ครบก่อน)
    py tools\review_worksheet.py --reveal        เฉลย
    -> แล้วเข้า Dashboard กดยืนยันทีละใบด้วยตัวเอง
"""
import csv
import os
import sys
import time
from datetime import datetime

from netmiko import ConnectHandler

from lib import mapping
from lib.diagnostics import CREDENTIALS, SSH_TIMEOUT

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_CSV = os.path.join(BASE_DIR, 'docs', 'ground_truth_plan.csv')

HOLD_SECONDS = 15      # ปล่อยให้ล่มค้างกี่วินาที (>= 10 ตาม CLAUDE.md ข้อ 2)
GAP_SECONDS = 15       # เว้นก่อนเคสถัดไป
UPLINK = 'Ethernet0/3'  # 🚫 ทางเข้า lab ห้ามแตะเด็ดขาด

# ---------------------------------------------------------------------------
# รายการเคส — บอกแค่ "ยิงอะไรที่ไหน" ไม่บอกคำตอบที่คาดหวัง (กฎ D3)
#
# device     = เครื่องที่ต้อง SSH เข้าไปสั่ง
# interfaces = interface บนเครื่องนั้นที่จะสั่ง shutdown พร้อมกัน
# ---------------------------------------------------------------------------
CASES = [
    {'id': 'GT01', 'device': 'Core', 'interfaces': ['Ethernet0/0'],
     'desc': 'สายเดี่ยว Core -> Site A'},
    {'id': 'GT02', 'device': 'Core', 'interfaces': ['Ethernet0/1'],
     'desc': 'สายเดี่ยว Core -> Site B'},
    {'id': 'GT03', 'device': 'Core', 'interfaces': ['Ethernet0/2'],
     'desc': 'สายเดี่ยว Core -> Site C'},
    {'id': 'GT04', 'device': 'Core', 'interfaces': ['Ethernet0/0'],
     'desc': 'สายเดี่ยว Core -> Site A (ซ้ำรอบสอง)'},
    {'id': 'GT05', 'device': 'Core', 'interfaces': ['Ethernet0/1'],
     'desc': 'สายเดี่ยว Core -> Site B (ซ้ำรอบสอง)'},
    {'id': 'GT06', 'device': 'Core', 'interfaces': ['Ethernet0/2'],
     'desc': 'สายเดี่ยว Core -> Site C (ซ้ำรอบสอง)'},

    {'id': 'GT07', 'device': 'Core', 'interfaces': ['Ethernet0/0', 'Ethernet0/1'],
     'desc': '2 Site พร้อมกัน (A + B)'},
    {'id': 'GT08', 'device': 'Core', 'interfaces': ['Ethernet0/1', 'Ethernet0/2'],
     'desc': '2 Site พร้อมกัน (B + C)'},

    {'id': 'GT09', 'device': 'Core', 'interfaces': ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2'],
     'desc': '3 Site พร้อมกัน'},
    {'id': 'GT10', 'device': 'Core', 'interfaces': ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2'],
     'desc': '3 Site พร้อมกัน (ซ้ำรอบสอง)'},

    {'id': 'GT11', 'device': 'Core', 'interfaces': ['Loopback0'],
     'desc': 'Loopback ของอุปกรณ์แกนกลาง'},
    {'id': 'GT12', 'device': 'Core', 'interfaces': ['Loopback0'],
     'desc': 'Loopback ของอุปกรณ์แกนกลาง (ซ้ำรอบสอง)'},

    # ⬇️ เคสที่อาจทำให้ระบบตอบไม่ตรงกับที่คนคิด
    #    อย่าอ่านกฎก่อนตัดสิน — เขียน my_group จากสิ่งที่ "ควรจะเป็น" ในมุมของงานจริง
    #
    # 🔴 คำเตือนจากของจริง (20 ส.ค. 2026): เคสนี้ **ตัดเส้นทาง SSH ของตัวเอง**
    #    การสั่ง shutdown Loopback0 บน Access Site ทำให้ session ที่กำลังใช้อยู่ตายกลางคัน
    #    netmiko โยน ReadTimeout ("Pattern not detected") และสคริปต์ซ่อมกลับให้ไม่ทัน
    #    -> ต้องกู้ผ่าน Console ใน CML Web UI เท่านั้น
    #
    #    ✅ แต่ syslog ยังส่งออกมาทัน TT จึงเกิดครบ (INC-20260820-0061) เคสนี้จึง "สำเร็จ"
    #       ในแง่ข้อมูล แต่ "ล้มเหลว" ในแง่การกู้คืนอัตโนมัติ
    #
    #    📌 บทเรียน: Loopback ของ Access Site เป็น interface ต้องห้ามระดับเดียวกับ Ethernet0/3
    #       เพราะเป็นเส้นทางจัดการ (management path) ของ lab
    {'id': 'GT13', 'device': 'Access-Site-A', 'interfaces': ['Loopback0'],
     'desc': 'Loopback ของอุปกรณ์ที่ Access Site A  ⚠️ ตัด SSH ตัวเอง ต้องกู้ผ่าน Console'},

    # GT14 (Loopback ที่ Site B) ตัดออกแล้ว — GT13 ให้เคสกำกวมที่ต้องการครบแล้ว
    # ยิงซ้ำอีกใบมีแต่จะต้องกู้ Console อีกรอบโดยไม่ได้ข้อมูลใหม่
]

# ---------------------------------------------------------------------------
# ชุด B (GT15-GT32) — เพิ่ม 20 ส.ค. 2026 ให้ตัวหารถึงเป้า และทดสอบเส้นแบ่งของกฎ
#
# ⚠️ กลไก correlation ที่ต้องเข้าใจก่อนอ่านชุดนี้ (lib/correlation.py):
#     MIN_SITES = 2 · WINDOW_SECONDS = 60
#     ทำงานกับ db.list_open_site_tickets() = **เฉพาะใบที่ยังเปิดอยู่**
#     -> เงื่อนไขจริงไม่ใช่ "ยิงห่างกันกี่วินาที" แต่คือ
#        "มี Site อื่นล่มค้างอยู่พร้อมกันภายใน 60 วินาทีไหม"
#
# ⚖️ จัดสัดส่วนให้สมดุล (แก้ 20 ส.ค. หลังพบว่าร่างแรกเป็น Network-Access ทั้ง 20 ใบ)
#     ร่างแรก 16 เคสเดี่ยว + 2 เคสเส้นแบ่ง = Access ล้วน -> เพิ่มจำนวนแต่ไม่เพิ่มความครอบคลุม
#     ซึ่งเป็นกับดักเดียวกับที่ประกาศไว้เองตอนต้นงาน D
#
# GT15-GT21 : สายเดี่ยว 7 ใบ  เว้น 65 วิ -> ไม่มีอะไรค้างพร้อมกัน
# GT22-GT24 : หลาย Site ล่มพร้อมกัน (2+2+3 = 7 ใบ)
# GT25-GT26 : Loopback ที่ Core 2 ใบ
# GT27-GT28 : **ทดสอบเส้นแบ่ง** — Site แรกล่มค้างไว้ Site ที่สองล่มตามอีก 75 วิ
#             (เกินหน้าต่าง 60) โดยใบแรกยังเปิดอยู่ -> ควรแยกเป็นคนละเหตุ ไม่ correlate
#             ถ้า correlate = เจอบั๊กจริง · ถ้าไม่ = พิสูจน์ว่ากฎแม่นทั้งสองด้าน
#             (ตอนนี้มีแต่หลักฐานฝั่ง "ใกล้กันแล้ว correlate ถูก" ยังไม่มีฝั่งตรงข้าม)
# ---------------------------------------------------------------------------
_ROTATION = ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2']
_SITE_OF = {'Ethernet0/0': 'Site A', 'Ethernet0/1': 'Site B', 'Ethernet0/2': 'Site C'}

CASES_B = [
    # --- เคสเดี่ยว 7 ใบ (สลับ 3 เส้น) ---
    {'id': f'GT{15 + i}', 'device': 'Core', 'interfaces': [_ROTATION[i % 3]],
     'desc': f'สายเดี่ยว Core -> {_SITE_OF[_ROTATION[i % 3]]} (รอบที่ {i // 3 + 1})'}
    for i in range(7)
] + [
    # --- หลาย Site ล่มพร้อมกัน 7 ใบ ---
    {'id': 'GT22', 'device': 'Core', 'interfaces': ['Ethernet0/0', 'Ethernet0/2'],
     'desc': '2 Site พร้อมกัน (A + C)'},
    {'id': 'GT23', 'device': 'Core', 'interfaces': ['Ethernet0/1', 'Ethernet0/2'],
     'desc': '2 Site พร้อมกัน (B + C)'},
    {'id': 'GT24', 'device': 'Core', 'interfaces': ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2'],
     'desc': '3 Site พร้อมกัน'},

    # --- Loopback ที่ Core 2 ใบ ---
    {'id': 'GT25', 'device': 'Core', 'interfaces': ['Loopback0'],
     'desc': 'Loopback ของอุปกรณ์แกนกลาง'},
    {'id': 'GT26', 'device': 'Core', 'interfaces': ['Loopback0'],
     'desc': 'Loopback ของอุปกรณ์แกนกลาง (ซ้ำรอบสอง)'},

    # --- ทดสอบเส้นแบ่งของหน้าต่างเวลา 4 ใบ ---
    {'id': 'GT27', 'device': 'Core',
     'stagger': [('Ethernet0/0', 0), ('Ethernet0/1', 75)],
     'desc': 'ทดสอบเส้นแบ่ง — A ล่มค้าง แล้ว B ล่มตามอีก 75 วิ (เกินหน้าต่าง 60)'},
    {'id': 'GT28', 'device': 'Core',
     'stagger': [('Ethernet0/1', 0), ('Ethernet0/2', 75)],
     'desc': 'ทดสอบเส้นแบ่ง — B ล่มค้าง แล้ว C ล่มตามอีก 75 วิ (เกินหน้าต่าง 60)'},
]

for _c in CASES_B:
    if 'interfaces' not in _c:
        _c['interfaces'] = [_i for _i, _d in _c['stagger']]

BATCHES = {'a': CASES, 'b': CASES_B}
GAP_BY_BATCH = {'a': GAP_SECONDS, 'b': 65}

# interface ที่แตะแล้วจะตัดเส้นทางจัดการของตัวเอง — ต้องยืนยันเป็นพิเศษก่อนยิง
MANAGEMENT_RISK = {
    ('Access-Site-A', 'Loopback0'),
    ('Access-Site-B', 'Loopback0'),
    ('Access-Site-C', 'Loopback0'),
}


def connect(device):
    target = mapping.DEVICE_SSH[device]
    conn = ConnectHandler(
        device_type='cisco_ios', host=target['host'], port=target['port'],
        timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT, **CREDENTIALS,
    )
    conn.enable()
    return conn


def apply_state(conn, interfaces, state):
    command = 'shutdown' if state == 'down' else 'no shutdown'
    lines = []
    for itf in interfaces:
        if itf == UPLINK:
            raise RuntimeError(f'ปฏิเสธคำสั่ง — {UPLINK} คือทางเข้า lab ห้ามแตะ')
        lines.extend([f'interface {itf}', command])
    conn.send_config_set(lines)


def countdown(seconds, message):
    for remaining in range(seconds, 0, -1):
        print(f"\r     {message} {remaining:>3} วิ ", end='', flush=True)
        time.sleep(1)
    print("\r" + " " * 62 + "\r", end='', flush=True)


def show_list(cases=None, gap=None):
    cases = cases if cases is not None else CASES
    gap = gap if gap is not None else GAP_SECONDS
    print()
    print("รายการเคสของงาน D — ยิงอะไรที่ไหน (ไม่บอกคำตอบที่คาดหวัง ตามกฎ D3)")
    print("=" * 74)
    for c in cases:
        if 'stagger' in c:
            itfs = ' -> '.join(f"{i} (+{d} วิ)" for i, d in c['stagger'])
        else:
            itfs = ', '.join(c['interfaces'])
        print(f"  {c['id']}  {c['device']:<15} {itfs:<45} {c['desc']}")
    print("=" * 74)
    total = sum(
        (max(d for _, d in c['stagger']) if 'stagger' in c else 0) + HOLD_SECONDS + gap
        for c in cases
    ) / 60
    tickets = sum(len(c['interfaces']) for c in cases)
    print(f"  {len(cases)} เคส · คาดว่าได้ราว {tickets} Ticket · ใช้เวลาประมาณ {total:.0f} นาที")
    print()


def preflight(cases):
    """ตรวจ 2 อย่างต่อเครื่องที่จะใช้: interface ปกติอยู่ไหม และตั้ง logging host ไว้หรือยัง

    ทำไมต้องตรวจ logging host: เคส GT13/GT14 ยิงที่ Access Site ไม่ใช่ที่ Core
    ถ้า Site ไม่ได้ตั้ง logging host ไว้ syslog จะไม่มาถึงเครื่องเรา -> ไม่เกิด TT
    โดยหน้าจอดูปกติทุกอย่าง (ความล้มเหลวแบบเงียบชนิดเดียวกับเรื่องนาฬิกา)
    """
    devices = sorted({c['device'] for c in cases})
    ok = True
    print()
    print("ตรวจความพร้อมก่อนยิง")
    print("=" * 74)
    for device in devices:
        try:
            conn = connect(device)
        except Exception as e:
            print(f"  🔴 {device:<15} SSH เข้าไม่ได้ ({type(e).__name__})")
            ok = False
            continue
        try:
            brief = conn.send_command('show ip interface brief')
            logging_cfg = conn.send_command('show running-config | include logging')
        finally:
            conn.disconnect()

        needed = sorted({i for c in cases if c['device'] == device for i in c['interfaces']})
        down = []
        missing = []
        for itf in needed:
            line = next((l for l in brief.splitlines() if l.split() and l.split()[0] == itf), None)
            if line is None:
                missing.append(itf)
            elif 'down' in ' '.join(line.split()[4:]):
                down.append(itf)

        has_logging = 'logging host' in logging_cfg or 'logging ' in logging_cfg
        mark = '✅' if (not down and not missing and has_logging) else '⚠️'
        print(f"  {mark} {device:<15} interface ที่ต้องใช้: {', '.join(needed)}")
        if missing:
            print(f"       🔴 ไม่มี interface: {', '.join(missing)} — ต้องแก้รายการเคส")
            ok = False
        if down:
            print(f"       🔴 ล่มค้างอยู่: {', '.join(down)} — ต้องซ่อมก่อน ไม่งั้นเคสแรกจะไม่เกิด TT")
            ok = False
        if not has_logging:
            print(f"       🟡 ไม่พบ 'logging' ใน running-config — syslog อาจไม่ถูกส่งมาถึงเครื่องเรา")
            print(f"          เคสของเครื่องนี้อาจไม่เกิด TT เลย · ถ้าเกิดจริงให้บันทึกเป็นข้อค้นพบ ไม่ใช่ยิงซ้ำเงียบๆ")
    print("=" * 74)
    return ok


def append_plan(rows):
    os.makedirs(os.path.dirname(PLAN_CSV), exist_ok=True)
    exists = os.path.exists(PLAN_CSV)
    fields = ['case_id', 'device', 'interfaces', 'desc', 'fired_at']
    with open(PLAN_CSV, 'a', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    args = sys.argv[1:]

    if '--list' in args:
        which = args[args.index('--batch') + 1].lower() if '--batch' in args else 'a'
        show_list(BATCHES.get(which, CASES), GAP_BY_BATCH.get(which, GAP_SECONDS))
        return

    batch = 'a'
    if '--batch' in args:
        batch = args[args.index('--batch') + 1].lower()
        if batch not in BATCHES:
            print(f"ไม่รู้จักชุด {batch} — เลือกได้: {', '.join(BATCHES)}")
            sys.exit(1)
    base = BATCHES[batch]
    gap = GAP_BY_BATCH[batch]

    cases = base
    if '--only' in args:
        want = args[args.index('--only') + 1].upper()
        cases = [c for c in base if c['id'] == want]
        if not cases:
            print(f"ไม่พบเคส {want} ในชุด {batch}")
            sys.exit(1)
    elif '--from' in args:
        want = args[args.index('--from') + 1].upper()
        ids = [c['id'] for c in base]
        if want not in ids:
            print(f"ไม่พบเคส {want} ในชุด {batch}")
            sys.exit(1)
        cases = base[ids.index(want):]

    if '--preflight' in args:
        preflight(cases)
        return

    show_list(cases, gap)
    print("  ⚠️ ต้องเปิด syslog_server.py ค้างไว้แล้ว ไม่งั้นไม่มี TT เกิดเลย")
    print("  ⚠️ ห้ามรีสตาร์ต node ใน CML ระหว่างชุดนี้")
    print("  📌 ชุดนี้ใช้วัด 'การจำแนก' ไม่ใช่ 'เวลา' — ห้ามเอาผลไปปนกับงาน B")
    print()

    if not preflight(cases):
        print("  🔴 ยังไม่พร้อม แก้ตามที่แจ้งด้านบนก่อน")
        sys.exit(1)

    answer = input("\n  พิมพ์ start แล้วกด Enter เพื่อเริ่ม (Enter เฉยๆ = ยกเลิก): ").strip().lower()
    if answer != 'start':
        print("  ยกเลิก")
        return

    started_at = datetime.now()
    print()
    print(f"  ⏱  เริ่มชุดเมื่อ {started_at.strftime('%Y-%m-%dT%H:%M:%S')}")
    print()

    fired = []
    current = None
    try:
        for i, case in enumerate(cases, 1):
            current = case
            itfs = ', '.join(case['interfaces'])
            fired_at = datetime.now()
            print(f"  [{fired_at.strftime('%H:%M:%S')}] {case['id']} ({i}/{len(cases)}) "
                  f"{case['device']} · {itfs} — {case['desc']}")

            # ⚠️ ห้ามใช้ชื่อ i ตรงนี้ — จะไปทับเลขลำดับเคสของ enumerate แล้ว
            #    เงื่อนไข `if i < len(cases)` ท้ายลูปจะพังโดยไม่มีอะไรฟ้อง (เจอจริงตอนรีวิว)
            risky = [itf for itf in case['interfaces'] if (case['device'], itf) in MANAGEMENT_RISK]
            if risky:
                print(f"       🔴 เคสนี้จะตัดเส้นทาง SSH ของตัวเอง ({', '.join(risky)} บน {case['device']})")
                print(f"          syslog จะยังออกมาและ TT จะเกิดปกติ แต่**สคริปต์จะซ่อมกลับให้ไม่ได้**")
                print(f"          ต้องกู้เองผ่าน Console ใน CML Web UI: interface {risky[0]} -> no shutdown")
                if input("          พิมพ์ yes ถ้ารับความเสี่ยงนี้ (อย่างอื่น = ข้ามเคสนี้): ").strip().lower() != 'yes':
                    print("          ข้ามเคสนี้")
                    print()
                    continue

            if 'stagger' in case:
                # ยิงทีละ interface ตามเวลาที่กำหนด แล้วปล่อยล่มค้างไว้พร้อมกัน
                for itf, delay in case['stagger']:
                    if delay:
                        countdown(delay, f"รอก่อนล่ม {itf} อีก")
                    print(f"       ↳ [{datetime.now().strftime('%H:%M:%S')}] ล่ม {itf}")
                    conn = connect(case['device'])
                    try:
                        apply_state(conn, [itf], 'down')
                    finally:
                        conn.disconnect()
                countdown(HOLD_SECONDS, "ล่มค้างไว้พร้อมกัน อีก")
                conn = connect(case['device'])
                try:
                    apply_state(conn, case['interfaces'], 'up')
                finally:
                    conn.disconnect()
                fired.append({
                    'case_id': case['id'], 'device': case['device'],
                    'interfaces': itfs, 'desc': case['desc'],
                    'fired_at': fired_at.strftime('%Y-%m-%d %H:%M:%S'),
                })
                if i < len(cases):
                    countdown(gap, "พักก่อนเคสถัดไป อีก")
                print()
                continue

            conn = connect(case['device'])
            try:
                apply_state(conn, case['interfaces'], 'down')
            except Exception as e:
                if risky:
                    # คาดไว้แล้ว — session ตายเพราะเราตัดสายตัวเอง ไม่ใช่ความผิดพลาด
                    print(f"       ⚠️ session ตายตามคาด ({type(e).__name__}) — คำสั่งถูกส่งไปแล้ว")
                    print(f"          ไปกู้ผ่าน Console แล้วรันต่อด้วย --from <เคสถัดไป>")
                    fired.append({
                        'case_id': case['id'], 'device': case['device'],
                        'interfaces': itfs, 'desc': case['desc'],
                        'fired_at': fired_at.strftime('%Y-%m-%d %H:%M:%S'),
                    })
                    append_plan(fired)
                    raise
                raise
            finally:
                try:
                    conn.disconnect()
                except Exception:
                    pass

            countdown(HOLD_SECONDS, "ล่มค้างไว้ อีก")

            conn = connect(case['device'])
            try:
                apply_state(conn, case['interfaces'], 'up')
            finally:
                conn.disconnect()

            fired.append({
                'case_id': case['id'], 'device': case['device'],
                'interfaces': itfs, 'desc': case['desc'],
                'fired_at': fired_at.strftime('%Y-%m-%d %H:%M:%S'),
            })

            if i < len(cases):
                countdown(gap, "พักก่อนเคสถัดไป อีก")
            print()

    except KeyboardInterrupt:
        print()
        print("  ⛔ หยุดกลางคัน — กำลังซ่อมให้ก่อนออก...")
        try:
            conn = connect(current['device'])
            try:
                apply_state(conn, current['interfaces'], 'up')
            finally:
                conn.disconnect()
            print("  ✅ ซ่อมเรียบร้อย")
        except Exception as e:
            print(f"  🔴 ซ่อมอัตโนมัติไม่สำเร็จ ({e})")
            print("     รันเอง: py fault_injection.py multi up")
        if fired:
            append_plan(fired)
            print(f"  💾 บันทึก {len(fired)} เคสที่ยิงไปแล้วลง {PLAN_CSV}")
            print(f"     ยิงต่อด้วย: py run_ground_truth_batch.py --from <case_id ถัดไป>")
        sys.exit(1)

    append_plan(fired)

    print("=" * 74)
    print(f"  ✅ ยิงครบ {len(fired)} เคส")
    print(f"  💾 บันทึกรายการลง {PLAN_CSV}")
    print()
    print("  ขั้นต่อไป — ตามลำดับ ห้ามสลับ:")
    print("     1) py show_tickets.py                  ตรวจว่าเกิด TT ครบตามจำนวนเคสไหม")
    print("     2) py tools\\review_worksheet.py        สร้างใบงานแบบปิดคำตอบ")
    print("     3) เปิด docs\\review_worksheet.csv กรอก my_group ให้ครบ **ก่อนดูเฉลย**")
    print("     4) py tools\\review_worksheet.py --reveal")
    print("     5) เข้า Dashboard กดยืนยันทีละใบด้วยตัวเอง (สคริปต์กดแทนไม่ได้)")
    print("     6) py check_accuracy.py")
    print()
    print("  ⚠️ ถ้า TT เกิดไม่ครบ อย่ายิงเพิ่มเงียบๆ — บันทึกว่าขาดกี่ใบเพราะอะไร (ข้อห้ามข้อ 2)")
    print("=" * 74)
    print()


if __name__ == '__main__':
    main()
