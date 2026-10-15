r"""
Phase 6 - ประมาณตัวเลขผลกระทบสำหรับชีท PW Info แถว 25-34 (กฎล้วนๆ ไม่แตะเครือข่าย)

ทำไมต้องประมาณให้ แทนที่จะปล่อยว่าง:
    ตัวเลขสิบช่องนี้ป้อนเข้าสูตรคำนวณ Severity อัตโนมัติของแม่แบบ ซึ่งไปกำหนดลำดับ
    การอนุมัติจริง — ปล่อยว่างหรือใส่ 0 ทิ้งไว้แล้วบอกให้คนไปกรอกเอง เท่ากับผลักภาระ
    ทั้งที่ระบบรู้ขอบเขตของเหตุอยู่แล้วจากข้อมูลใน TT

ทำไมยังต้องให้คนยืนยัน:
    ระบบรู้แค่ "กี่ Site ที่กระทบ" จาก topology ไม่รู้ว่าแต่ละ Site มีผู้ใช้จริงกี่ราย
    ตัวเลขที่เสนอจึงเป็นการนับหน่วยบริการ ไม่ใช่การนับหัวผู้ใช้
    ทุกตัวเลขต้องมาพร้อมเหตุผลว่านับมาจากอะไร ให้คนตรวจแล้วแก้ได้ก่อนลงเอกสารจริง

ทดสอบได้เองไม่ต้องมี lab: py lib\impact_estimator.py
"""
import re

try:
    from lib import mapping
except ImportError:
    import mapping

# ลำดับต้องตรงกับแถว 25-34 ของชีท PW Info เป๊ะ — ห้ามสลับ
FIELDS = [
    ('mobile_site',            25, 'Mobile Site Impact'),
    ('mobile_subscriber',      26, 'Mobile Subscriber Impact'),
    ('b2c_subscriber',         27, 'Consumer (B2C) Subscriber Impact'),
    ('b2b_subscriber',         28, 'Corporate (B2B) Subscriber Impact'),
    ('hq_vip',                 29, 'HQ/VIP Subscriber Impact'),
    ('mobile_site_risk',       30, 'Mobile Site Risk'),
    ('mobile_subscriber_risk', 31, 'Mobile Subscriber Risk'),
    ('b2c_subscriber_risk',    32, 'Consumer (B2C) Subscriber Risk'),
    ('b2b_subscriber_risk',    33, 'Corporate (B2B) Subscriber Risk'),
    ('hq_vip_risk',            34, 'HQ/VIP Subscriber Risk'),
]

KEYS = [key for key, _, _ in FIELDS]
ROW_OF = {key: row for key, row, _ in FIELDS}
LABEL_OF = {key: label for key, _, label in FIELDS}


def affected_sites(node=None, interface=None, classify_reason=None):
    """
    นับจำนวน Site ที่ได้รับผลกระทบจากเหตุนี้ — คืน (จำนวน, เหตุผล)

    ลำดับการตัดสิน:
      1. ข้อความจากชั้นรวมเหตุ (correlation) ถ้าบอกจำนวนไว้แล้ว ใช้ค่านั้น เชื่อถือได้ที่สุด
         เพราะนับจาก TT ที่เกิดขึ้นจริงพร้อมกัน ไม่ใช่การอนุมานจาก topology
      2. เหตุที่ Loopback ของอุปกรณ์แกนกลาง -> กระทบทุก Site ที่ห้อยอยู่ใต้อุปกรณ์นั้น
      3. เหตุที่พอร์ตซึ่งต่อไป Site ใด"Site หนึ่ง -> กระทบ 1 Site
    """
    total_sites = len(mapping.SITE_ADDRESSES)

    if classify_reason:
        match = re.search(r'ตรวจพบ\s*(\d+)\s*Site', classify_reason)
        if match:
            count = min(int(match.group(1)), total_sites)
            return count, f'ชั้นรวมเหตุพบว่ามี {count} Site หลุดพร้อมกัน'

    itf = (interface or '').lower()
    if itf.startswith('loopback'):
        return total_sites, (f'เหตุอยู่ที่ {interface} ของอุปกรณ์แกนกลาง '
                             f'จึงกระทบทุก Site ที่เชื่อมผ่านอุปกรณ์นี้ ({total_sites} Site)')

    if node in mapping.SITE_ADDRESSES:
        return 1, f'เหตุอยู่ที่ {node} กระทบเฉพาะ Site นี้'

    site = mapping.CORE_INTERFACE_TO_SITE_ADDR.get(interface)
    if site:
        return 1, f'พอร์ต {interface} ต่อไปยัง {site} เพียง Site เดียว'

    return 1, 'ไม่ทราบขอบเขตแน่ชัด จึงนับอย่างต่ำที่สุดคือ 1 Site — ต้องให้ผู้ตรวจยืนยัน'


def estimate(ticket):
    """
    เสนอตัวเลขทั้งสิบช่องพร้อมเหตุผลของแต่ละช่อง

    คืน dict: {'values': {key: จำนวน}, 'reasons': {key: เหตุผล}, 'basis': ข้อความรวม,
               'sites': จำนวน Site, 'confidence': 'high'|'low'}

    ข้อสมมติที่เขียนไว้ตรงๆ:
      - วงจรที่ทีมนี้ดูแลเป็นวงจรลูกค้าองค์กร จึงนับเข้าช่อง Corporate (B2B)
        ช่อง Mobile / Consumer / HQ-VIP เป็น 0 เพราะไม่ใช่บริการที่วงจรนี้ให้
      - นับ 1 Site = 1 หน่วยบริการองค์กร ไม่ใช่จำนวนผู้ใช้ปลายทาง
        (ระบบไม่มีข้อมูลจำนวนผู้ใช้ในแต่ละ Site และไม่ควรเดา)
      - ช่อง Risk เท่ากับช่อง Impact เพราะงานทำกับอุปกรณ์ตัวเดียวกับที่กำลังเสียอยู่
        ขอบเขตที่อาจเสียหายเพิ่มจึงไม่เกินขอบเขตที่เสียอยู่แล้ว
    """
    sites, why = affected_sites(
        node=ticket.get('node'),
        interface=ticket.get('interface'),
        classify_reason=ticket.get('classify_reason'),
    )
    uncertain = 'ไม่ทราบขอบเขต' in why

    zero = 'ไม่ใช่บริการที่วงจรนี้ให้ จึงไม่นับ'
    values = {
        'mobile_site': 0, 'mobile_subscriber': 0, 'b2c_subscriber': 0,
        'b2b_subscriber': sites, 'hq_vip': 0,
        'mobile_site_risk': 0, 'mobile_subscriber_risk': 0, 'b2c_subscriber_risk': 0,
        'b2b_subscriber_risk': sites, 'hq_vip_risk': 0,
    }
    reasons = {
        'mobile_site': zero, 'mobile_subscriber': zero, 'b2c_subscriber': zero,
        'hq_vip': zero + ' (ถ้าวงจรนี้เป็นลูกค้า VIP ต้องแก้เอง)',
        'b2b_subscriber': f'{why} — นับเป็น {sites} หน่วยบริการองค์กร',
        'mobile_site_risk': zero, 'mobile_subscriber_risk': zero,
        'b2c_subscriber_risk': zero, 'hq_vip_risk': zero,
        'b2b_subscriber_risk': 'เท่ากับช่องผลกระทบ เพราะงานทำกับอุปกรณ์ตัวเดียวกับที่เสียอยู่',
    }
    return {
        'values': values,
        'reasons': reasons,
        'sites': sites,
        'basis': why,
        'confidence': 'low' if uncertain else 'high',
    }


def sanitize(raw):
    """
    ตรวจค่าที่คนกรอกกลับมา — คืน (values, error)

    รับเฉพาะจำนวนเต็มไม่ติดลบ และเฉพาะคีย์ที่รู้จัก
    ค่าที่ไม่ส่งมาถือว่า 0 (ไม่ใช่ "คงค่าเดิม") เพื่อให้สิ่งที่บันทึกตรงกับสิ่งที่คนเห็นบนจอเสมอ
    """
    if not isinstance(raw, dict):
        return None, 'รูปแบบข้อมูลไม่ถูกต้อง'
    out = {}
    for key in KEYS:
        value = raw.get(key, 0)
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None, f'ช่อง "{LABEL_OF[key]}" ต้องเป็นจำนวนเต็ม'
        if number < 0:
            return None, f'ช่อง "{LABEL_OF[key]}" ติดลบไม่ได้'
        if number > 1000000:
            return None, f'ช่อง "{LABEL_OF[key]}" มากเกินกว่าที่จะเป็นไปได้'
        out[key] = number
    return out, None


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    check([k for k, _, _ in FIELDS] == KEYS, 'ลำดับคีย์ตรงกับที่ประกาศไว้')
    check([row for _, row, _ in FIELDS] == list(range(25, 35)),
          'แถวเรียง 25-34 ตรงกับชีท PW Info')

    r = estimate({'node': 'Core', 'interface': 'Ethernet0/0'})
    check(r['sites'] == 1 and r['values']['b2b_subscriber'] == 1, 'พอร์ตไป 1 Site -> นับ 1')
    check(r['values']['mobile_site'] == 0, 'ช่องที่ไม่ใช่บริการของวงจรนี้เป็น 0')
    check(r['values']['b2b_subscriber_risk'] == r['values']['b2b_subscriber'],
          'ช่อง Risk เท่ากับช่อง Impact')
    check(all(k in r['reasons'] for k in KEYS), 'ทุกช่องมีเหตุผลกำกับ')

    r = estimate({'node': 'Core', 'interface': 'Loopback0'})
    check(r['sites'] == 3, 'Loopback ของอุปกรณ์แกนกลาง -> กระทบทุก Site')

    r = estimate({'node': 'Core', 'interface': 'Ethernet0/1',
                  'classify_reason': 'ตรวจพบ 3 Site หลุดพร้อมกันภายใน 60 วินาที'})
    check(r['sites'] == 3, 'ชั้นรวมเหตุบอกจำนวนมา -> ใช้ค่านั้น')

    r = estimate({'node': 'Access-Site-B'})
    check(r['sites'] == 1, 'เหตุอยู่ที่ Site เดียว -> นับ 1')

    r = estimate({'node': 'ไม่รู้จัก', 'interface': 'Ethernet9/9'})
    check(r['sites'] == 1 and r['confidence'] == 'low',
          'ไม่ทราบขอบเขต -> นับอย่างต่ำและตั้ง confidence ต่ำ')

    v, err = sanitize({k: 2 for k in KEYS})
    check(err is None and v['hq_vip'] == 2, 'รับค่าที่ถูกต้อง')
    v, err = sanitize({'b2b_subscriber': -1})
    check(v is None and 'ติดลบ' in err, 'กันค่าติดลบ')
    v, err = sanitize({'b2b_subscriber': 'สาม'})
    check(v is None and 'จำนวนเต็ม' in err, 'กันค่าที่ไม่ใช่ตัวเลข')
    v, err = sanitize({'b2b_subscriber': 5, 'ช่องแปลกปลอม': 99})
    check(err is None and 'ช่องแปลกปลอม' not in v, 'ทิ้งคีย์ที่ไม่รู้จัก')
    check(sanitize({})[0] == {k: 0 for k in KEYS}, 'ช่องที่ไม่ส่งมาถือเป็น 0')
    v, err = sanitize('ไม่ใช่ dict')
    check(v is None, 'รูปแบบผิด -> ปฏิเสธ')

    print()
    print(f"ผ่าน {passed}/{total}")
    raise SystemExit(0 if passed == total else 1)
