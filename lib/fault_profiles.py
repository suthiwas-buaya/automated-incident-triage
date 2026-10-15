"""
fault_profiles — นิยาม fault 4 ประเภทตามปลายทางที่ต้องส่งต่อ

แนวคิด:
    เดิม fault ทุกแบบคือ "shutdown ที่ Core" เหมือนกันหมด อุปกรณ์จึงให้หลักฐาน
    ออกมาเหมือนกันทุกครั้ง (administratively down) ตัวคัดแยกจะแยกไม่ออกว่า
    งานนี้ต้องส่งใคร

    ของใหม่: แยกประเภทด้วย **ตำแหน่งที่ไป shutdown** ไม่ใช่ด้วยป้ายชื่อ
    หลักฐานที่ Core มองเห็นจึงต่างกันจริง โดยไม่ต้องปลอมข้อมูลอะไรเลย

        ประเภท          ไป shutdown ที่                 Core มองเห็น
        ------------------------------------------------------------------
        configuration   Core   Ethernet0/x              พอร์ตตัวเอง admin down
        physical        Access Ethernet0/0              พอร์ตตัวเอง up/up
                                                        แต่ ping peer /30 ไม่ผ่าน
        customer        Access Loopback1 (LAN ลูกค้า)   ping peer /30 ผ่าน
                                                        แต่ ping LAN ลูกค้าไม่ผ่าน
        nofault         Core Ethernet0/x แล้วคืนใน 20s  ทุกอย่างปกติ
                                                        มีแต่ร่องรอยใน syslog

ข้อตกลงสำคัญ:
    ประเภทที่เลือกจะถูกบันทึกลง fault_ledger.jsonl เท่านั้น
    **ห้ามส่งเข้าไปใน pipeline วิเคราะห์เด็ดขาด** มันมีไว้เทียบตอนวัดผลว่าระบบตอบถูกไหม
    ถ้าตัวคัดแยกได้เห็นค่านี้ ผลการทดลองทั้งหมดเป็นโมฆะ

ต้องตั้งค่าแล็บก่อน:
    py setup_customer_lan.py    (เพิ่ม Loopback1 + static route — รันครั้งเดียว)
"""
import json
import os
import time
from datetime import datetime

from netmiko import ConnectHandler

import fault_injection as fi
from lib import safe_remote, ticket_draft

# ---------------------------------------------------------------------------
# แผนผัง Site — ตรงกับ Phase0.3_Topology_Setup.md และ setup_customer_lan.py
# ---------------------------------------------------------------------------
SITES = {
    'A': {
        'node': 'Access-Site-A',
        'core_if': 'Ethernet0/0',       # พอร์ตฝั่ง Core ที่เสียบไป Site นี้
        'access_if': 'Ethernet0/0',     # พอร์ตฝั่ง Access ที่เสียบกลับมา Core
        'peer_ip': '10.0.1.2',
        'lan_ip': '192.168.10.1',
        'ssh_port': 2201,
    },
    'B': {
        'node': 'Access-Site-B',
        'core_if': 'Ethernet0/1',
        'access_if': 'Ethernet0/0',
        'peer_ip': '10.0.2.2',
        'lan_ip': '192.168.20.1',
        'ssh_port': 2202,
    },
    'C': {
        'node': 'Access-Site-C',
        'core_if': 'Ethernet0/2',
        'access_if': 'Ethernet0/0',
        'peer_ip': '10.0.3.2',
        'lan_ip': '192.168.30.1',
        'ssh_port': 2203,
    },
}

CUSTOMER_IF = 'Loopback1'

# ---------------------------------------------------------------------------
# วิธีจำลอง fault แบบ Physical — เลือกได้ 2 แบบ
#
#   'acl'       (ค่าตั้งต้น) ใส่ ACL ขาเข้าบน Ethernet0/0 ของ Access ให้ทิ้ง ICMP
#               ผลที่ Core เห็น: พอร์ตตัวเอง up/up แต่ ping ปลายสายไม่ผ่าน
#               = หลักฐานเหมือนสายขาดทุกประการสำหรับการวินิจฉัยของ L1
#               ข้อดี: SSH ยังเข้าได้ ซ่อมกลับได้ทันที ไม่ต้องรอ reboot
#
#   'shutdown'  ปิดพอร์ต Ethernet0/0 ของ Access ทั้งพอร์ต
#               เหมือนของจริงมากกว่า แต่ตัดเส้นทาง SSH ของตัวเองไปด้วย
#               ทำให้ซ่อมกลับไม่ได้ ต้องรอ reload หรือไปแก้ผ่าน Console ใน CML
#               ใช้ตอนอยากแสดงให้เห็นข้อจำกัดของ topology เท่านั้น
#
# ข้อจำกัดนี้เกิดเพราะ Access ทุกตัวมีทางเข้าออกทางเดียวคือ Ethernet0/0
# ไม่มี out-of-band management — ต้องเขียนไว้ในเล่มตรงๆ
# ---------------------------------------------------------------------------
PHYSICAL_METHOD = 'acl'

PHYSICAL_ACL = 'SIM-PHYSICAL-CUT'
PHYSICAL_ACL_LINES = [
    f'ip access-list extended {PHYSICAL_ACL}',
    ' deny icmp any any',
    ' permit ip any any',
]
NOFAULT_HOLD_SECONDS = 20       # นานพอให้ syslog ยิงออกมา แล้วคืนสภาพเอง

DISPOSITIONS = {
    'configuration': 'Configuration — ต้องแก้ config ส่งต่อ L2 (มี PW)',
    'physical':      'Physical — สายขาดระหว่างทาง ส่งช่างหน้างาน',
    'customer':      'Customer-side — ปัญหาอยู่เลย demarcation แจ้งลูกค้า',
    'nofault':       'No-fault — หายเอง ปิดงานพร้อมหลักฐาน',
}

LEDGER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'fault_ledger.jsonl',
)


# ---------------------------------------------------------------------------
# สมุดบันทึก — เก็บ "เฉลย" ไว้นอก pipeline
# ---------------------------------------------------------------------------
def record(**fields):
    fields['ts'] = datetime.now().isoformat(timespec='seconds')
    with open(LEDGER, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(fields, ensure_ascii=False) + '\n')


# ---------------------------------------------------------------------------
# ตัวช่วยต่ออุปกรณ์
# ---------------------------------------------------------------------------
def connect_access(site_key):
    device = dict(fi.CORE)
    device['port'] = SITES[site_key]['ssh_port']
    device['conn_timeout'] = 15
    conn = ConnectHandler(**device)
    conn.enable()
    return conn


def ping_from_core(conn, target, repeat=2):
    out = conn.send_command(f'ping {target} repeat {repeat}', read_timeout=30)
    return 'Success rate is 0' not in out and '!' in out


# ---------------------------------------------------------------------------
# ทำให้ล่ม — แยกตามประเภท
# ---------------------------------------------------------------------------
def _inject_configuration(site_key):
    cfg = SITES[site_key]
    conn = fi.connect()
    try:
        fi.apply_state(conn, [cfg['core_if']], 'down')
    finally:
        conn.disconnect()
    return {'note': f"Core {cfg['core_if']} ถูกสั่งปิด — จะค้างจนกว่าจะสั่งซ่อม"}


def _inject_physical(site_key):
    """
    จำลองอาการ "คุยกับปลายสายไม่ได้" โดยที่พอร์ตฝั่ง Core ยังปกติ

    วิธี acl (ค่าตั้งต้น): ใส่ ACL ขาเข้าบน Ethernet0/0 ของ Access ให้ทิ้ง ICMP
        Core ping 10.0.x.2 และ LAN ลูกค้าไม่ผ่านทั้งคู่ = ลายเซ็นเดียวกับสายขาด
        แต่ SSH (TCP 22) ยังผ่าน จึงซ่อมกลับได้ทันที

    วิธี shutdown: ปิดพอร์ตทั้งพอร์ต — เหมือนจริงกว่า แต่ตัดขาตัวเอง
        ต้องมีประกัน reload และซ่อมกลับด้วยการรอ reboot เท่านั้น
    """
    cfg = SITES[site_key]

    if PHYSICAL_METHOD == 'shutdown':
        info = safe_remote.break_remote(
            cfg['node'],
            [f"interface {cfg['access_if']}", 'shutdown'],
        )
        return {
            'note': f"{cfg['node']} {cfg['access_if']} ถูกปิด — "
                    f"Core จะ ping {cfg['peer_ip']} ไม่ผ่าน",
            'method': 'shutdown',
            'auto_recover_at': info['auto_recover_at'].isoformat(timespec='seconds'),
            'reload_minutes': info['minutes'],
        }

    conn = connect_access(site_key)
    try:
        conn.send_config_set(
            PHYSICAL_ACL_LINES
            + [f"interface {cfg['access_if']}", f'ip access-group {PHYSICAL_ACL} in']
        )
    finally:
        conn.disconnect()
    return {
        'note': f"{cfg['node']} {cfg['access_if']} ทิ้ง ICMP ขาเข้าแล้ว — "
                f"Core จะ ping {cfg['peer_ip']} ไม่ผ่าน (SSH ยังเข้าได้ ซ่อมกลับได้ทันที)",
        'method': 'acl',
    }


def _inject_customer(site_key):
    """
    ปิด Loopback1 (LAN ลูกค้าจำลอง) — ไม่กระทบเส้นทาง SSH จึงไม่ต้องใช้ประกัน reload
    """
    cfg = SITES[site_key]
    conn = connect_access(site_key)
    try:
        conn.send_config_set([f'interface {CUSTOMER_IF}', 'shutdown'])
    finally:
        conn.disconnect()
    return {'note': f"{cfg['node']} {CUSTOMER_IF} ({cfg['lan_ip']}) ถูกปิด — "
                    f"Core ยัง ping {cfg['peer_ip']} ผ่าน แต่ ping LAN ไม่ผ่าน"}


def _inject_nofault(site_key):
    """ปิดแล้วเปิดคืนเอง — เหลือไว้แต่ร่องรอยใน syslog"""
    cfg = SITES[site_key]
    conn = fi.connect()
    try:
        fi.apply_state(conn, [cfg['core_if']], 'down')
        print(f"      ปิด {cfg['core_if']} แล้ว — รอ {NOFAULT_HOLD_SECONDS} วินาทีให้ syslog ยิงออกมา")
        time.sleep(NOFAULT_HOLD_SECONDS)
        fi.apply_state(conn, [cfg['core_if']], 'up')
    finally:
        conn.disconnect()
    return {'note': f"Core {cfg['core_if']} ปิดแล้วเปิดคืนเอง — "
                    f"ตอนนี้สถานะปกติหมด เหลือแต่ร่องรอยใน show logging",
            'self_healed': True}


INJECTORS = {
    'configuration': _inject_configuration,
    'physical': _inject_physical,
    'customer': _inject_customer,
    'nofault': _inject_nofault,
}


def inject(site_key, disposition):
    if site_key not in SITES:
        raise ValueError(f"ไม่รู้จัก site '{site_key}'")
    if disposition not in INJECTORS:
        raise ValueError(f"ไม่รู้จักประเภท '{disposition}'")

    cfg = SITES[site_key]
    result = INJECTORS[disposition](site_key)

    # fault สองประเภทนี้ไม่ทำให้เกิด syslog ระบบจึงไม่เปิด TT ให้เอง
    # ร่างใบแจ้งเหตุไว้ให้ เพื่อไม่ต้องมานั่งพิมพ์เองตอนสาธิต
    if disposition in ('physical', 'customer'):
        draft, draft_path = ticket_draft.draft_for(cfg, disposition)
        result['ticket_draft'] = draft
        result['draft_path'] = draft_path

    record(action='inject', site=site_key, node=cfg['node'],
           core_if=cfg['core_if'], expected_disposition=disposition,
           **{k: v for k, v in result.items()
              if k not in ('note', 'ticket_draft')})
    result['site'] = site_key
    result['disposition'] = disposition
    return result


# ---------------------------------------------------------------------------
# ซ่อม
# ---------------------------------------------------------------------------
def repair(site_key, disposition=None):
    """
    ซ่อม site หนึ่ง — ถ้าไม่ระบุประเภท จะซ่อมทุกจุดที่เป็นไปได้ของ site นั้น
    ปลอดภัยที่จะเรียกซ้ำ ถึงจะไม่มีอะไรล่มอยู่ก็ตาม
    """
    cfg = SITES[site_key]
    done = []

    if disposition in (None, 'configuration', 'nofault'):
        conn = fi.connect()
        try:
            fi.apply_state(conn, [cfg['core_if']], 'up')
        finally:
            conn.disconnect()
        done.append(f"Core {cfg['core_if']} เปิดคืนแล้ว")

    # ถอนทุกอย่างที่ทำไว้บน Access ในการเชื่อมต่อครั้งเดียว
    # (ถ้าเข้าไม่ถึงก็ไม่ต้องลองซ้ำหลายรอบ — แปลว่าใช้วิธี shutdown ไว้ ต้องรอ reload)
    if disposition in (None, 'physical', 'customer'):
        try:
            conn = connect_access(site_key)
        except Exception:
            done.append(f"{cfg['node']} เข้าไม่ถึง — พอร์ตถูกปิดทั้งพอร์ต "
                        f"ต้องรอ reload กู้เอง หรือไปสั่ง reload ที่ Console ใน CML")
            record(action='repair', site=site_key, node=cfg['node'],
                   disposition=disposition or 'all', unreachable=True)
            return done
        try:
            conn.send_config_set([
                f"interface {cfg['access_if']}",
                f'no ip access-group {PHYSICAL_ACL} in',
                'no shutdown',
                f'interface {CUSTOMER_IF}',
                'no shutdown',
            ])
            conn.send_config_set([f'no ip access-list extended {PHYSICAL_ACL}'])
            safe_remote.disarm(conn)          # เผื่อมีประกัน reload ค้างอยู่
            done.append(f"{cfg['node']} ถอน ACL · เปิด {cfg['access_if']} "
                        f"และ {CUSTOMER_IF} คืนแล้ว")
        finally:
            conn.disconnect()

    record(action='repair', site=site_key, node=cfg['node'],
           disposition=disposition or 'all')
    return done


def repair_everything():
    """ซ่อมทุก site ทุกจุด + คืนสถานะ Core ให้ครบ"""
    lines = []
    conn = fi.connect()
    try:
        fi.apply_state(conn, fi.WATCHED, 'up')
    finally:
        conn.disconnect()
    lines.append("Core: เปิดคืนทุกพอร์ต + Loopback0")

    for key in SITES:
        lines.extend(f"  {line}" for line in repair(key))
    return lines


# ---------------------------------------------------------------------------
# อ่านสภาพจริงจาก Core — ใช้หลักฐานชุดเดียวกับที่ตัวคัดแยกจะใช้
# ---------------------------------------------------------------------------
def probe_all():
    """
    คืน dict: {site_key: {'core_admin': 'up'|'down',
                          'peer_ok': bool, 'lan_ok': bool}}
    """
    conn = fi.connect()
    try:
        core_status = fi.read_status(conn)
        out = {}
        for key, cfg in SITES.items():
            admin = core_status.get(cfg['core_if'], '?')
            if admin == 'down':
                # พอร์ตปิดอยู่ ไม่ต้อง ping ให้เสียเวลา ผลเป็น fail แน่นอน
                out[key] = {'core_admin': 'down', 'peer_ok': False, 'lan_ok': False}
                continue
            out[key] = {
                'core_admin': admin,
                'peer_ok': ping_from_core(conn, cfg['peer_ip']),
                'lan_ok': ping_from_core(conn, cfg['lan_ip']),
            }
    finally:
        conn.disconnect()
    return out


def describe(state):
    """แปลสภาพที่ Core มองเห็น เป็นคำอธิบายสั้นๆ สำหรับแสดงในเมนู"""
    if state['core_admin'] == 'down':
        return '🔴 พอร์ต Core ถูกปิดอยู่'
    if not state['peer_ok']:
        return '🔴 พอร์ต Core ปกติ แต่คุยกับปลายสายไม่ได้'
    if not state['lan_ok']:
        return '🟠 ลิงก์ปกติ แต่ LAN ลูกค้าไม่ตอบ'
    return '🟢 ปกติทุกจุด'
