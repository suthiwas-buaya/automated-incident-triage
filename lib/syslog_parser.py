"""
Step 4 - Parser แปลง syslog message ดิบ -> dict ที่ใช้สร้าง TT ได้

ทดสอบด้วยข้อความจริงที่เก็บได้จาก sniff_test.py (Step 2) ไม่ต้องต่อ network
วิธีรัน:
    py syslog_parser.py
"""
import re

# ---------------------------------------------------------------------------
# รูปแบบข้อความจริงที่เก็บได้จาก Step 2:
#   <189>100: Core: Jul 30 2026 10:37:56.666: %LINK-5-CHANGED: Interface Ethernet0/1, changed state to administratively down
#   ^pri ^seq  ^host  ^timestamp                ^facility-sev-mnemonic  ^message
# ---------------------------------------------------------------------------
SYSLOG_RE = re.compile(
    r'^<(?P<pri>\d+)>'                        # <189>
    r'(?P<seq>\d+):\s+'                       # 100:
    r'(?P<hostname>[\w\-\.]+):\s+'            # Core:  / Access-Site-A:
    r'(?P<timestamp>\w{3}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\.\d+):\s+'   # Jul 30 2026 10:37:56.666
    r'%(?P<facility>[\w\-]+?)-(?P<severity>\d)-(?P<mnemonic>[\w_]+):\s+'     # %LINK-5-CHANGED:
    r'(?P<message>.*)$'                       # Interface Ethernet0/1, changed state to ...
)

# ดึงชื่อ interface และ state ปลายทางจากส่วน message
INTERFACE_RE = re.compile(r'Interface\s+(?P<interface>[\w/\.]+)')
STATE_RE = re.compile(r'changed state to\s+(?P<state>administratively down|down|up)')

# Mapping: interface บน Core -> Site ที่ได้รับผลกระทบ (ตามตารางใน Plan.md 1.5)
CORE_INTERFACE_TO_SITE = {
    'Ethernet0/0': 'Access-Site-A',
    'Ethernet0/1': 'Access-Site-B',
    'Ethernet0/2': 'Access-Site-C',
}

# interface ที่ต้อง ignore — E0/3 คือทางออกไป host/NAT ไม่ใช่ fault ของ Site
IGNORED_INTERFACES = {'Ethernet0/3'}


def parse(raw_message):
    """
    แปลง syslog ดิบ -> dict
    คืน None ถ้ารูปแบบไม่ตรง (parse ไม่ได้)
    """
    m = SYSLOG_RE.match(raw_message.strip())
    if not m:
        return None

    result = {
        'hostname': m.group('hostname'),
        'timestamp': m.group('timestamp'),
        'facility': m.group('facility'),
        'severity': int(m.group('severity')),
        'mnemonic': m.group('mnemonic'),
        'message': m.group('message'),
        'interface': None,
        'state': None,
        'raw': raw_message.strip(),
    }

    if (im := INTERFACE_RE.search(result['message'])):
        result['interface'] = im.group('interface')

    if (sm := STATE_RE.search(result['message'])):
        state = sm.group('state')
        # รวม "administratively down" เข้ากับ down (ตาม trigger rule ที่ confirm ไว้)
        result['state'] = 'down' if 'down' in state else 'up'

    return result


def is_trigger_event(parsed):
    """
    ตัดสินว่า event นี้ควรสร้าง/ปิด TT ไหม ตามกฎที่ confirm ใน Plan.md 1.4:
      - รับ facility ตระกูล LINK เท่านั้น (ทั้ง CHANGED และ UPDOWN)
      - ต้องมี interface และ state
      - ignore Ethernet0/3 (ทางออกไป host)
      - ignore LINEPROTO / AMDP2_FE / SYS ทั้งหมด
    """
    if parsed is None:
        return False
    if parsed['facility'] != 'LINK':
        return False
    if not parsed['interface'] or not parsed['state']:
        return False
    if parsed['interface'] in IGNORED_INTERFACES:
        return False
    return True


def build_ticket_fields(parsed):
    """
    แปลง parsed event -> field ที่ db.create_ticket() / close_ticket() ต้องใช้
    node = Site ที่ได้รับผลกระทบจริง (ไม่ใช่ Core ที่เป็นแค่ผู้รายงาน)
    """
    reporter = parsed['hostname']
    interface = parsed['interface']
    label = 'Link down' if parsed['state'] == 'down' else 'Link restored'

    # ถ้า Core รายงาน -> แปลง interface เป็นชื่อ Site ที่หลุด
    if reporter == 'Core' and interface in CORE_INTERFACE_TO_SITE:
        affected_node = CORE_INTERFACE_TO_SITE[interface]
        summary = f"{label}: Core {interface} <-> {affected_node}"
    else:
        # กรณี Site รายงานเอง (เช่น interface ฝั่ง Site ล่มแต่ยังส่ง syslog ออกได้)
        affected_node = reporter
        summary = f"{label}: {reporter} {interface}"

    return {
        'node': affected_node,
        'interface': interface,
        'summary': summary,
        'event_start_time': parsed['timestamp'],
        'raw_syslog': parsed['raw'],
        'state': parsed['state'],
    }


# ---------------------------------------------------------------------------
# ทดสอบด้วยข้อความจริงที่เก็บจาก sniff_test.py -- รัน: py syslog_parser.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # ข้อความจริงทั้งหมดที่เก็บได้จาก Step 2
    SAMPLES = [
        # (ข้อความ, ควรเป็น trigger ไหม, คำอธิบาย)
        ("<189>100: Core: Jul 30 2026 10:37:56.666: %LINK-5-CHANGED: Interface Ethernet0/1, changed state to administratively down",
         True, "fault down จาก Core E0/1 -> ต้องเปิด TT ของ Site-B"),

        ("<187>104: Core: Jul 30 2026 10:39:08.026: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to up",
         True, "recovery up -> ต้องปิด TT ของ Site-B"),

        ("<189>101: Core: Jul 30 2026 10:37:57.666: %LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet0/1, changed state to down",
         False, "LINEPROTO ต้องถูก ignore (กัน TT ซ้ำ)"),

        ("<190>98: Core: Jul 30 2026 10:37:12.716: %AMDP2_FE-6-EXCESSCOLL: Ethernet0/3 TDR=0, TRC=0",
         False, "noise ของ IOL ต้องถูก ignore"),

        ("<189>99: Core: Jul 30 2026 10:37:54.958: %SYS-5-CONFIG_I: Configured from console by console",
         False, "config change ไม่ใช่ fault"),

        ("<190>55: Access-Site-A: Jul 30 2026 10:39:32.682: %SYS-6-TTY_EXPIRE_TIMER: (exec timer expired, tty 0 (0.0.0.0)), user",
         False, "TTY timer จาก Site ไม่ใช่ fault"),
    ]

    print("=== ทดสอบ parse() + is_trigger_event() ===\n")
    passed = 0
    for raw, should_trigger, note in SAMPLES:
        parsed = parse(raw)
        if parsed is None:
            print(f"❌ parse ไม่ได้เลย: {raw[:70]}...")
            continue

        actual = is_trigger_event(parsed)
        mark = "✓" if actual == should_trigger else "❌"
        if actual == should_trigger:
            passed += 1

        print(f"{mark} [{parsed['facility']}-{parsed['severity']}-{parsed['mnemonic']}] trigger={actual}")
        print(f"   {note}")
        if actual:
            fields = build_ticket_fields(parsed)
            print(f"   -> node={fields['node']}, state={fields['state']}")
            print(f"   -> summary={fields['summary']}")
            print(f"   -> event_time={fields['event_start_time']}")
        print()

    print(f"ผลรวม: {passed}/{len(SAMPLES)} test case ผ่าน")

    # ตรวจความถูกต้องของ mapping สำคัญ
    down = build_ticket_fields(parse(SAMPLES[0][0]))
    assert down['node'] == 'Access-Site-B', "E0/1 ต้อง map เป็น Access-Site-B"
    assert down['state'] == 'down', "administratively down ต้องนับเป็น down"

    up = build_ticket_fields(parse(SAMPLES[1][0]))
    assert up['node'] == 'Access-Site-B', "event up ต้อง map เป็น node เดียวกันเพื่อจับคู่ปิด TT ได้"
    assert up['state'] == 'up'

    print("\n✅ mapping down/up จับคู่เป็น node เดียวกันถูกต้อง -> ปิด TT ได้")
