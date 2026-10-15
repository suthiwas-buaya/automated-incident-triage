r"""
Phase 6 - คลังแม่แบบขั้นตอนปฏิบัติงาน (MOP) สำหรับร่าง PW

ขอบเขตที่ต้องเข้าใจก่อนอ่านโค้ด:
    L1 **ไม่ใช่คนเขียน PW** — L2 เป็นคนเขียนและเป็นคนยื่น
    สิ่งที่ไฟล์นี้ผลิตคือ "ร่าง" ที่ L1 แนบไปให้ L2 พร้อมกับงานที่ส่งต่อ
    เพื่อให้ L2 ไม่ต้องเริ่มจากหน้ากระดาษเปล่า และไม่ต้องกรอกข้อมูลที่ L1 ตรวจมาแล้วซ้ำอีกรอบ
    L2 มีสิทธิ์แก้ทุกบรรทัด และต้องตรวจก่อนยื่นเสมอ

กติกา 3 ข้อที่บังคับไว้ในโค้ด:
    1. ระบบ "เลือก" แม่แบบเท่านั้น ห้ามแต่งขั้นตอนขึ้นเอง
       ขั้นตอนที่เอาไปแตะอุปกรณ์จริงต้องผ่านสายตาคนมาก่อนทั้งหมด
    2. ไม่มีแม่แบบไหนตรง -> ปฏิเสธ ไม่เดา (select() คืน None พร้อมเหตุผล)
       ร่างที่เดาเอาอันตรายกว่าไม่มีร่างเลย เพราะคนอาจเผลอเชื่อ
    3. ทุกแม่แบบต้องมีแผนสำรอง (NoGo) ครบ — ขั้นตอนที่แก้ config แล้วย้อนกลับไม่ได้
       ถือว่าแม่แบบไม่สมบูรณ์ ตรวจด้วย validate_all()

ที่มาของเนื้อหา:
    เรียบเรียงจาก runbook ที่ใช้ทดสอบจริงในแล็บ (docs/plan/Phase5, Phase6)
    ให้เข้ารูปแบบชีท MOP / NoGo Plan / NoGo MOP ของ Runbook Template v5.0.2603
    **ยังไม่ผ่านการตรวจจากทีม L2 ของจริง** — ต้องให้พี่เลี้ยงตรวจก่อนนำไปใช้กับระบบจริง

ตัวแปรในข้อความ:
    {NE}      ชื่ออุปกรณ์ที่จะเข้าไปทำ        {IF}    interface ที่เกี่ยวข้อง
    {MGMT}    ช่องทางที่ใช้เข้าอุปกรณ์         {PEER}  IP ปลายอีกด้านของลิงก์
    {TICKET}  หมายเลข TT ที่เป็นต้นเรื่อง

ทดสอบได้เองไม่ต้องมี lab: py lib\mop_library.py
"""
import re

# NoGo Method ที่ใช้ได้มีแค่ 3 ค่านี้ ตามที่ระบุในชีท NoGo Plan ของแม่แบบจริง
NOGO_METHODS = ('Corrective', 'Rollback', 'Cancel')

TEMPLATES = {
    # -----------------------------------------------------------------------
    'M-01': {
        'title': 'เปิดคืน interface ที่ถูกสั่งปิด (administratively down)',
        'applies_to': 'พอร์ตที่ต่อไปยัง Access Site ถูกสั่ง shutdown ไว้',
        'effect_if_not_done': 'Site ปลายทางยังใช้งานเครือข่ายไม่ได้ทั้ง Site จนกว่าจะเปิดพอร์ตคืน',
        'work_classification': 'Operational Control (Enable / Disable)',
        'activity_catalog': 'Interface Control',
        'steps': [
            ('Implement', 'เข้า {NE} ผ่าน {MGMT} ยืนยันว่า hostname ตรงกับ {NE} ก่อนทำทุกครั้ง'),
            ('Verify', 'บันทึกสถานะก่อนแก้: show ip interface brief | include {IF} '
                       '(คาดว่าเป็น administratively down) และเก็บ '
                       'show running-config interface {IF} ไว้เป็นหลักฐานก่อนเปลี่ยนแปลง'),
            ('NoGo', 'ถ้าเข้าอุปกรณ์ไม่ได้ หรือสถานะไม่ตรงกับที่ระบุไว้ข้างต้น '
                     'ให้หยุดและดำเนินการตาม NoGo MOP#1'),
            ('Implement', 'เข้าโหมดตั้งค่า: configure terminal แล้ว interface {IF}'),
            ('Implement', 'สั่ง no shutdown'),
            ('Implement', 'ออกจากโหมดตั้งค่าและบันทึก: end แล้ว write memory'),
            ('Verify', 'ยืนยันผล 3 อย่าง: (1) show ip interface brief | include {IF} ต้องเป็น up/up '
                       '(2) show interfaces {IF} ตัวนับ input errors ต้องไม่เพิ่มขึ้น '
                       '(3) ping {PEER} repeat 5 ต้องได้ครบ 5/5'),
            ('NoGo', 'ถ้าภายใน 5 นาที ลิงก์ไม่ขึ้น up/up หรือตัวนับ error เพิ่มขึ้น '
                     'ให้ดำเนินการตาม NoGo MOP#2'),
        ],
        'nogo_plan': [
            {'ref': 1, 'criteria': 'เข้าอุปกรณ์ไม่ได้ หรือสถานะของ {IF} ไม่ตรงกับที่ระบุไว้ใน MOP',
             'method': 'Cancel', 'scope': '{NE} {IF}', 'impact': 'ไม่มี (ยังไม่ได้เปลี่ยนแปลงสิ่งใด)',
             'exec_time': '10 นาที'},
            {'ref': 2, 'criteria': 'สั่ง no shutdown แล้วลิงก์ไม่ขึ้น up/up ภายใน 5 นาที '
                                   'หรือตัวนับ input errors เพิ่มขึ้น',
             'method': 'Rollback', 'scope': '{NE} {IF}',
             'impact': '{IF} กลับสู่สถานะเดิม บริการยังไม่คืน', 'exec_time': '15 นาที'},
        ],
        'nogo_mop': {
            1: [('Prepare', 'เก็บหลักฐานสถานะปัจจุบัน: show logging และ show ip interface brief '
                            'แนบเข้า {TICKET}'),
                ('Implement', 'ออกจากโหมดตั้งค่าโดยไม่บันทึกคำสั่งใด (end โดยไม่ write memory)'),
                ('Verify', 'ยืนยันด้วย show running-config interface {IF} ว่าตรงกับที่บันทึกไว้ก่อนเริ่มงาน '
                           'แล้วแจ้งผู้ส่งงานพร้อมเหตุผลที่ยกเลิก')],
            2: [('Prepare', 'เรียกสถานะก่อนเปลี่ยนแปลงที่บันทึกไว้ในขั้นตอนที่ 2 ของ MOP ขึ้นมาเทียบ'),
                ('Implement', 'คืนสถานะเดิม: configure terminal → interface {IF} → shutdown → end → write memory'),
                ('Verify', 'ยืนยันว่าสถานะกลับเป็น administratively down ตรงกับก่อนเริ่มงาน '
                           'แล้วอัปเดต {TICKET} ว่าต้องส่งช่างตรวจสอบทางกายภาพต่อ')],
        },
    },
    # -----------------------------------------------------------------------
    'M-02': {
        'title': 'เปิดคืน Loopback ของอุปกรณ์แกนกลางที่ถูกสั่งปิด',
        'applies_to': 'Loopback ของ Core ถูกสั่ง shutdown ทำให้เส้นทางในเครือข่ายเสียหาย',
        'effect_if_not_done': 'เส้นทางในเครือข่ายยังไม่คืน กระทบการเชื่อมต่อในวงกว้างกว่าหนึ่ง Site',
        'work_classification': 'Operational Control (Enable / Disable)',
        'activity_catalog': 'Interface Control',
        'steps': [
            ('Implement', 'เข้า {NE} ผ่าน {MGMT} ยืนยัน hostname ก่อนทำทุกครั้ง'),
            ('Verify', 'บันทึกสถานะก่อนแก้: show ip interface brief | include {IF} '
                       'และ show ip route summary เก็บจำนวนเส้นทางไว้เทียบหลังแก้'),
            ('NoGo', 'ถ้าเข้าอุปกรณ์ไม่ได้ หรือสถานะไม่ตรงกับที่ระบุไว้ ให้หยุดและทำตาม NoGo MOP#1'),
            ('Implement', 'configure terminal แล้ว interface {IF}'),
            ('Implement', 'สั่ง no shutdown'),
            ('Implement', 'end แล้ว write memory'),
            ('Verify', 'ยืนยันผล: {IF} เป็น up/up · ping {PEER} ได้ครบ · '
                       'จำนวนเส้นทางใน show ip route summary กลับมาเท่ากับก่อนเกิดเหตุ'),
            ('NoGo', 'ถ้าเส้นทางไม่กลับคืนภายใน 10 นาที ให้ดำเนินการตาม NoGo MOP#2'),
        ],
        'nogo_plan': [
            {'ref': 1, 'criteria': 'เข้าอุปกรณ์ไม่ได้ หรือสถานะของ {IF} ไม่ตรงกับที่ระบุไว้ใน MOP',
             'method': 'Cancel', 'scope': '{NE} {IF}', 'impact': 'ไม่มี (ยังไม่ได้เปลี่ยนแปลงสิ่งใด)',
             'exec_time': '10 นาที'},
            {'ref': 2, 'criteria': 'เปิด {IF} แล้วเส้นทางในเครือข่ายไม่กลับคืนภายใน 10 นาที',
             'method': 'Rollback', 'scope': '{NE} {IF}',
             'impact': 'กลับสู่สถานะเดิม ต้องยกระดับให้ทีมที่ดูแลอุปกรณ์แกนกลางต่อ',
             'exec_time': '15 นาที'},
        ],
        'nogo_mop': {
            1: [('Prepare', 'เก็บ show logging และ show ip interface brief แนบเข้า {TICKET}'),
                ('Implement', 'ออกจากโหมดตั้งค่าโดยไม่บันทึกคำสั่งใด'),
                ('Verify', 'ยืนยันว่า running-config ไม่เปลี่ยน แล้วแจ้งผู้ส่งงานพร้อมเหตุผล')],
            2: [('Prepare', 'เรียกจำนวนเส้นทางและสถานะที่บันทึกไว้ก่อนเริ่มงานขึ้นมาเทียบ'),
                ('Implement', 'คืนสถานะเดิม: configure terminal → interface {IF} → shutdown → end → write memory'),
                ('Verify', 'ยืนยันว่าสถานะกลับเป็นเหมือนก่อนเริ่มงาน แล้วยกระดับงานพร้อมแนบหลักฐานทั้งหมด')],
        },
    },
    # -----------------------------------------------------------------------
    'M-03': {
        'title': 'คืนค่าคิวขาเข้าของ interface ที่ถูกตั้งต่ำผิดปกติ',
        'applies_to': 'พอร์ตยังใช้งานได้แต่ทิ้งแพ็กเก็ต เพราะคิวขาเข้าถูกตั้งค่าไว้ต่ำผิดปกติ',
        'effect_if_not_done': 'ลูกค้ายังพบอาการใช้งานช้าและแพ็กเก็ตหายต่อเนื่อง',
        'work_classification': 'Configuration Management',
        'activity_catalog': 'Modify / Delete Configuration',
        'steps': [
            ('Implement', 'เข้า {NE} ผ่าน {MGMT} ยืนยัน hostname ก่อนทำทุกครั้ง'),
            ('Verify', 'บันทึกค่าปัจจุบันก่อนแก้: show running-config interface {IF} (ดูบรรทัด hold-queue) '
                       'และ show interfaces {IF} เก็บตัวเลข input drops ไว้เทียบ'),
            ('NoGo', 'ถ้าเข้าอุปกรณ์ไม่ได้ หรือไม่พบการตั้งค่า hold-queue ที่ผิดปกติ '
                     'ให้หยุดและทำตาม NoGo MOP#1'),
            ('Implement', 'configure terminal แล้ว interface {IF}'),
            ('Implement', 'ตั้งค่าคิวขาเข้ากลับเป็นค่ามาตรฐาน: hold-queue 75 in'),
            ('Implement', 'end แล้ว write memory'),
            ('Verify', 'เฝ้าดู 10 นาที: show interfaces {IF} ตัวเลข input drops ต้องหยุดเพิ่ม '
                       'และ ping {PEER} repeat 20 ต้องไม่มีแพ็กเก็ตหาย'),
            ('NoGo', 'ถ้า input drops ยังเพิ่มขึ้นหลังผ่านไป 10 นาที ให้ดำเนินการตาม NoGo MOP#2'),
        ],
        'nogo_plan': [
            {'ref': 1, 'criteria': 'เข้าอุปกรณ์ไม่ได้ หรือไม่พบการตั้งค่า hold-queue ที่ผิดปกติบน {IF}',
             'method': 'Cancel', 'scope': '{NE} {IF}', 'impact': 'ไม่มี (ยังไม่ได้เปลี่ยนแปลงสิ่งใด)',
             'exec_time': '10 นาที'},
            {'ref': 2, 'criteria': 'คืนค่าคิวแล้ว input drops ยังเพิ่มขึ้นหลังผ่านไป 10 นาที',
             'method': 'Rollback', 'scope': '{NE} {IF}',
             'impact': 'คืนค่าเดิม อาการทิ้งแพ็กเก็ตยังอยู่ ต้องวิเคราะห์สาเหตุอื่นต่อ',
             'exec_time': '15 นาที'},
        ],
        'nogo_mop': {
            1: [('Prepare', 'เก็บ show running-config interface {IF} และ show interfaces {IF} แนบเข้า {TICKET}'),
                ('Implement', 'ออกจากโหมดตั้งค่าโดยไม่บันทึกคำสั่งใด'),
                ('Verify', 'ยืนยันว่า running-config ไม่เปลี่ยน แล้วแจ้งผู้ส่งงานพร้อมเหตุผล')],
            2: [('Prepare', 'เรียกค่า hold-queue เดิมที่บันทึกไว้ในขั้นตอนที่ 2 ของ MOP ขึ้นมา'),
                ('Implement', 'คืนค่าเดิม: configure terminal → interface {IF} → hold-queue <ค่าเดิม> in → end → write memory'),
                ('Verify', 'ยืนยันว่า running-config กลับเป็นเหมือนก่อนเริ่มงาน '
                           'แล้วอัปเดต {TICKET} ว่าต้องวิเคราะห์สาเหตุอื่นต่อ')],
        },
    },
}


# ---------------------------------------------------------------------------
# การเลือกแม่แบบ — เลือกเท่านั้น ห้ามสร้างใหม่
# ---------------------------------------------------------------------------
def select(cause_summary=None, interface=None, evidence=None):
    """
    เลือกแม่แบบที่ตรงกับอาการ — คืน (key, template) หรือ (None, เหตุผลที่เลือกไม่ได้)

    เจตนา: ถ้าไม่มีแม่แบบไหนตรง ต้องปฏิเสธ ไม่ใช่หยิบอันที่ใกล้เคียงที่สุดมาให้
    ร่างที่เดาเอาอันตรายกว่าไม่มีร่างเลย เพราะ L2 อาจเผลอเชื่อว่ามีคนตรวจมาแล้ว
    """
    text = ' '.join(str(x) for x in (cause_summary, evidence) if x).lower()
    itf = (interface or '').lower()

    if 'hold-queue' in text or 'input drops' in text or 'output drops' in text:
        return 'M-03', TEMPLATES['M-03']

    if 'administratively' in text:
        if itf.startswith('loopback'):
            return 'M-02', TEMPLATES['M-02']
        if itf.startswith('ethernet'):
            return 'M-01', TEMPLATES['M-01']
        return None, ('พบว่าพอร์ตถูกสั่งปิด แต่ระบุชนิดของ interface ไม่ได้ '
                      'จึงเลือกแม่แบบไม่ได้ — ต้องให้ผู้ตรวจระบุเอง')

    return None, (f"ไม่มีแม่แบบใดตรงกับอาการนี้ (สาเหตุที่สรุปได้: {cause_summary or 'ไม่ระบุ'}) "
                  f"— ระบบไม่ร่าง PW ให้ ต้องให้ผู้ตรวจพิจารณาเอง")


def fill(text, context):
    """แทนค่าตัวแปรในข้อความ — ตัวแปรที่ไม่มีค่าจะถูกคงไว้ให้เห็นว่ายังต้องกรอก"""
    def sub(match):
        key = match.group(1)
        value = context.get(key)
        return str(value) if value not in (None, '') else '{' + key + '}'
    return re.sub(r'\{(\w+)\}', sub, text or '')


def validate_all():
    """
    ตรวจว่าทุกแม่แบบสมบูรณ์ — ใช้เป็นเทสต์ประจำ ไม่ใช่ตรวจตอนรันจริง

    เงื่อนไขที่ถือว่าไม่สมบูรณ์:
      - ขั้นตอน NoGo ในชีท MOP อ้าง NoGo MOP# ที่ไม่มีอยู่จริง
      - NoGo Method ไม่อยู่ใน 3 ค่าที่แม่แบบจริงอนุญาต
      - มีขั้นตอนที่แก้การตั้งค่า แต่ไม่มีแผน Rollback รองรับ
    """
    problems = []
    for key, tpl in TEMPLATES.items():
        refs_in_steps = set()
        for _, text in tpl['steps']:
            refs_in_steps.update(int(n) for n in re.findall(r'NoGo MOP#(\d+)', text))
        declared = {row['ref'] for row in tpl['nogo_plan']}
        blocks = set(tpl['nogo_mop'])

        for ref in refs_in_steps - declared:
            problems.append(f"{key}: ขั้นตอนอ้าง NoGo MOP#{ref} แต่ไม่มีใน NoGo Plan")
        for ref in refs_in_steps - blocks:
            problems.append(f"{key}: ขั้นตอนอ้าง NoGo MOP#{ref} แต่ไม่มีขั้นตอนรองรับใน NoGo MOP")
        for row in tpl['nogo_plan']:
            if row['method'] not in NOGO_METHODS:
                problems.append(f"{key}: NoGo Method '{row['method']}' ไม่อยู่ใน {NOGO_METHODS}")

        changes_config = any(
            'write memory' in text for kind, text in tpl['steps'] if kind == 'Implement')
        has_rollback = any(row['method'] == 'Rollback' for row in tpl['nogo_plan'])
        if changes_config and not has_rollback:
            problems.append(f"{key}: มีขั้นตอนที่บันทึกการตั้งค่า แต่ไม่มีแผน Rollback")
    return problems


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    problems = validate_all()
    check(not problems, 'ทุกแม่แบบสมบูรณ์' + ('' if not problems else ': ' + '; '.join(problems)))

    k, _ = select('Port administratively shut down', 'Ethernet0/0')
    check(k == 'M-01', 'พอร์ต Ethernet ถูกสั่งปิด -> M-01')
    k, _ = select('Port administratively shut down', 'Loopback0')
    check(k == 'M-02', 'Loopback ถูกสั่งปิด -> M-02')
    k, _ = select('Interface input queue full', 'Ethernet0/1', evidence='hold-queue 1 in')
    check(k == 'M-03', 'คิวขาเข้าเต็ม -> M-03')

    k, why = select('Carrier loss detected', 'Ethernet0/2')
    check(k is None, 'อาการที่ไม่มีแม่แบบ -> ไม่ร่างให้')
    check('ไม่มีแม่แบบ' in why, 'และบอกเหตุผลที่ไม่ร่าง')

    k, why = select(None, None)
    check(k is None, 'ไม่มีข้อมูลเลย -> ไม่ร่างให้')

    ctx = {'NE': 'Core', 'IF': 'Ethernet0/0', 'PEER': '10.0.1.2'}
    out = fill('เข้า {NE} แล้วดู {IF} · ping {PEER} · {MGMT}', ctx)
    check('Core' in out and '10.0.1.2' in out, 'แทนค่าตัวแปรที่มีค่าได้')
    check('{MGMT}' in out, 'ตัวแปรที่ยังไม่มีค่าคงไว้ให้เห็นว่าต้องกรอก')

    for key, tpl in TEMPLATES.items():
        check(len(tpl['steps']) == 8, f'{key} มีขั้นตอนครบ 8 บรรทัดตามชีท MOP (แถว 34-41)')

    print()
    print(f"ผ่าน {passed}/{total}")
    raise SystemExit(0 if passed == total else 1)
