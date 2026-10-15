r"""
Phase 2 Step 3 - Classifier: จำแนก TT -> Owner Group + Priority + Confidence

ทดสอบได้เองไม่ต้องมี lab:
    py lib\classifier.py
"""
try:
    from lib import mapping
except ImportError:          # กรณีรันไฟล์นี้ตรงๆ จากในโฟลเดอร์ lib
    import mapping

# ---------------------------------------------------------------------------
# ปรับ confidence ตามความครบถ้วนของข้อมูล
# ---------------------------------------------------------------------------
BONUS_KNOWN_SITE = 5        # รู้แน่ชัดว่า Site ไหนได้รับผลกระทบ
PENALTY_UNKNOWN_NODE = -20  # ไม่รู้ว่า node ไหนเลย
PENALTY_NO_INTERFACE = -15  # ไม่รู้ว่า interface ไหน
FALLBACK_CONFIDENCE = 30    # ไม่ match กฎใดเลย


def build_context(parsed, ticket_fields):
    """
    รวมข้อมูลจาก parser -> context ที่กฎใน mapping.py ใช้ตัดสิน
    parsed        = ผลจาก syslog_parser.parse()
    ticket_fields = ผลจาก syslog_parser.build_ticket_fields()
    """
    reporter = parsed.get('hostname') or ''
    node = ticket_fields.get('node') or ''
    interface = parsed.get('interface') or ''

    # fault ทำให้ Site หลุดทั้ง Site หรือไม่:
    # Core รายงาน + interface นั้น map เป็น Access-Site ได้ = Site นั้นขาดการเชื่อมต่อ
    is_site_isolating = (
        reporter == 'Core'
        and node.startswith('Access-Site')
        and node != reporter
    )

    return {
        'reporter': reporter,
        'node': node,
        'interface': interface,
        'facility': parsed.get('facility') or '',
        'mnemonic': parsed.get('mnemonic') or '',
        'severity': parsed.get('severity'),
        'message': (parsed.get('message') or '').lower(),
        'is_site_isolating': is_site_isolating,
    }


def _score(base_score, context):
    """ปรับคะแนนพื้นฐานตามความครบถ้วนของข้อมูลที่มี"""
    score = base_score

    if context['is_site_isolating']:
        score += BONUS_KNOWN_SITE
    if not context['node']:
        score += PENALTY_UNKNOWN_NODE
    if not context['interface']:
        score += PENALTY_NO_INTERFACE

    return max(0, min(100, score))


def decide_priority(context):
    """severity -> priority แล้วยกระดับถ้า impact สูง (impact สำคัญกว่า severity)"""
    severity = context['severity']
    priority = mapping.SEVERITY_TO_PRIORITY.get(severity, mapping.DEFAULT_PRIORITY)
    note = f"severity {severity} -> {priority}"

    if context['is_site_isolating']:
        escalated = mapping.escalate(priority, mapping.SITE_ISOLATING_MIN_PRIORITY)
        if escalated != priority:
            note += f" แล้วยกเป็น {escalated} เพราะ Site หลุดจากเครือข่ายทั้ง Site"
            priority = escalated

    return priority, note


def classify(parsed, ticket_fields):
    """
    จำแนก TT
    คืน dict: owner_group, priority, confidence, reason
    """
    context = build_context(parsed, ticket_fields)

    matched = None
    for rule in mapping.RULES:
        try:
            if rule['match'](context):
                matched = rule
                break
        except Exception:
            # กฎใดพังไม่ควรทำให้ทั้งระบบล้ม — ข้ามไปกฎถัดไป
            continue

    priority, priority_note = decide_priority(context)

    if matched is None:
        return {
            'owner_group': mapping.FALLBACK_GROUP,
            'priority': priority,
            'confidence': FALLBACK_CONFIDENCE,
            'reason': f"ไม่เข้ากฎใดเลย -> ส่งให้ {mapping.FALLBACK_GROUP} ตรวจสอบ | {priority_note}",
        }

    confidence = _score(matched['base_score'], context)

    return {
        'owner_group': matched['group'],
        'priority': priority,
        'confidence': confidence,
        'reason': f"{matched['why']} (กฎ: {matched['name']}) | {priority_note}",
    }


# ---------------------------------------------------------------------------
# ทดสอบด้วยข้อความจริง + เคสสมมติ -- รัน: py lib\classifier.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from lib import syslog_parser

    CASES = [
        # (ข้อความ syslog, group ที่คาด, priority ที่คาด, คำอธิบาย)
        ("<189>146: Core: Jul 30 2026 11:10:17.076: %LINK-5-CHANGED: Interface Ethernet0/1, changed state to administratively down",
         'Network-Access', 'P2',
         "admin down สาย Core<->Site-B: severity 5 (P3) ต้องถูกยกเป็น P2 เพราะ Site หลุด"),

        ("<187>104: Core: Jul 30 2026 10:39:08.026: %LINK-3-UPDOWN: Interface Ethernet0/0, changed state to down",
         'Network-Access', 'P2',
         "สายล่มจริง severity 3 -> P2 อยู่แล้ว"),

        ("<187>200: Core: Jul 30 2026 12:00:00.000: %LINK-3-UPDOWN: Interface Loopback0, changed state to down",
         'Network-Core', 'P2',
         "Loopback ของ Core ล่ม = ปัญหาอุปกรณ์แกนกลาง"),

        ("<187>201: Core: Jul 30 2026 12:05:00.000: %LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down, carrier loss detected",
         'Transmission', 'P2',
         "มี keyword carrier -> ปัญหาชั้นกายภาพ (กฎนี้มาก่อน link down)"),

        ("<185>202: Core: Jul 30 2026 12:10:00.000: %BGP-3-NOTIFICATION: neighbor 10.0.1.2 down, adjacency lost",
         'Network-Core', 'P2',
         "keyword neighbor/adjacency -> routing ระดับ core"),
    ]

    print("=== ทดสอบ classify() ===\n")
    passed = 0

    for raw, expect_group, expect_priority, note in CASES:
        parsed = syslog_parser.parse(raw)
        if parsed is None:
            print(f"❌ parse ไม่ได้: {raw[:60]}...")
            continue

        fields = syslog_parser.build_ticket_fields(parsed)
        result = classify(parsed, fields)

        ok_group = result['owner_group'] == expect_group
        ok_priority = result['priority'] == expect_priority
        mark = "✓" if (ok_group and ok_priority) else "❌"
        if ok_group and ok_priority:
            passed += 1

        print(f"{mark} {note}")
        print(f"   Group      : {result['owner_group']}"
              f"{'' if ok_group else f'  (คาด {expect_group})'}")
        print(f"   Priority   : {result['priority']}"
              f"{'' if ok_priority else f'  (คาด {expect_priority})'}")
        print(f"   Confidence : {result['confidence']}")
        print(f"   เหตุผล      : {result['reason']}")
        print()

    # เคส fallback: syslog ที่ไม่ใช่ LINK และไม่มี keyword ใดเลย
    parsed = syslog_parser.parse(
        "<190>203: Access-Site-A: Jul 30 2026 12:20:00.000: %SYS-6-TTY_EXPIRE_TIMER: (exec timer expired, tty 0), user"
    )
    fields = syslog_parser.build_ticket_fields(parsed)
    fb = classify(parsed, fields)
    ok_fb = fb['owner_group'] == mapping.FALLBACK_GROUP and fb['confidence'] == FALLBACK_CONFIDENCE
    print(f"{'✓' if ok_fb else '❌'} fallback: event ที่ไม่เข้ากฎใด -> {fb['owner_group']} (confidence {fb['confidence']})")
    if ok_fb:
        passed += 1

    total = len(CASES) + 1
    print(f"\nผลรวม: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case")
