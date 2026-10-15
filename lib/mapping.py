"""
Phase 2 - Keyword Mapping Table (ไฟล์ config — แก้ตรงนี้ได้เลยไม่ต้องแตะ logic)

ตอบ Open Item #5 ใน Plan.md: สร้าง Owner Group ใหม่ 4 กลุ่มที่ map กับ fault ที่จำลองได้จริง
แทน 5 กลุ่มเดิมที่อิงข้อมูล ITSM ของจริง (ซึ่งเป็นข้อมูลปิด)
"""
import re

# ---------------------------------------------------------------------------
# 1) Owner Group — 4 ทีมที่รับผิดชอบ
# ---------------------------------------------------------------------------
OWNER_GROUPS = {
    'Network-Core': 'ดูแลอุปกรณ์แกนกลาง (Core Router) และ routing ระดับ backbone',
    'Network-Access': 'ดูแลสายเชื่อมระหว่าง Core กับ Access Site และอุปกรณ์ปลายทาง',
    'Transmission': 'ดูแลชั้นกายภาพ — สาย สัญญาณ carrier ระดับ transport',
    'NOC-Monitoring': 'เฝ้าระวังทั่วไป รับเคสที่ยังระบุเจ้าภาพไม่ได้ (fallback)',
}

FALLBACK_GROUP = 'NOC-Monitoring'

# ---------------------------------------------------------------------------
# 2) กฎจำแนก — เรียงตามลำดับความสำคัญ (ตัวแรกที่ match ชนะ)
#
#    แต่ละกฎประกอบด้วย:
#      name        ชื่อกฎ (ใช้อธิบายเหตุผลใน TT)
#      group       Owner Group ที่จะได้
#      base_score  คะแนนความมั่นใจพื้นฐาน
#      match       ฟังก์ชันรับ context -> True/False
#
#    context ที่ส่งเข้ามามี key:
#      reporter    node ที่ส่ง syslog มา (เช่น 'Core')
#      node        node ที่ได้รับผลกระทบจริง (เช่น 'Access-Site-B')
#      interface   interface ที่เกิดเหตุ (เช่น 'Ethernet0/1')
#      facility    เช่น 'LINK'
#      mnemonic    เช่น 'CHANGED' / 'UPDOWN'
#      severity    0-7
#      message     ข้อความส่วนท้ายของ syslog (ตัวเล็กทั้งหมด)
#      is_site_isolating  True ถ้า fault นี้ทำให้ Site หลุดจากเครือข่ายทั้ง Site
# ---------------------------------------------------------------------------

# keyword ที่บ่งชี้ปัญหาชั้นกายภาพ (transmission)
TRANSMISSION_KEYWORDS = (
    'carrier', 'signal loss', 'loss of signal', 'los', 'sfp',
    'transceiver', 'fiber', 'optical', 'crc', 'framing',
)

# keyword ที่บ่งชี้ปัญหา routing ระดับ core
CORE_KEYWORDS = (
    'bgp', 'ospf', 'adjacency', 'neighbor', 'loopback', 'route',
)


def _has_keyword(message, keywords):
    """
    match แบบ "ทั้งคำ" (word boundary) ไม่ใช่ substring

    ⚠️ ทำไมต้องใช้ word boundary: ตอนทดสอบเจอบั๊กจริง — keyword `los`
    ไปตรงกับคำว่า "lost" ในข้อความ BGP ("adjacency lost") ทำให้จำแนกเป็น
    Transmission แทนที่จะเป็น Network-Core
    """
    return any(re.search(r'\b' + re.escape(k) + r'\b', message) for k in keywords)


# กฎเรียงจาก "เจาะจงที่สุด" ไป "กว้างที่สุด" — ตัวแรกที่ match ชนะ
RULES = [
    {
        'name': 'core-loopback-down',
        'group': 'Network-Core',
        'base_score': 95,
        'match': lambda c: c['interface'].lower().startswith('loopback'),
        'why': 'Loopback down indicates a core device problem, not a single link',
    },
    {
        'name': 'physical-layer-keyword',
        'group': 'Transmission',
        'base_score': 85,
        'match': lambda c: _has_keyword(c['message'], TRANSMISSION_KEYWORDS),
        'why': 'Message contains a physical-layer keyword (cable/signal/optic)',
    },
    {
        'name': 'core-routing-keyword',
        'group': 'Network-Core',
        'base_score': 85,
        'match': lambda c: _has_keyword(c['message'], CORE_KEYWORDS),
        'why': 'Message contains a core routing/protocol keyword',
    },
    {
        'name': 'core-to-access-link-down',
        'group': 'Network-Access',
        'base_score': 95,
        'match': lambda c: c['facility'] == 'LINK' and c['is_site_isolating'],
        'why': 'Core-to-Access Site link down, isolating the site from the network',
    },
    {
        'name': 'generic-link-event',
        'group': 'Network-Access',
        'base_score': 65,
        'match': lambda c: c['facility'] == 'LINK',
        'why': 'Link event without a clearly identified destination site',
    },
]

# ---------------------------------------------------------------------------
# 3) Severity -> Priority (ตามมาตรฐาน syslog severity ของ Cisco)
# ---------------------------------------------------------------------------
SEVERITY_TO_PRIORITY = {
    0: 'P1',  # emergency
    1: 'P1',  # alert
    2: 'P1',  # critical
    3: 'P2',  # error       <- %LINK-3-UPDOWN
    4: 'P3',  # warning
    5: 'P3',  # notification <- %LINK-5-CHANGED
    6: 'P4',  # informational
    7: 'P4',  # debug
}

DEFAULT_PRIORITY = 'P4'

# ---------------------------------------------------------------------------
# 4) กฎ override priority — impact สำคัญกว่า severity
#
#    เหตุผล: severity ของ Cisco บอกแค่ "ประเภทเหตุการณ์" ไม่ได้บอก
#    "ผลกระทบทางธุรกิจ" — admin down (severity 5) ที่ทำให้ Site หลุดทั้ง Site
#    ส่งผลกระทบจริงเท่ากับสายขาด จึงต้องยก priority ขึ้น
#    (ระบุเป็น design decision ในเล่มรายงาน)
# ---------------------------------------------------------------------------
SITE_ISOLATING_MIN_PRIORITY = 'P2'

PRIORITY_ORDER = ['P1', 'P2', 'P3', 'P4']   # index น้อย = ด่วนกว่า


def escalate(priority, floor):
    """คืน priority ที่ด่วนกว่าระหว่าง priority ปัจจุบันกับ floor ที่กำหนด"""
    if priority not in PRIORITY_ORDER:
        return floor
    return priority if PRIORITY_ORDER.index(priority) <= PRIORITY_ORDER.index(floor) else floor


# ---------------------------------------------------------------------------
# 5) Phase 3 — Deep Diagnostic (ดู docs/BRIEF_Phase3_Analyze.md ข้อ 3)
#    config ล้วนๆ แก้/เพิ่มคำสั่งได้ตรงนี้โดยไม่ต้องแตะ lib/diagnostics.py
# ---------------------------------------------------------------------------

# port forwarding ของแต่ละ node — ห้าม SSH ด้วย Loopback IP ตรงๆ (ข้อห้าม #6)
#
# ⚠️ docs/BRIEF_Phase3_Analyze.md เขียน host เป็น 172.20.10.5 (ของเก่า — libvirt/virbr0
# NAT ก่อน 4 ส.ค. 2026) ค่านั้นใช้ไม่ได้แล้ว เครือข่ายเปลี่ยนมาเป็น VMware NAT (VMnet8) ตาม
# docs/DECISIONS.md ข้อ E1 — ต้องใช้ 192.168.150.50 (ตรงกับ CLAUDE.md และ fault_injection.py
# ที่ใช้ host เดียวกันนี้อยู่แล้วสำหรับ Core) ยืนยันแล้วว่า 172.20.10.5 unreachable จริง
DEVICE_SSH = {
    'Core': {'host': '192.168.150.50', 'port': 22},
    'Access-Site-A': {'host': '192.168.150.50', 'port': 2201},
    'Access-Site-B': {'host': '192.168.150.50', 'port': 2202},
    'Access-Site-C': {'host': '192.168.150.50', 'port': 2203},
}

# ---------------------------------------------------------------------------
# ที่อยู่ที่ใช้ทดสอบการเข้าถึง (Phase 6 — ใช้โดย lib/reachability.py)
#
# แต่ละ Site มีจุดที่ต้อง ping 2 จุด และลำดับมีความหมาย:
#   peer_ip      IP ปลายอีกด้านของลิงก์ /30 = ขอบเขตความรับผิดชอบของเรา
#   customer_lan IP ของ LAN ฝั่งลูกค้า      = เลย demarcation point ออกไป
#
# ping จุดแรกไม่ผ่าน  -> ปัญหาอยู่ในลิงก์ของเรา      (งานช่างหน้างาน)
# ping จุดแรกผ่าน จุดที่สองไม่ผ่าน -> ปัญหาอยู่ฝั่งลูกค้า (แจ้งลูกค้า)
#
# customer_lan เป็น Loopback1 ที่สร้างขึ้นเพื่อจำลองฝั่งลูกค้า (setup_customer_lan.py)
# ในระบบจริงต้องเปลี่ยนเป็น IP ของอุปกรณ์ CPE จริงที่ได้รับอนุญาตให้ ping
# ---------------------------------------------------------------------------
SITE_ADDRESSES = {
    'Access-Site-A': {
        'core_interface': 'Ethernet0/0',
        'peer_ip': '10.0.1.2',
        'customer_lan': '192.168.10.1',
    },
    'Access-Site-B': {
        'core_interface': 'Ethernet0/1',
        'peer_ip': '10.0.2.2',
        'customer_lan': '192.168.20.1',
    },
    'Access-Site-C': {
        'core_interface': 'Ethernet0/2',
        'peer_ip': '10.0.3.2',
        'customer_lan': '192.168.30.1',
    },
}

# หา Site จากชื่อ interface ฝั่ง Core (ticket เก็บ interface ของ Core ไว้)
CORE_INTERFACE_TO_SITE_ADDR = {
    v['core_interface']: k for k, v in SITE_ADDRESSES.items()
}


# interface ที่เลือกได้ของแต่ละ node ตอนแจ้งเหตุด้วยตนเอง (Phase 5 ข้อ C-1 ส่วนขยาย)
#
# ค่าที่ใช้คือ **interface ฝั่ง Core** ตามที่ lib/syslog_parser.py CORE_INTERFACE_TO_SITE
# บันทึกไว้ — TT ที่มาจาก syslog เก็บ node = Site ที่เดือดร้อน คู่กับ interface ของ Core
# ที่เส้นนั้นเสียบอยู่ ช่องแจ้งเหตุเองจึงต้องใช้ความหมายเดียวกัน ไม่งั้นสองเส้นทางจะเก็บ
# ข้อมูลคนละความหมายในคอลัมน์เดียวกัน
#
# 🚫 Ethernet0/3 ไม่อยู่ในรายการโดยเจตนา — เป็นทางเข้า lab (SSH + syslog) ห้ามแตะ
NODE_INTERFACES = {
    'Core': ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2'],
    'Access-Site-A': ['Ethernet0/0'],
    'Access-Site-B': ['Ethernet0/1'],
    'Access-Site-C': ['Ethernet0/2'],
}

# รายการคำสั่งที่คนเลือกรันเองได้จากหน้า Analyze (Manual Investigation) — whitelist
# ต้อง validate ฝั่ง server ว่าคำสั่งที่ส่งมาตรงกับรายการนี้เป๊ะ ห้ามเชื่อ frontend (ข้อห้าม #5)
MANUAL_COMMANDS = [
    'show interface {interface}',
    'show ip interface brief',
    'show logging',
    'show ip route',
    'show cdp neighbors',
    'show version',
]
