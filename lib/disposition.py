r"""
Phase 6 - ตัดสิน "ประเภทงาน" (disposition) จากหลักฐาน — กฎล้วนๆ ไม่แตะเครือข่าย

ทำไมต้องมีไฟล์นี้ ทั้งที่มี classifier.py อยู่แล้ว:
    classifier.py ตอบคำถามว่า "ปัญหาอยู่ในพื้นที่ของทีมไหน" (owner_group)
    ไฟล์นี้ตอบคนละคำถาม คือ "เป็นงานชนิดไหน ต้องทำอะไรต่อ" (disposition)

    Network-Access บอกแค่ว่าเป็นเรื่องฝั่ง Access แต่ไม่ได้บอกว่าต้องส่ง L2 ไปแก้ config
    หรือส่งช่างไปเปลี่ยนสาย ซึ่งเป็นคนละคนและคนละกระบวนการ
    สองแกนนี้แยกจากกัน ต้องไม่เอามารวมเป็นค่าเดียว

4 ประเภท และปลายทาง:
    configuration   ส่งต่อ L2       -> ร่าง PW ให้ L2 ตรวจและยื่นต่อ
    physical        ส่งช่างหน้างาน   -> ออกใบ dispatch
    customer        แจ้งลูกค้า       -> ออกข้อความแจ้งลูกค้า
    nofault         ปิดงาน          -> เสนอปิดพร้อมแนบ log

หลักการตัดสิน (สำคัญ — เป็นข้อจำกัดที่ตั้งใจใส่):
    1. ตัดสินจากหลักฐานที่ "มี" เท่านั้น ไม่ตัดสินด้วยการไล่ตัดตัวเลือกจนเหลืออันเดียว
    2. หลักฐานไม่ครบ (มีค่า None) -> ตอบ 'undetermined' ห้ามเดา
    3. คืน ruled_out ทุกครั้ง เพื่อให้ agent ตรวจสอบเหตุผลได้ ไม่ใช่เชื่อคำตอบเปล่าๆ
    4. ผลลัพธ์นี้เป็นเพียงข้อเสนอ — คนต้องยืนยันก่อนเสมอ ระบบไม่ลงมือเอง

ตารางตัดสิน (ยืนยันด้วยการทดลองจริงบนแล็บ 17 ก.ย. 2026 ผ่าน 4/4):
    admin_down = True                                   -> configuration
    admin_down = False, peer ไม่ถึง                      -> physical
    admin_down = False, peer ถึง, customer ไม่ถึง        -> customer
    admin_down = False, ทุกอย่างถึง, มี log ในช่วงเวลา    -> nofault
    นอกเหนือจากนี้                                       -> undetermined

ทดสอบได้เองไม่ต้องมี lab: py lib\disposition.py
"""
import re
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# ปลายทางของแต่ละประเภท
# ---------------------------------------------------------------------------
ROUTES = {
    'configuration': {
        'label': 'Configuration',
        'route': 'ส่งต่อ L2 — ระบบร่าง PW ให้ L2 ตรวจและยื่นต่อ',
        'owner_hint': 'L2',
        'action': 'draft_pw',
    },
    'physical': {
        'label': 'Physical',
        'route': 'ส่งช่างหน้างาน — ออกใบ dispatch',
        'owner_hint': 'Field Technician',
        'action': 'dispatch',
    },
    'customer': {
        'label': 'Customer-side',
        'route': 'แจ้งลูกค้า — ปัญหาอยู่เลย demarcation point',
        'owner_hint': 'Customer',
        'action': 'notify_customer',
    },
    'nofault': {
        'label': 'No-fault',
        'route': 'เสนอปิดงาน พร้อมแนบ log เป็นหลักฐาน',
        'owner_hint': 'L1',
        'action': 'propose_close',
    },
    'undetermined': {
        'label': 'ระบุไม่ได้',
        'route': 'ให้ agent ตรวจสอบเพิ่มเติมและตัดสินเอง',
        'owner_hint': None,
        'action': 'ask_agent',
    },
}

# ---------------------------------------------------------------------------
# กรอง log ตามช่วงเวลา
#
# ⚠️ show logging เก็บสะสมไม่หายเอง ถ้าไม่กรองตามเวลา ร่องรอยจาก TT ใบก่อนจะค้างอยู่
#    แล้วทำให้ TT ใบใหม่ถูกตัดสินเป็น nofault ทั้งที่ยังผิดปกติอยู่
#    (เจอจริงตอนทดสอบ 17 ก.ย. 2026 — ตอนนั้นแก้ชั่วคราวด้วย clear logging
#     ซึ่งทำในระบบจริงไม่ได้ จึงต้องกรองด้วย timestamp แทน)
# ---------------------------------------------------------------------------
MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}

# ตัวอย่าง: "Sep 17 2026 14:16:40.327: %LINK-5-CHANGED: Interface Ethernet0/0, ..."
_LOG_TS_RE = re.compile(
    r'^\*?(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<year>\d{4})\s+'
    r'(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})'
)

_INTERESTING = ('%LINK-', '%LINEPROTO-')


def parse_log_time(line, default_year=None):
    """อ่าน timestamp จากบรรทัด log — คืน datetime หรือ None ถ้าอ่านไม่ออก"""
    match = _LOG_TS_RE.match(line.strip())
    if not match:
        return None
    try:
        return datetime(
            int(match['year']), MONTHS[match['mon']], int(match['day']),
            int(match['h']), int(match['m']), int(match['s']),
        )
    except (KeyError, ValueError):
        return None


def recent_log_lines(raw_logging, since, interface=None, window_minutes=None):
    """
    คัดเฉพาะบรรทัด log ที่เกี่ยวกับ interface นี้ และเกิด "ไม่ก่อน" เวลา since

    since          : เวลาที่ TT เปิด (datetime) — บรรทัดที่เก่ากว่านี้เป็นของเหตุการณ์อื่น
    window_minutes : ถ้าใส่ จะตัดบรรทัดที่ใหม่กว่า since เกินกรอบนี้ออกด้วย
    บรรทัดที่อ่าน timestamp ไม่ออก -> ทิ้ง (ปลอดภัยกว่าเดาว่าอยู่ในช่วง)
    """
    if not raw_logging:
        return []

    upper = since + timedelta(minutes=window_minutes) if window_minutes else None
    out = []
    for line in raw_logging.splitlines():
        stripped = line.strip()
        if not any(tag in stripped for tag in _INTERESTING):
            continue
        if interface and interface not in stripped:
            continue
        when = parse_log_time(stripped)
        if when is None or when < since:
            continue
        if upper and when > upper:
            continue
        out.append(stripped)
    return out


# ---------------------------------------------------------------------------
# ตัวตัดสิน
# ---------------------------------------------------------------------------
def _missing(facts):
    """คืนรายชื่อหลักฐานที่ยังไม่มี — ใช้อธิบายว่าทำไมถึงตอบ undetermined"""
    gaps = []
    if facts.get('admin_down') is None:
        gaps.append('อ่านสถานะ admin ของพอร์ตไม่ได้')
    if not facts.get('admin_down'):
        if facts.get('peer_reachable') is None:
            gaps.append('ยังไม่ได้ทดสอบ ping ไปปลายอีกด้านของลิงก์')
        elif facts.get('peer_reachable') and facts.get('customer_reachable') is None:
            gaps.append('ยังไม่ได้ทดสอบ ping ไป LAN ฝั่งลูกค้า')
    return gaps


def decide(facts):
    """
    ตัดสินประเภทงานจากหลักฐาน

    facts (ทุกค่าที่เป็น None แปลว่า "ยังไม่รู้" ไม่ใช่ "ไม่ผ่าน"):
        admin_down          bool  พอร์ตฝั่ง Core อยู่ในสถานะ administratively down ไหม
        peer_reachable      bool  ping ไป IP ปลายอีกด้านของลิงก์ /30 ผ่านไหม
        customer_reachable  bool  ping ไป LAN ฝั่งลูกค้าผ่านไหม
        recent_log_lines    list  บรรทัด log ที่เกิดหลังเวลาที่ TT เปิด (กรองมาแล้ว)
        interface           str   ชื่อ interface — ใช้ประกอบข้อความเท่านั้น

    คืน dict:
        disposition, label, route, action, confidence ('high'|'low'),
        reasons (list), ruled_out (list), source ('rule')
    """
    itf = facts.get('interface') or 'พอร์ตที่เกี่ยวข้อง'
    gaps = _missing(facts)
    if gaps:
        return _result('undetermined', 'low',
                       reasons=gaps,
                       ruled_out=['ยังไม่ตัดอะไรออก — หลักฐานไม่พอจะสรุป'])

    if facts['admin_down']:
        return _result(
            'configuration', 'high',
            reasons=[
                f'{itf} รายงานตัวเองว่า administratively down',
                'สถานะนี้เกิดจากคำสั่ง shutdown เท่านั้น ความเสียหายทางกายภาพทำให้เกิดไม่ได้',
            ],
            ruled_out=[
                'physical — ถ้าเป็นความเสียหายทางกายภาพ พอร์ตจะเป็น up/down '
                'ไม่ใช่ administratively down',
                'customer — จุดที่ผิดปกติอยู่ที่พอร์ตของเราเอง ยังไม่ถึง demarcation point',
                'nofault — ยังผิดปกติอยู่ ณ เวลาที่ตรวจสอบ',
            ])

    if not facts['peer_reachable']:
        return _result(
            'physical', 'high',
            reasons=[
                f'{itf} ฝั่งเราสถานะปกติ ไม่ได้ถูกสั่งปิด',
                'แต่ติดต่อปลายอีกด้านของลิงก์เดียวกันไม่ได้',
                'จุดที่ขาดอยู่ระหว่างทาง ไม่ใช่ที่การตั้งค่าของเรา',
            ],
            ruled_out=[
                f'configuration — {itf} ไม่ได้อยู่ในสถานะ administratively down',
                'customer — ลิงก์ของเราเองยังติดต่อไม่ได้ ปัญหาอยู่ก่อนถึง demarcation point',
                'nofault — ยังผิดปกติอยู่ ณ เวลาที่ตรวจสอบ',
            ])

    if not facts['customer_reachable']:
        return _result(
            'customer', 'high',
            reasons=[
                f'{itf} ปกติ และติดต่อปลายอีกด้านของลิงก์ได้ = ลิงก์ของเราสมบูรณ์',
                'แต่ติดต่อ LAN ฝั่งลูกค้าไม่ได้',
                'จุดที่เสียอยู่เลย demarcation point ออกไป นอกความรับผิดชอบของเรา',
            ],
            ruled_out=[
                f'configuration — {itf} สถานะปกติ',
                'physical — ลิงก์ของเรายังติดต่อถึงกันได้ตามปกติ',
                'nofault — ยังมีจุดที่ใช้งานไม่ได้อยู่',
            ])

    logs = facts.get('recent_log_lines') or []
    if logs:
        return _result(
            'nofault', 'high',
            reasons=[
                'ทุกจุดที่ตรวจสอบกลับมาปกติแล้ว ณ เวลาที่ตรวจ',
                f'แต่พบร่องรอยความผิดปกติในช่วงเวลาของ TT ใบนี้ {len(logs)} บรรทัด',
                'เข้าข่ายเหตุที่หายเอง ต้องแนบ log เป็นหลักฐานก่อนปิดงาน',
            ],
            ruled_out=[
                'configuration / physical / customer — '
                'ไม่พบจุดใดผิดปกติอยู่ ณ เวลาที่ตรวจสอบ',
            ],
            evidence=logs)

    return _result(
        'undetermined', 'low',
        reasons=[
            'ทุกจุดที่ตรวจสอบปกติ',
            'และไม่พบร่องรอยความผิดปกติในช่วงเวลาของ TT ใบนี้',
            'หลักฐานไม่พอจะสรุปเป็นประเภทใดประเภทหนึ่ง',
        ],
        ruled_out=['ยังไม่ตัดอะไรออก — ต้องให้คนตรวจสอบเพิ่ม'])


def _result(key, confidence, reasons, ruled_out, evidence=None):
    meta = ROUTES[key]
    return {
        'disposition': key,
        'label': meta['label'],
        'route': meta['route'],
        'action': meta['action'],
        'owner_hint': meta['owner_hint'],
        'confidence': confidence,
        'reasons': reasons,
        'ruled_out': ruled_out,
        'evidence_lines': '\n'.join(evidence) if evidence else None,
        'source': 'rule',
    }


# ---------------------------------------------------------------------------
# ทดสอบด้วยหลักฐานจำลอง — ไม่ต้องมี lab -- รัน: py lib\disposition.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    base = {'interface': 'Ethernet0/0'}

    r = decide({**base, 'admin_down': True})
    check(r['disposition'] == 'configuration', 'admin down -> configuration')
    check(r['action'] == 'draft_pw', 'configuration -> ร่าง PW')
    check(len(r['ruled_out']) == 3, 'configuration ตัดอีก 3 ประเภทออกพร้อมเหตุผล')

    r = decide({**base, 'admin_down': False, 'peer_reachable': False,
                'customer_reachable': False})
    check(r['disposition'] == 'physical', 'ping ปลายสายไม่ผ่าน -> physical')
    check(r['action'] == 'dispatch', 'physical -> ส่งช่าง')

    r = decide({**base, 'admin_down': False, 'peer_reachable': True,
                'customer_reachable': False})
    check(r['disposition'] == 'customer', 'ลิงก์ดีแต่ LAN ลูกค้าไม่ตอบ -> customer')

    r = decide({**base, 'admin_down': False, 'peer_reachable': True,
                'customer_reachable': True,
                'recent_log_lines': ['Sep 17 2026 14:16:40.327: %LINK-5-CHANGED: ...']})
    check(r['disposition'] == 'nofault', 'ปกติหมดแต่มี log ในช่วงเวลา -> nofault')

    r = decide({**base, 'admin_down': False, 'peer_reachable': True,
                'customer_reachable': True, 'recent_log_lines': []})
    check(r['disposition'] == 'undetermined', 'ปกติหมดและไม่มี log -> undetermined')
    check(r['confidence'] == 'low', 'undetermined ต้องมี confidence ต่ำ')

    # หลักฐานไม่ครบ ห้ามเดา
    r = decide({**base, 'admin_down': False, 'peer_reachable': None})
    check(r['disposition'] == 'undetermined', 'ยังไม่ได้ ping -> undetermined ไม่ใช่เดา')
    r = decide({**base, 'admin_down': False, 'peer_reachable': True,
                'customer_reachable': None})
    check(r['disposition'] == 'undetermined', 'ยังไม่ได้ ping LAN -> undetermined')
    r = decide({**base})
    check(r['disposition'] == 'undetermined', 'ไม่มีหลักฐานเลย -> undetermined')

    # ทุกผลต้องมีเหตุผลและรายการที่ตัดออกเสมอ
    for facts in [{**base, 'admin_down': True},
                  {**base, 'admin_down': False, 'peer_reachable': False,
                   'customer_reachable': False},
                  {**base}]:
        res = decide(facts)
        check(res['reasons'] and res['ruled_out'],
              f"{res['disposition']} มีทั้ง reasons และ ruled_out")

    # --- กรอง log ตามเวลา ---
    LOG = (
        "Sep 17 2026 10:00:00.100: %LINK-5-CHANGED: Interface Ethernet0/0, "
        "changed state to administratively down\n"
        "Sep 17 2026 14:16:40.327: %LINK-5-CHANGED: Interface Ethernet0/0, "
        "changed state to administratively down\n"
        "Sep 17 2026 14:16:41.328: %LINEPROTO-5-UPDOWN: Line protocol on "
        "Interface Ethernet0/0, changed state to down\n"
        "Sep 17 2026 14:17:00.000: %SYS-5-CONFIG_I: Configured from console\n"
        "Sep 17 2026 14:18:00.000: %LINK-5-CHANGED: Interface Ethernet0/1, "
        "changed state to administratively down\n"
    )
    since = datetime(2026, 9, 17, 14, 15, 0)

    lines = recent_log_lines(LOG, since, interface='Ethernet0/0')
    check(len(lines) == 2, f'กรอง log ตามเวลา+interface ได้ 2 บรรทัด (ได้ {len(lines)})')
    check(all('10:00:00' not in l for l in lines), 'บรรทัดเก่าก่อนเวลา TT ถูกตัดออก')
    check(all('Ethernet0/1' not in l for l in lines), 'บรรทัดของ interface อื่นถูกตัดออก')
    check(all('CONFIG_I' not in l for l in lines), 'บรรทัดที่ไม่ใช่ LINK/LINEPROTO ถูกตัดออก')

    check(recent_log_lines(LOG, since, interface='Ethernet0/0',
                           window_minutes=1) == [],
          'window_minutes ตัดบรรทัดที่ใหม่เกินกรอบออก')
    check(recent_log_lines('', since) == [], 'log ว่าง -> คืนรายการว่าง ไม่พัง')
    check(parse_log_time('ไม่ใช่ log') is None, 'บรรทัดที่อ่านเวลาไม่ออก -> None')

    print()
    print(f"ผ่าน {passed}/{total}")
    raise SystemExit(0 if passed == total else 1)
