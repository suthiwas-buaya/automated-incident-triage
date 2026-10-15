"""
Correlation — รวมหลาย event ที่เกี่ยวข้องกันให้เป็นเหตุเดียว

แนวคิด: ถ้า Access Site หลายแห่งหลุดพร้อมกันในเวลาไล่เลี่ยกัน
ปัญหาไม่น่าจะอยู่ที่สายแต่ละเส้น (บังเอิญขาดพร้อมกันมีโอกาสน้อยมาก)
แต่น่าจะอยู่ที่ **ตัวอุปกรณ์แกนกลาง** ที่เป็นจุดร่วมของทุกเส้น

ระบบจึงต้องยกระดับความสำคัญและเปลี่ยนเจ้าภาพ แทนที่จะปล่อยให้ทีม Access
ไล่ตรวจสายทีละเส้นโดยไม่รู้ว่าต้นเหตุอยู่ที่เดียวกัน

ทดสอบได้เอง:
    py lib\correlation.py
"""
import re
from datetime import datetime

try:
    from lib import db
except ImportError:
    import db

# --- เกณฑ์ตัดสิน ---
MIN_SITES = 2            # กี่ Site ขึ้นไปถึงจะถือว่าเป็นปัญหาที่ Core
WINDOW_SECONDS = 60      # ต้องเกิดห่างกันไม่เกินกี่วินาที

CORRELATED_GROUP = 'Network-Core'
CORRELATED_PRIORITY = 'P1'
CORRELATED_CONFIDENCE = 90

TIMESTAMP_FORMAT = '%b %d %Y %H:%M:%S.%f'


def parse_event_time(text):
    """
    แปลง timestamp จาก syslog เป็น datetime
    รูปแบบ: 'Jul 30 2026 11:10:17.076'  (วันที่เลขหลักเดียวจะมีเว้นวรรค 2 ช่อง)
    คืน None ถ้าแปลงไม่ได้
    """
    if not text:
        return None
    cleaned = re.sub(r'\s+', ' ', text.strip())
    try:
        return datetime.strptime(cleaned, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def find_correlated_group(tickets):
    """
    หา TT ที่เกิดใกล้กันพอจะถือว่าเป็นเหตุเดียวกัน
    คืน list ของ TT ที่เข้าข่าย (ต้องมาจาก Site ต่างกันอย่างน้อย MIN_SITES แห่ง)
    """
    dated = []
    for t in tickets:
        ts = parse_event_time(t.get('event_start_time'))
        if ts:
            dated.append((ts, t))

    if len(dated) < MIN_SITES:
        return []

    dated.sort(key=lambda x: x[0])
    newest_time = dated[-1][0]

    # เอาเฉพาะใบที่เกิดภายใน WINDOW_SECONDS ก่อนใบล่าสุด
    window = [t for ts, t in dated
              if (newest_time - ts).total_seconds() <= WINDOW_SECONDS]

    distinct_sites = {t['node'] for t in window}
    if len(distinct_sites) >= MIN_SITES:
        return window
    return []


def check_and_correlate():
    """
    ตรวจ TT ที่เปิดอยู่ทั้งหมด ถ้าเข้าเกณฑ์ correlation ให้จำแนกใหม่เป็นปัญหาที่ Core

    คืน dict:
        {'correlated': [incident_number, ...], 'sites': [ชื่อ Site, ...]}
    หรือ None ถ้าไม่เข้าเกณฑ์ / ไม่มีอะไรเปลี่ยน
    """
    open_tickets = db.list_open_site_tickets()
    group = find_correlated_group(open_tickets)

    if not group:
        return None

    sites = sorted({t['node'] for t in group})
    reason = (
        f"ตรวจพบ {len(sites)} Site หลุดพร้อมกันภายใน {WINDOW_SECONDS} วินาที "
        f"({', '.join(sites)}) → สรุปว่าต้นเหตุอยู่ที่อุปกรณ์แกนกลาง ไม่ใช่สายแต่ละเส้น "
        f"| ยกระดับเป็น {CORRELATED_PRIORITY} เพราะกระทบหลาย Site พร้อมกัน"
    )

    changed = []
    for t in group:
        # ข้ามใบที่จำแนกเป็น correlated ไปแล้ว จะได้ไม่รายงานซ้ำทุกครั้ง
        if t.get('owner_group') == CORRELATED_GROUP and t.get('priority') == CORRELATED_PRIORITY:
            continue
        db.update_classification(
            incident_number=t['incident_number'],
            owner_group=CORRELATED_GROUP,
            priority=CORRELATED_PRIORITY,
            confidence=CORRELATED_CONFIDENCE,
            reason=reason,
        )
        changed.append(t['incident_number'])

    if not changed:
        return None

    return {'correlated': changed, 'sites': sites, 'reason': reason}


# ---------------------------------------------------------------------------
# ทดสอบ logic ด้วยข้อมูลปลอม (ไม่แตะฐานข้อมูลจริง) -- รัน: py lib\correlation.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    def make(node, timestamp):
        return {'incident_number': f'INC-TEST-{node[-1]}', 'node': node,
                'event_start_time': timestamp, 'owner_group': 'Network-Access',
                'priority': 'P2'}

    print("=== ทดสอบ find_correlated_group() ===\n")
    passed = 0
    total = 0

    # เคส 1: 3 Site ล่มห่างกันไม่กี่วินาที -> ต้องเข้าเกณฑ์
    total += 1
    case1 = [
        make('Access-Site-A', 'Jul 30 2026 12:00:01.100'),
        make('Access-Site-B', 'Jul 30 2026 12:00:03.200'),
        make('Access-Site-C', 'Jul 30 2026 12:00:05.300'),
    ]
    r1 = find_correlated_group(case1)
    ok = len(r1) == 3
    passed += ok
    print(f"{'✓' if ok else '❌'} 3 Site ล่มห่างกัน 4 วิ -> เข้าเกณฑ์ ({len(r1)} ใบ)")

    # เคส 2: Site เดียวล่ม -> ไม่เข้าเกณฑ์
    total += 1
    case2 = [make('Access-Site-A', 'Jul 30 2026 12:00:01.100')]
    r2 = find_correlated_group(case2)
    ok = len(r2) == 0
    passed += ok
    print(f"{'✓' if ok else '❌'} Site เดียวล่ม -> ไม่เข้าเกณฑ์")

    # เคส 3: 2 Site ล่มแต่ห่างกัน 5 นาที -> ไม่เข้าเกณฑ์ (คนละเหตุ)
    total += 1
    case3 = [
        make('Access-Site-A', 'Jul 30 2026 12:00:00.000'),
        make('Access-Site-B', 'Jul 30 2026 12:05:00.000'),
    ]
    r3 = find_correlated_group(case3)
    ok = len(r3) == 0
    passed += ok
    print(f"{'✓' if ok else '❌'} 2 Site ล่มห่างกัน 5 นาที -> ไม่เข้าเกณฑ์ (ถือเป็นคนละเหตุ)")

    # เคส 4: 2 Site ล่มห่างกัน 10 วิ -> เข้าเกณฑ์
    total += 1
    case4 = [
        make('Access-Site-A', 'Jul 30 2026 12:00:00.000'),
        make('Access-Site-B', 'Jul 30 2026 12:00:10.000'),
    ]
    r4 = find_correlated_group(case4)
    ok = len(r4) == 2
    passed += ok
    print(f"{'✓' if ok else '❌'} 2 Site ล่มห่างกัน 10 วิ -> เข้าเกณฑ์")

    # เคส 5: Site เดิมล่มซ้ำ 2 ใบ (interface เดียวกัน) -> ไม่ควรนับเป็น 2 Site
    total += 1
    case5 = [
        make('Access-Site-A', 'Jul 30 2026 12:00:00.000'),
        make('Access-Site-A', 'Jul 30 2026 12:00:05.000'),
    ]
    r5 = find_correlated_group(case5)
    ok = len(r5) == 0
    passed += ok
    print(f"{'✓' if ok else '❌'} Site เดิม 2 ใบ -> ไม่เข้าเกณฑ์ (ต้องต่าง Site กันจริง)")

    # เคส 6: timestamp เสีย -> ต้องไม่ crash
    total += 1
    case6 = [
        make('Access-Site-A', 'ข้อความเสีย'),
        make('Access-Site-B', 'Jul 30 2026 12:00:00.000'),
    ]
    try:
        r6 = find_correlated_group(case6)
        ok = len(r6) == 0
        passed += ok
        print(f"{'✓' if ok else '❌'} timestamp เสีย -> ข้ามไปไม่ crash")
    except Exception as e:
        print(f"❌ crash: {e}")

    print(f"\nผลรวม: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case")
