"""
ticket_draft — ร่างใบแจ้งเหตุสำหรับ fault ที่ไม่มี syslog

ปัญหาที่แก้:
    fault 2 ใน 4 ประเภทไม่ทำให้เกิด syslog เลย
        Physical       ปิดพอร์ตฝั่ง Access  -> Core เห็นพอร์ตตัวเอง up/up ตลอด
        Customer-side  ปิด LAN ฝั่งลูกค้า    -> ไม่มีอะไรเปลี่ยนในสายตา Core
    ระบบจึงไม่สร้าง TT ให้เอง ต้องมีคนเปิดเอง

    ในงานจริงนี่ถูกต้องแล้ว — เคสพวกนี้เข้ามาทาง "สายโทรศัพท์" ไม่ใช่ทาง alarm
    แต่ตอนสาธิตให้อาจารย์ดู การให้คนมานั่งพิมพ์ใบแจ้งเหตุเองทำให้เสียจังหวะ
    ไฟล์นี้จึงร่างใบแจ้งเหตุให้พร้อม เหลือแค่ตรวจแล้วกดเปิด TT

หลักการสำคัญ — ข้อความต้องไม่บอกใบ้คำตอบ:
    ข้อความที่ร่างเป็น "คำพูดของลูกค้า" ซึ่งลูกค้าไม่มีทางรู้ว่าปัญหาเกิดจากสายขาด
    หรือจากอุปกรณ์ฝั่งตัวเอง เขารู้แค่ว่า "ใช้งานไม่ได้"
    เพราะฉะนั้นข้อความของ Physical กับ Customer-side จึงหน้าตาเหมือนกันโดยตั้งใจ
    ถ้าร่างให้ต่างกัน ตัวคัดแยกจะได้เปรียบโดยไม่เป็นธรรม และผลทดสอบจะใช้ไม่ได้

    ตัวคัดแยกต้องหาคำตอบจากหลักฐานบนอุปกรณ์เท่านั้น ไม่ใช่จากคำพูดของลูกค้า
"""
import json
import os
import random
from datetime import datetime

# ถ้อยคำที่ลูกค้าพูดได้จริงตอนโทรเข้ามา — ทุกแบบบอกแค่ "ใช้ไม่ได้"
# ไม่มีข้อความไหนที่ชี้ไปยังสาเหตุ และใช้ชุดเดียวกันทุกประเภทโดยตั้งใจ
SYMPTOMS = [
    "ใช้อินเทอร์เน็ตที่สาขาไม่ได้เลย เพิ่งเป็นเมื่อสักครู่",
    "เครื่องในออฟฟิศเข้าระบบส่วนกลางไม่ได้ทั้งหมด",
    "วงจรที่สาขาใช้งานไม่ได้ ลูกค้าแจ้งว่าหลุดพร้อมกันทั้งชั้น",
    "ระบบที่สาขาใช้งานไม่ได้ ตรวจดูแล้วอุปกรณ์ฝั่งลูกค้าเปิดอยู่ปกติ",
]

CALLERS = [
    "ผู้ดูแลระบบของลูกค้า",
    "เจ้าหน้าที่ประจำสาขา",
    "ผู้ประสานงานฝั่งลูกค้า",
]

DRAFT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'ticket_drafts',
)


def build(site_cfg, disposition=None, when=None):
    """
    สร้างร่างใบแจ้งเหตุจากข้อมูลที่ agent จะมีจริงตอนรับสาย

    disposition รับเข้ามาเพื่อ "บันทึกไว้ในไฟล์ร่างเท่านั้น" ไม่ได้เอาไปแต่งข้อความ
    และไม่ถูกส่งต่อเข้า pipeline วิเคราะห์
    """
    when = when or datetime.now()
    return {
        'created_at': when.isoformat(timespec='seconds'),
        'channel': 'Inbound Call',
        'caller': random.choice(CALLERS),
        'node': site_cfg['node'],
        'interface': site_cfg['core_if'],
        'symptom': random.choice(SYMPTOMS),
        'reported_scope': 'ทั้ง Site',
        'status': 'draft — รอ agent ตรวจสอบและกดเปิด TT',
        '_expected_disposition': disposition,   # ไว้เทียบตอนวัดผล ห้ามใช้ในการวิเคราะห์
    }


def save(draft):
    os.makedirs(DRAFT_DIR, exist_ok=True)
    stamp = draft['created_at'].replace(':', '').replace('-', '')
    path = os.path.join(DRAFT_DIR, f"TT_DRAFT_{draft['node']}_{stamp}.json")
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(draft, fh, ensure_ascii=False, indent=2)
    return path


def show(draft, path=None):
    """พิมพ์ร่างในรูปแบบที่ copy ไปวางในช่องแจ้งเหตุเองบน Dashboard ได้ทันที"""
    print()
    print("  " + "=" * 66)
    print("  📋 ร่างใบแจ้งเหตุ — fault ประเภทนี้ไม่มี syslog ระบบจึงไม่เปิด TT ให้เอง")
    print("  " + "=" * 66)
    print(f"    ช่องทางรับแจ้ง : {draft['channel']}")
    print(f"    เวลาที่รับแจ้ง  : {draft['created_at'].replace('T', ' ')}")
    print(f"    ผู้แจ้ง        : {draft['caller']}")
    print(f"    Site ที่แจ้ง    : {draft['node']}")
    print(f"    Interface      : {draft['interface']}  (ฝั่ง Core)")
    print(f"    อาการที่แจ้ง    : {draft['symptom']}")
    print(f"    ขอบเขต         : {draft['reported_scope']}")
    print("  " + "-" * 66)
    print("    ⚠️  นี่เป็นเพียงร่าง — agent ต้องตรวจสอบแล้วกดเปิด TT เองบน Dashboard")
    print("        (ช่อง 'แจ้งเหตุเอง' → เลือก node และ interface ตามด้านบน)")
    if path:
        print(f"    บันทึกไว้ที่: {os.path.relpath(path)}")
    print("  " + "=" * 66)


def draft_for(site_cfg, disposition):
    """ทางลัดที่ใช้บ่อย — สร้าง บันทึก และแสดงผลในครั้งเดียว"""
    d = build(site_cfg, disposition)
    return d, save(d)
