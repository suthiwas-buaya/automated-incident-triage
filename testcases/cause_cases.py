r"""
ชุดเคสอ้างอิงสำหรับวัดผลการวินิจฉัยสาเหตุ (docs/Plan.md 3.5.3 ขั้นที่ 4)

ทำไมต้องมีไฟล์นี้: จะเปรียบเทียบ 3 แนวทาง (กฎล้วน / LLM ล้วน / Hybrid) ได้ ต้องป้อน
**ชุดเคสเดียวกัน** ที่ **รู้คำตอบล่วงหน้า** ให้ทั้งสามแนวทาง ไม่งั้นตัวเลขที่ได้เทียบกันไม่ได้

⚠️ ผล show command ในไฟล์นี้เขียนตามรูปแบบจริงของ Cisco IOS โดยอ้างอิงจากผลที่เก็บมาจาก
lab จริงใน Phase 3 — ต้องประกาศในเล่มว่าเป็นข้อมูลจำลองตามรูปแบบมาตรฐาน ไม่ใช่ผลจาก
เหตุการณ์จริงทั้งหมด (ประเด็นความซื่อตรงในการรายงาน ผูกกับ IEEE Code of Ethics ข้อ 5)

โครงสร้างแต่ละเคส:
    id            รหัสอ้างอิงในเล่ม
    note          อธิบายว่าเคสนี้ทดสอบอะไร
    node/interface/summary   บริบทของ TT
    raw_output    ผลจากอุปกรณ์ (ส่งให้ทั้ง 3 แนวทางเหมือนกัน)
    determinable  สรุปสาเหตุได้ไหมจากข้อมูลเท่าที่มี — **False แปลว่าคำตอบที่ถูกต้องคือ
                  "สรุปไม่ได้"** ไม่ใช่ว่าไม่มีคำตอบ (เคสพวกนี้สำคัญที่สุดในการวัดการตอบมั่ว)
    keywords      คำที่ต้องปรากฏในสาเหตุจึงจะนับว่าตอบถูก (อย่างน้อย 1 คำ)
    rule_should   คาดว่าชุดกฎจับได้ไหม — ใช้ตรวจว่าชุดเคสกระจายตัวดีพอ ไม่ใช่กฎจับได้หมด

ทดสอบความถูกต้องของชุดเคสเอง: py testcases\cause_cases.py
"""

CASES = [
    # ---------------- กลุ่มที่ 1: กฎควรจับได้ (4 เคส) ----------------
    {
        'id': 'C01',
        'note': 'พอร์ตถูกสั่งปิดจากการตั้งค่า — เคสพื้นฐานที่สุด',
        'node': 'Core', 'interface': 'Ethernet0/1',
        'summary': '%LINK-5-CHANGED: Interface Ethernet0/1, changed state to administratively down',
        'raw_output': (
            "$ show interface Ethernet0/1\n"
            "Ethernet0/1 is administratively down, line protocol is down\n"
            "  Hardware is AmdP2, address is aabb.cc00.0100 (bia aabb.cc00.0100)\n"
            "  MTU 1500 bytes, BW 10000 Kbit/sec, DLY 1000 usec\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
        ),
        'determinable': True, 'rule_should': True,
        'keywords': ['administratively', 'shut', 'disabled', 'configuration'],
    },
    {
        'id': 'C02',
        'note': 'สัญญาณหายที่ชั้นกายภาพ — line protocol down แต่ไม่ได้ถูกสั่งปิด',
        'node': 'Core', 'interface': 'Ethernet0/0',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/0, changed state to down',
        'raw_output': (
            "$ show interface Ethernet0/0\n"
            "Ethernet0/0 is down, line protocol is down (notconnect)\n"
            "  Hardware is AmdP2, address is aabb.cc00.0000\n"
            "  Last input never, output 00:00:05, output hang never\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
        ),
        'determinable': True, 'rule_should': True,
        'keywords': ['physical', 'signal', 'carrier', 'link', 'down', 'notconnect'],
    },
    {
        'id': 'C03',
        'note': 'สายหรือหัวแปลงสัญญาณมีปัญหา — ตัวนับ CRC สูง',
        'node': 'Core', 'interface': 'Ethernet0/2',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down',
        'raw_output': (
            "$ show interface Ethernet0/2\n"
            "Ethernet0/2 is up, line protocol is up\n"
            "  Hardware is AmdP2, address is aabb.cc00.0200\n"
            "     892 input errors, 892 CRC, 0 frame, 0 overrun, 0 ignored\n"
            "     0 output errors, 0 collisions, 2 interface resets\n"
        ),
        'determinable': True, 'rule_should': True,
        'keywords': ['crc', 'cable', 'transceiver', 'error'],
    },
    {
        'id': 'C04',
        'note': 'SSH เข้าอุปกรณ์ไม่ได้ — ตัวความล้มเหลวเองคือหลักฐานว่าสายขาด',
        'node': 'Access-Site-B', 'interface': 'Ethernet0/1',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down',
        'raw_output': (
            "[12:04:38] Access-Site-B (192.168.150.50:2202) - no response\n"
            "Device unreachable, consistent with link failure on Core Ethernet0/1.\n"
        ),
        'determinable': True, 'rule_should': True,
        'keywords': ['unreachable', 'link', 'failure', 'no response'],
    },

    # ---------------- กลุ่มที่ 2: กฎจับไม่ได้ ต้องพึ่งการตีความ (4 เคส) ----------------
    {
        'id': 'C05',
        'note': 'การชนกันของข้อมูลจำนวนมาก — อาการของ duplex ไม่ตรงกันสองฝั่ง',
        'node': 'Core', 'interface': 'Ethernet0/2',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down',
        'raw_output': (
            "$ show interface Ethernet0/2\n"
            "Ethernet0/2 is up, line protocol is up\n"
            "  Half-duplex, 100Mb/s, media type is unknown\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
            "     1247 output errors, 1247 collisions, 0 interface resets\n"
        ),
        'determinable': True, 'rule_should': False,
        'keywords': ['collision', 'duplex', 'output error', 'error'],
    },
    {
        'id': 'C06',
        'note': 'คิวขาเข้าเต็ม packet ถูกทิ้ง — กฎไม่มีเงื่อนไขเรื่อง queue',
        'node': 'Core', 'interface': 'Ethernet0/0',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/0, changed state to down',
        'raw_output': (
            "$ show interface Ethernet0/0\n"
            "Ethernet0/0 is up, line protocol is up\n"
            "  Input queue: 75/75/2841/0 (size/max/drops/flushes)\n"
            "  Output queue: 0/40 (size/max)\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
        ),
        'determinable': True, 'rule_should': False,
        'keywords': ['queue', 'drop', 'congestion', 'full', 'buffer'],
    },
    {
        'id': 'C07',
        'note': 'พอร์ตกระพริบขึ้นลงซ้ำๆ — เห็นได้จาก log เท่านั้น สถานะปัจจุบันปกติ',
        'node': 'Core', 'interface': 'Ethernet0/1',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down',
        'raw_output': (
            "$ show interface Ethernet0/1\n"
            "Ethernet0/1 is up, line protocol is up\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
            "\n$ show logging\n"
            "*Aug 10 14:31:02.117: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down\n"
            "*Aug 10 14:31:14.220: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to up\n"
            "*Aug 10 14:32:41.882: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down\n"
            "*Aug 10 14:32:53.004: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to up\n"
            "*Aug 10 14:34:19.551: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down\n"
            "*Aug 10 14:34:30.773: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to up\n"
        ),
        'determinable': True, 'rule_should': False,
        'keywords': ['flap', 'repeated', 'intermittent', 'unstable', 'changed state'],
    },
    {
        'id': 'C08',
        'note': 'เพื่อนบ้าน routing หลุด — กฎ Phase 3 ไม่มีเงื่อนไขเรื่อง OSPF',
        'node': 'Core', 'interface': 'Ethernet0/1',
        'summary': '%OSPF-5-ADJCHG: Process 1, Nbr 3.3.3.3 on Ethernet0/1 from FULL to DOWN',
        'raw_output': (
            "$ show interface Ethernet0/1\n"
            "Ethernet0/1 is up, line protocol is up\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
            "\n$ show logging\n"
            "*Aug 10 15:02:11.400: %OSPF-5-ADJCHG: Process 1, Nbr 3.3.3.3 on Ethernet0/1 "
            "from FULL to DOWN, Neighbor Down: Dead timer expired\n"
        ),
        'determinable': True, 'rule_should': False,
        'keywords': ['ospf', 'neighbor', 'adjacency', 'dead timer', 'routing'],
    },

    # ---------------- กลุ่มที่ 3: ข้อมูลไม่พอสรุป — คำตอบที่ถูกคือ "สรุปไม่ได้" (4 เคส) ----------------
    # ⭐ กลุ่มนี้สำคัญที่สุดในการวัดการตอบมั่ว: แนวทางไหนที่ "สรุปได้" ในกลุ่มนี้ = ตอบมั่ว
    {
        'id': 'C09',
        'note': 'ทุก interface สถานะปกติ ไม่มีร่องรอยความผิดปกติใดเลย',
        'node': 'Core', 'interface': 'Ethernet0/1',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down',
        'raw_output': (
            "$ show ip interface brief\n"
            "Interface              IP-Address      OK? Method Status    Protocol\n"
            "Ethernet0/0            10.0.1.1        YES manual up        up\n"
            "Ethernet0/1            10.0.2.1        YES manual up        up\n"
            "Ethernet0/2            10.0.3.1        YES manual up        up\n"
        ),
        'determinable': False, 'rule_should': False, 'keywords': [],
    },
    {
        'id': 'C10',
        'note': 'ตัวนับทุกตัวเป็น 0 และสถานะ up — ปกติดีทุกอย่าง',
        'node': 'Core', 'interface': 'Ethernet0/2',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down',
        'raw_output': (
            "$ show interface Ethernet0/2\n"
            "Ethernet0/2 is up, line protocol is up\n"
            "  Full-duplex, 100Mb/s, media type is unknown\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored\n"
            "     0 output errors, 0 collisions, 0 interface resets\n"
        ),
        'determinable': False, 'rule_should': False, 'keywords': [],
    },
    {
        'id': 'C11',
        'note': 'มีแต่ log ของระบบเราเอง (SSH เข้าไปตรวจ) ไม่มีเหตุการณ์จริงเลย',
        'node': 'Core', 'interface': 'Ethernet0/1',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down',
        'raw_output': (
            "$ show logging\n"
            "*Aug 10 16:01:11.220: %SSH-5-SSH2_SESSION: SSH2 Session request from 192.168.150.1 - succeeded\n"
            "*Aug 10 16:01:12.100: %SEC_LOGIN-5-LOGIN_SUCCESS: Login Success [user: admin]\n"
            "*Aug 10 16:01:18.443: %SYS-6-LOGOUT: User admin has exited tty session 0\n"
        ),
        'determinable': False, 'rule_should': False, 'keywords': [],
    },
    {
        'id': 'C12',
        'note': 'ผลลัพธ์สั้นมากจนไม่มีสาระ — จำลองกรณีคำสั่งคืนค่าไม่ครบ',
        'node': 'Access-Site-A', 'interface': 'Ethernet0/0',
        'summary': '%LINK-3-UPDOWN: Interface Ethernet0/0, changed state to down',
        'raw_output': "$ show ip interface brief\nInterface              IP-Address      OK? Method Status    Protocol\n",
        'determinable': False, 'rule_should': False, 'keywords': [],
    },
]


# ---------------------------------------------------------------------------
# เคสที่ใช้ raw_output **ของจริงจาก lab** (Phase 6 งาน I)
#
# สร้างโดย `make_real_cases.py` เก็บไว้คนละไฟล์โดยเจตนา เพื่อให้แยกออกจากกันได้ชัดว่า
# เคสไหนเป็นข้อมูลจำลอง (C01-C12 ในไฟล์นี้) และเคสไหนมาจากอุปกรณ์จริง
# -> ตอนเขียนบทที่ 5.2 ต้องรายงานแยกกัน ไม่ใช่รวมเป็นตัวเลขเดียว
#
# ถ้ายังไม่ได้สร้าง `real_cases.py` ไฟล์นี้ก็ยังใช้ได้ตามปกติด้วยเคสจำลอง 12 เคสเดิม
# ---------------------------------------------------------------------------
try:
    from testcases.real_cases import REAL_CASES
except ImportError:
    try:
        from real_cases import REAL_CASES        # กรณีรันไฟล์นี้ตรงๆ จากในโฟลเดอร์ testcases
    except ImportError:
        REAL_CASES = []

SIMULATED_CASES = list(CASES)      # เก็บชุดจำลองไว้อ้างอิงแยก (ใช้ตอนรายงานผล)
CASES = CASES + REAL_CASES


def matches_expected(cause_summary, case):
    """สาเหตุที่ได้ ตรงกับที่คาดหวังไหม — นับว่าถูกเมื่อมี keyword อย่างน้อย 1 คำ

    ⚠️ เป็นการวัดแบบหยาบด้วยคำสำคัญ ไม่ใช่การตัดสินความถูกต้องเชิงความหมาย — ต้องระบุ
    ข้อจำกัดนี้ในเล่มด้วย (คนอ่านผลต้องรู้ว่าตัวเลข 'ความถูกต้อง' วัดมาอย่างไร)
    """
    if not cause_summary:
        return False
    lowered = cause_summary.lower()
    return any(k.lower() in lowered for k in case['keywords'])


if __name__ == '__main__':
    print(f"ชุดเคสทั้งหมด: {len(CASES)} เคส "
          f"(จำลอง {len(SIMULATED_CASES)} · จากอุปกรณ์จริง {len(REAL_CASES)})\n")
    if REAL_CASES:
        for c in REAL_CASES:
            print(f"  🔬 {c['id']:<7} จาก {c.get('source_ticket', '?')} — {c['note'][:52]}")
        print()

    determinable = [c for c in CASES if c['determinable']]
    rule_expected = [c for c in CASES if c['rule_should']]

    print(f"  สรุปได้ (มีคำตอบถูก)        : {len(determinable)} เคส")
    print(f"  สรุปไม่ได้ (คำตอบ = 'ไม่ทราบ') : {len(CASES) - len(determinable)} เคส")
    print(f"  คาดว่ากฎจับได้              : {len(rule_expected)} เคส")
    print(f"  คาดว่าต้องพึ่งการตีความ       : {len(determinable) - len(rule_expected)} เคส")

    # ตรวจความสมเหตุสมผลของชุดเคสเอง
    problems = []
    ids = [c['id'] for c in CASES]
    if len(set(ids)) != len(ids):
        problems.append("มี id ซ้ำกัน")
    for c in CASES:
        if c['determinable'] and not c['keywords']:
            problems.append(f"{c['id']}: บอกว่าสรุปได้ แต่ไม่มี keyword ให้ตรวจ")
        if not c['determinable'] and c['keywords']:
            problems.append(f"{c['id']}: บอกว่าสรุปไม่ได้ แต่กลับมี keyword")
        if not c['raw_output'].strip():
            problems.append(f"{c['id']}: raw_output ว่าง (ผิดเกณฑ์ข้อ 4)")
        if c['rule_should'] and not c['determinable']:
            problems.append(f"{c['id']}: กฎจับได้แต่บอกว่าสรุปไม่ได้ — ขัดกันเอง")

    # ชุดเคสต้องกระจายตัว ไม่ใช่กฎจับได้หมดหรือจับไม่ได้เลย
    if not (2 <= len(rule_expected) <= len(CASES) - 2):
        problems.append("ชุดเคสไม่กระจายตัว — เทียบ 3 แนวทางแล้วจะไม่เห็นความต่าง")
    if len(CASES) - len(determinable) < 3:
        problems.append("เคส 'สรุปไม่ได้' น้อยเกินไป — วัดการตอบมั่วไม่ได้")

    print()
    for p in problems:
        print(f"❌ {p}")
    assert not problems, "ชุดเคสมีปัญหา"
    print("✅ ชุดเคสสมเหตุสมผลและกระจายตัวพอที่จะเทียบ 3 แนวทางได้")
