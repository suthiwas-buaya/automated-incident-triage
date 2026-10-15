r"""
Phase 6 - review_worksheet.py: ใบงานสำหรับไล่ยืนยันการจำแนกกลุ่ม (เกณฑ์ข้อ 3)

⚠️ สคริปต์นี้ **อ่านอย่างเดียว ไม่เขียนอะไรลงฐานข้อมูลเลย** — การยืนยันจริงต้องกดใน
Dashboard ด้วยตัวเอง เพราะ confirmed_by ต้องหมายถึง "คนดูแล้วเห็นด้วย" จริงๆ
ถ้าให้สคริปต์เขียนแทน ตัวเลขเกณฑ์ข้อ 2 และข้อ 3 จะใช้ไม่ได้ทั้งคู่

ทำไมต้องมีใบงานนี้: กฎข้อ D3 ของ Phase 6 บอกว่า **ต้องตัดสินเองก่อนดูคำตอบของระบบ**
ไม่งั้นจะเผลอเขียนตามระบบโดยไม่รู้ตัว แล้ว % ความแม่นยำจะสูงเกินจริง

วิธีใช้ 3 ขั้น:
    1) py review_worksheet.py            <- โหมดปิดคำตอบ: อ่าน syslog แล้วเขียนกลุ่มที่คิดเองลง CSV
    2) เปิด docs/review_worksheet.csv กรอกคอลัมน์ my_group ให้ครบ
    3) py review_worksheet.py --reveal   <- เฉลย: เทียบที่คิดไว้กับที่ระบบจำแนก

ตัวเลือกอื่น:
    --all              รวม Ticket ที่ยืนยันไปแล้วด้วย (ปกติแสดงเฉพาะที่ยังไม่ยืนยัน)
    --since <เวลา>     เอาเฉพาะ Ticket ที่เกิดตั้งแต่เวลานี้ เช่น --since 2026-08-20T16:00

🔴 แก้ 20 ส.ค. 2026 — เพิ่มคอลัมน์ "เหตุการณ์อื่นในหน้าต่าง 60 วินาที"
   ปัญหาที่เจอ: ใบงานเดิมแสดงทีละใบโดดๆ แต่ `correlation.py` ตัดสินจาก**บริบทข้ามใบ**
   (มี Site อื่นล่มพร้อมกันไหมภายใน 60 วิ) ผลคือคนถูกให้ตัดสินด้วยข้อมูล**น้อยกว่า**ที่ระบบมี
   แล้วเอามาเทียบกันว่าใครถูก ซึ่งไม่ยุติธรรมตั้งแต่ต้นและทำให้ตัวเลขตอบคำถามผิดข้อ
   -> ตอนนี้แสดงเหตุการณ์ร่วมหน้าต่างให้ด้วย คนจะได้ตัดสินด้วยข้อมูลชุดเดียวกับระบบ
   📌 ต้องประกาศในเล่มว่าใบงานรอบแรกไม่มีข้อมูลนี้ และรายงานผลทั้งก่อนและหลัง

⚠️ ทำไมต้องมี --since (เพิ่ม 20 ส.ค. 2026):
   งาน B ยิง fault 40 ครั้งเพื่อวัด "เวลา" ทิ้ง Ticket ไว้ในฐานข้อมูล 40 ใบ
   ใบพวกนั้นเป็น fault แบบเดียวซ้ำๆ ที่รู้คำตอบล่วงหน้า **ห้ามนับเป็นชุดเคสอ้างอิง**
   ถ้าไม่กรองออก ใบงานจะมี 60 แถวโดย 40 แถวเป็นของที่ไม่ควรอยู่ในเกณฑ์ข้อ 3
"""
import csv
import os
import sqlite3
import sys
from datetime import datetime

# ผูกกับตำแหน่งไฟล์นี้ ไม่ใช่ cwd — กันปัญหาเดียวกับ Notes #1
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ขึ้นไป 1 ชั้นเพราะไฟล์นี้อยู่ใน tools/
DB_PATH = os.path.join(PROJECT_ROOT, 'tickets.db')
CSV_PATH = os.path.join(PROJECT_ROOT, 'docs', 'review_worksheet.csv')

GROUPS = ['Network-Core', 'Network-Access', 'Transmission', 'NOC-Monitoring']
LINE = '=' * 74


def is_fixture(row):
    """Ticket จาก self-test ของ lib/db.py ที่เคยเขียนทับ tickets.db (Notes #1)

    syslog จริงจาก IOS ขึ้นต้นด้วย priority ในวงเล็บแหลม เช่น <189>98: Core: ...
    ส่วน fixture เป็นข้อความ %LINK-3-UPDOWN เปล่าๆ ไม่มีส่วนหัว
    """
    raw = row['raw_syslog'] or ''
    return not raw.startswith('<')


def parse_event_time(value):
    """event_start_time เก็บเป็นรูปแบบ Cisco 'Aug 20 2026 16:01:44.338' ไม่ใช่ ISO

    (บทเรียนเดียวกับ measure_latency.py — fromisoformat() ตรงๆ พังทุกแถว)
    """
    if not value:
        return None
    try:
        return datetime.strptime(' '.join(value.split()), '%b %d %Y %H:%M:%S.%f')
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


CORRELATION_WINDOW_SECONDS = 60   # ต้องตรงกับ lib/correlation.py WINDOW_SECONDS


def attach_context(rows, all_rows):
    """หา "เหตุการณ์ที่ยังล่มค้างอยู่พร้อมกัน" ให้แต่ละใบ

    นี่คือข้อมูลชุดเดียวกับที่ correlation.py ใช้ตัดสิน — ถ้าไม่แสดงให้คนเห็น
    คนจะตัดสินด้วยข้อมูลน้อยกว่าระบบ แล้วการเทียบกันจะไม่มีความหมาย

    ⚠️ ต้องนับเฉพาะใบที่ "ยังเปิดอยู่" ตอนที่ใบนี้เกิด ไม่ใช่ทุกใบที่อยู่ในช่วง ±60 วิ
    เพราะ correlation.py ทำงานกับ db.list_open_site_tickets() เท่านั้น
    ใบที่ซ่อมไปแล้วก่อนหน้านี้ไม่ถูกนับ — ถ้าเอามาแสดงจะทำให้คนเข้าใจผิดไปอีกทาง
    (เจอจริงตอนทดสอบ 20 ส.ค.: เคสที่ซ่อมไปแล้ว 34 วิก่อนหน้า โผล่มาในบริบท)
    """
    dated = []
    for r in all_rows:
        t0 = parse_event_time(r['event_start_time'])
        if t0 is None:
            continue
        t1 = parse_event_time(r['event_end_time']) if r.get('event_end_time') else None
        dated.append((t0, t1, r))

    for r in rows:
        t0 = parse_event_time(r['event_start_time'])
        if t0 is None:
            r['_context'] = []
            continue
        near = []
        for o0, o1, other in dated:
            if other['incident_number'] == r['incident_number']:
                continue
            delta = (o0 - t0).total_seconds()
            if abs(delta) > CORRELATION_WINDOW_SECONDS:
                continue
            # ใบอื่นต้อง "ยังล่มอยู่" ตอนที่ใบนี้เกิด หรือมาล่มทีหลังขณะใบนี้ยังไม่จบ
            if o0 <= t0:
                still_down = (o1 is None) or (o1 >= t0)
            else:
                my_end = parse_event_time(r['event_end_time']) if r.get('event_end_time') else None
                still_down = (my_end is None) or (o0 <= my_end)
            if still_down:
                near.append((delta, other))
        near.sort(key=lambda x: x[0])
        r['_context'] = [f"{o['node']} {o['interface']} ({d:+.2f} วิ)" for d, o in near]
    return rows


def load(include_confirmed=False, since=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY incident_number")]
    conn.close()

    out, skipped_manual, skipped_fixture, skipped_old = [], 0, 0, 0
    for r in rows:
        if not r['raw_syslog']:
            skipped_manual += 1          # แจ้งด้วยตนเอง — ระบบไม่ได้จำแนก ไม่นับในเกณฑ์ข้อ 3
            continue
        if is_fixture(r):
            skipped_fixture += 1         # ข้อมูลทดสอบ — ห้ามนับ
            continue
        if r['confirmed_by'] and not include_confirmed:
            continue
        if since is not None:
            t = parse_event_time(r['event_start_time'])
            if t is None or t < since:
                skipped_old += 1         # อยู่นอกช่วงชุดเคสที่กำลังตรวจ
                continue
        out.append(r)
    return out, skipped_manual, skipped_fixture, skipped_old, rows


def cmd_blind(rows):
    """โหมดปิดคำตอบ — แสดงเฉพาะข้อมูลดิบ ไม่บอกว่าระบบจำแนกเป็นอะไร"""
    print()
    print(LINE)
    print("  ใบงานยืนยันการจำแนก — โหมดปิดคำตอบ")
    print(LINE)
    print("  อ่าน syslog แต่ละใบแล้วตัดสินเองว่าควรเป็นกลุ่มไหน")
    print(f"  กลุ่มที่เลือกได้: {' / '.join(GROUPS)}")
    print()

    print("  ⚠️ ดู 'เหตุการณ์ร่วมหน้าต่าง' ประกอบด้วย — Site อื่นที่ล่มภายใน 60 วินาทีของใบนี้")
    print("     ถ้ามีหลาย Site ล่มไล่เลี่ยกัน ให้คิดว่าต้นเหตุน่าจะอยู่ที่ไหน")
    print()

    for i, r in enumerate(rows, 1):
        print(f"  [{i}/{len(rows)}] {r['incident_number']}")
        print(f"        node/interface : {r['node']} {r['interface']}")
        print(f"        เวลาเกิดเหตุ    : {r['event_start_time']}")
        print(f"        syslog         : {(r['raw_syslog'] or '')[:110]}")
        ctx = r.get('_context') or []
        if ctx:
            print(f"        ⚡ เหตุการณ์ร่วมหน้าต่าง 60 วิ ({len(ctx)} ใบ):")
            for c in ctx:
                print(f"             - {c}")
        else:
            print(f"        เหตุการณ์ร่วมหน้าต่าง 60 วิ : ไม่มี (เกิดใบเดียวโดดๆ)")
        print()

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['incident_number', 'node', 'interface', 'event_start_time',
                    'other_events_within_60s', 'raw_syslog', 'my_group', 'my_reason'])
        for r in rows:
            ctx = ' | '.join(r.get('_context') or []) or '(ไม่มี)'
            w.writerow([r['incident_number'], r['node'], r['interface'],
                        r['event_start_time'], ctx, r['raw_syslog'], '', ''])

    print(LINE)
    print(f"  เขียนใบงานแล้ว: {CSV_PATH}")
    print("  กรอกคอลัมน์ my_group ให้ครบ แล้วรัน:  py review_worksheet.py --reveal")
    print(LINE)
    print()


def read_worksheet_csv():
    """อ่านใบงานกลับมา โดยเผื่อว่า Excel เซฟด้วย encoding อื่น

    ⚠️ เจอจริง 20 ส.ค. 2026: Excel บน Windows ภาษาไทยอาจเซฟ CSV เป็น ANSI (cp874)
    ไม่ใช่ UTF-8 ถ้ามีภาษาไทยในช่อง my_reason แล้วอ่านด้วย utf-8 อย่างเดียวจะ error
    ทั้งที่ไฟล์ถูกต้องทุกอย่าง — จึงลองหลาย encoding ตามลำดับ
    """
    if not os.path.exists(CSV_PATH):
        print(f"\n  ❌ ยังไม่มีใบงาน {CSV_PATH}")
        print("     รัน py review_worksheet.py ก่อน แล้วกรอก my_group ให้ครบ\n")
        return None

    raw = open(CSV_PATH, 'rb').read()
    for enc in ('utf-8-sig', 'cp874', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        rows = list(csv.DictReader(text.splitlines()))
        if rows and 'my_group' in rows[0]:
            if enc != 'utf-8-sig':
                print(f"  ℹ️  อ่านไฟล์ด้วย encoding {enc} (Excel เซฟมาแบบนี้)")
            return rows
    print(f"\n  ❌ อ่าน {CSV_PATH} ไม่ออก หรือไม่มีคอลัมน์ my_group")
    print("     ถ้าเปิดด้วย Excel ให้กด Ctrl+S แล้วเลือก Yes เพื่อคงรูปแบบ CSV\n")
    return None


def cmd_reveal(rows):
    """โหมดเฉลย — เทียบกลุ่มที่กรอกไว้กับที่ระบบจำแนก"""
    rows_csv = read_worksheet_csv()
    if rows_csv is None:
        return
    answers = {r['incident_number']: (r.get('my_group') or '').strip() for r in rows_csv}

    by_id = {r['incident_number']: r for r in rows}
    filled = [(k, v) for k, v in answers.items() if v and k in by_id]

    print()
    print(LINE)
    print("  เฉลย — เทียบที่คุณตัดสิน กับที่ระบบจำแนก")
    print(LINE)
    print(f"  {'incident':22}{'คุณตัดสิน':>18}{'ระบบจำแนก':>18}  conf  ผล")
    print("  " + "-" * 70)

    same = 0
    for inc, mine in filled:
        r = by_id[inc]
        sys_group = r['owner_group']
        ok = (mine == sys_group)
        same += ok
        print(f"  {inc:22}{mine:>18}{str(sys_group):>18}  {str(r['confidence']):>4}  {'ตรงกัน' if ok else '⚠ ต่างกัน'}")

    print()
    if filled:
        print(f"  ตรงกัน {same}/{len(filled)} ใบ  ({round(100 * same / len(filled), 1)}%)")
    else:
        print("  ⚠️ ยังไม่ได้กรอก my_group เลยสักใบ")
        print()
        print("     ถ้าคุณกรอกไปแล้วแต่ขึ้นข้อความนี้ = **ไฟล์ยังไม่ถูกเซฟ**")
        print("     กด Ctrl+S ใน Excel แล้วเลือก Yes เพื่อคงรูปแบบ CSV (ถ้ากด No จะกลายเป็น .xlsx)")
        print("     หรือเปิดแก้ด้วย Notepad แทน จะเชื่อถือได้กว่า")

    print()
    print("  ต่อไปให้ไปกดใน Dashboard ทีละใบ:")
    print("    • ใบที่ตรงกัน  -> เลือกกลุ่มเดิมซ้ำ  = ยืนยันว่าระบบจำแนกถูก (corrected_group ว่าง)")
    print("    • ใบที่ต่างกัน -> เลือกกลุ่มที่คุณตัดสิน = ระบบจำแนกผิด (corrected_group ถูกบันทึก)")
    print("  ⚠️ ตัวเลขข้างบนยังไม่ใช่ผลของเกณฑ์ข้อ 3 จนกว่าจะกดใน Dashboard จริง")
    print("     ตรวจตัวเลขจริงด้วย  py check_accuracy.py")
    print(LINE)
    print()


def main():
    if not os.path.exists(DB_PATH):
        print(f"\n  ❌ ไม่พบ {DB_PATH}\n")
        return

    since = None
    if '--since' in sys.argv:
        raw = sys.argv[sys.argv.index('--since') + 1]
        try:
            since = datetime.fromisoformat(raw)
        except ValueError:
            print(f"\n  ❌ อ่านเวลา '{raw}' ไม่ออก — ใช้รูปแบบ 2026-08-20T16:00\n")
            return

    rows, skipped_manual, skipped_fixture, skipped_old, all_rows = load('--all' in sys.argv, since)
    rows = attach_context(rows, all_rows)

    print()
    print(f"  ฐานข้อมูล : {DB_PATH}")
    print(f"  กันออก    : แจ้งด้วยตนเอง {skipped_manual} ใบ · ข้อมูลทดสอบ {skipped_fixture} ใบ", end='')
    if since:
        print(f" · นอกช่วงที่เลือก {skipped_old} ใบ")
        print(f"  ช่วงที่ตรวจ: ตั้งแต่ {since}")
    else:
        print()
        print("  ⚠️ ไม่ได้ระบุ --since — ใบงานจะรวม Ticket จากงาน B (วัดเวลา) ที่ไม่ควรนับด้วย")
    print(f"  ต้องยืนยัน : {len(rows)} ใบ")

    if not rows:
        print("\n  ✅ ไม่มีใบที่ค้างการยืนยัน\n")
        return

    if '--reveal' in sys.argv:
        cmd_reveal(rows)
    else:
        cmd_blind(rows)


if __name__ == '__main__':
    main()
