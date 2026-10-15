r"""
Phase 6 - เก็บหลักฐานการเข้าถึงจาก Core เพื่อป้อนให้ lib/disposition.py

แยกหน้าที่ตามแบบเดียวกับ diagnostics.py / cause_rules.py:
    ไฟล์นี้   = ส่วนที่ต่ออุปกรณ์จริง (I/O) ทดสอบต้องมี lab
    disposition.py = กฎล้วนๆ ทดสอบได้เองโดยไม่ต้องมี lab

คำสั่งที่ใช้ทั้งหมดเป็น read-only:
    show ip interface brief | include <interface>
    ping <peer_ip> repeat 5
    ping <customer_lan> repeat 5
    show logging | include <interface>

⚠️ ping เป็นคำสั่งที่ไม่เปลี่ยนแปลงการตั้งค่าใดๆ แต่ก็ยังต้องอยู่ในรายการที่ได้รับอนุญาต
   ก่อนนำไปใช้กับระบบจริง ต้องขอสิทธิ์จากทีมเจ้าของระบบให้เรียบร้อยก่อน

⚠️ ต้อง ping จาก Core เท่านั้น ห้าม ping จาก Access ที่กำลังมีปัญหา
   เพราะถ้าลิงก์ขาด เราก็ SSH เข้า Access ไม่ได้อยู่แล้ว และผลที่ได้จะไม่มีความหมาย
"""
from datetime import datetime

from netmiko import ConnectHandler

try:
    from lib import disposition, mapping
    from lib.lab_secrets import CREDENTIALS     # รหัสแยกไว้ในไฟล์ที่ไม่ขึ้น GitHub
except ImportError:          # กรณีรันไฟล์นี้ตรงๆ จากในโฟลเดอร์ lib
    import disposition
    import mapping
    from lab_secrets import CREDENTIALS
SSH_TIMEOUT = 10
PING_REPEAT = 5
PROBE_FROM = 'Core'


def _connect(device=PROBE_FROM):
    target = mapping.DEVICE_SSH[device]
    conn = ConnectHandler(
        device_type='cisco_ios', host=target['host'], port=target['port'],
        timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT, **CREDENTIALS,
    )
    conn.enable()
    return conn


def ping_ok(raw):
    """
    แปลผล ping ของ IOS

    IOS ตอบ 'Success rate is 0 percent' เมื่อไม่ผ่านเลย
    และใช้ '!' แทนแพ็กเก็ตที่ตอบกลับ '.' แทนที่หาย
    ถือว่าผ่านเมื่อมี '!' อย่างน้อยหนึ่งตัวและ success rate ไม่เป็น 0
    """
    if not raw:
        return None                      # ไม่มีผล = ยังไม่รู้ ไม่ใช่ไม่ผ่าน
    if 'Success rate is 0' in raw:
        return False
    return '!' in raw


def site_of(node=None, interface=None):
    """หาว่า TT ใบนี้เกี่ยวกับ Site ไหน — ดูจากชื่อ node ก่อน ถ้าไม่ได้ค่อยดู interface"""
    if node in mapping.SITE_ADDRESSES:
        return node
    return mapping.CORE_INTERFACE_TO_SITE_ADDR.get(interface)


def collect(node=None, interface=None, since=None, conn=None):
    """
    เก็บหลักฐานทั้งหมดที่ lib/disposition.decide() ต้องใช้

    since : เวลาที่ TT เปิด (datetime) ใช้กรอง log — ถ้าไม่ส่งมาจะไม่กรอง
            และจะไม่นับ log เป็นหลักฐานเลย เพื่อกันการสรุปว่า nofault ผิดพลาด
            จาก log เก่าที่ค้างอยู่ในบัฟเฟอร์

    คืน dict 2 ส่วน:
        facts    -> ส่งเข้า disposition.decide() ได้ตรงๆ
        raw      -> ผลดิบทุกคำสั่ง ไว้แสดงเป็นหลักฐานให้ agent ตรวจ
    """
    site = site_of(node, interface)
    facts = {
        'interface': interface,
        'admin_down': None,
        'peer_reachable': None,
        'customer_reachable': None,
        'recent_log_lines': [],
    }
    raw = {'site': site, 'probed_from': PROBE_FROM,
           'probed_at': datetime.now().isoformat(timespec='seconds')}

    if site is None:
        raw['error'] = (f"ไม่รู้จัก Site ของ node={node} interface={interface} — "
                        f"ตรวจสอบ mapping.SITE_ADDRESSES")
        return {'facts': facts, 'raw': raw}

    addr = mapping.SITE_ADDRESSES[site]
    core_if = interface or addr['core_interface']
    facts['interface'] = core_if

    own = conn is None
    if own:
        try:
            conn = _connect()
        except Exception as exc:
            raw['error'] = f"เชื่อมต่อ {PROBE_FROM} ไม่ได้: {exc}"
            return {'facts': facts, 'raw': raw}

    try:
        brief = conn.send_command(f'show ip interface brief | include {core_if}')
        raw['interface_brief'] = brief.strip()
        facts['admin_down'] = 'administratively down' in brief

        if facts['admin_down']:
            # พอร์ตถูกปิดอยู่ ping ไม่มีความหมาย — ไม่ต้องเสียเวลา และไม่ต้องเดาผล
            raw['ping_peer'] = '(ข้าม — พอร์ตอยู่ในสถานะ administratively down)'
            raw['ping_customer'] = '(ข้าม — พอร์ตอยู่ในสถานะ administratively down)'
        else:
            peer = conn.send_command(
                f"ping {addr['peer_ip']} repeat {PING_REPEAT}", read_timeout=40)
            cust = conn.send_command(
                f"ping {addr['customer_lan']} repeat {PING_REPEAT}", read_timeout=40)
            raw['ping_peer'] = peer.strip()
            raw['ping_customer'] = cust.strip()
            facts['peer_reachable'] = ping_ok(peer)
            facts['customer_reachable'] = ping_ok(cust)

        logging_out = conn.send_command(f'show logging | include {core_if}')
        raw['logging'] = logging_out.strip()
        if since is not None:
            facts['recent_log_lines'] = disposition.recent_log_lines(
                logging_out, since, interface=core_if)
        else:
            raw['logging_note'] = ('ไม่ได้ส่งเวลาที่ TT เปิดมา จึงไม่นับ log เป็นหลักฐาน '
                                   '— ป้องกันการสรุปว่าหายเองจาก log เก่าที่ค้างอยู่')
    finally:
        if own:
            conn.disconnect()

    raw['peer_ip'] = addr['peer_ip']
    raw['customer_lan'] = addr['customer_lan']
    return {'facts': facts, 'raw': raw}


def assess(node=None, interface=None, since=None, conn=None):
    """เก็บหลักฐานแล้วตัดสินในขั้นตอนเดียว — คืน {'facts', 'raw', 'result'}"""
    collected = collect(node=node, interface=interface, since=since, conn=conn)
    collected['result'] = disposition.decide(collected['facts'])
    return collected


# ---------------------------------------------------------------------------
# ทดสอบส่วนที่ไม่ต้องพึ่ง lab -- รัน: py lib\reachability.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    OK = ("Type escape sequence to abort.\n"
          "Sending 5, 100-byte ICMP Echos to 10.0.1.2, timeout is 2 seconds:\n"
          "!!!!!\nSuccess rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms")
    BAD = ("Sending 5, 100-byte ICMP Echos to 10.0.1.2, timeout is 2 seconds:\n"
           ".....\nSuccess rate is 0 percent (0/5)")

    check(ping_ok(OK) is True, 'ping สำเร็จ -> True')
    check(ping_ok(BAD) is False, 'ping ล้มเหลว -> False')
    check(ping_ok('') is None, 'ไม่มีผล ping -> None (ยังไม่รู้ ไม่ใช่ไม่ผ่าน)')

    check(site_of(node='Access-Site-B') == 'Access-Site-B', 'หา Site จากชื่อ node')
    check(site_of(node='Core', interface='Ethernet0/2') == 'Access-Site-C',
          'node เป็น Core -> หา Site จาก interface ฝั่ง Core')
    check(site_of(node='Core', interface='Ethernet0/3') is None,
          'interface ที่ไม่ได้ต่อ Site ใด -> None')

    out = collect(node='ไม่มีอยู่จริง', interface='Ethernet9/9')
    check(out['facts']['admin_down'] is None, 'หา Site ไม่เจอ -> ไม่เดาหลักฐาน')
    check('error' in out['raw'], 'หา Site ไม่เจอ -> มีข้อความอธิบายกลับไป')
    check(disposition.decide(out['facts'])['disposition'] == 'undetermined',
          'หลักฐานว่าง -> disposition = undetermined')

    print()
    print(f"ผ่าน {passed}/{total}")
    print("(ทดสอบกับ lab จริงด้วย: py check_disposition.py A)")
    raise SystemExit(0 if passed == total else 1)
