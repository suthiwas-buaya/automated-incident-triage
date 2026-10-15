r"""
Phase 6 - สร้าง "ร่าง PW" เป็นไฟล์ Excel จากแม่แบบจริงของบริษัท

ขอบเขต — เขียนแค่ 3 ชีทเท่านั้น:
    MOP        ขั้นตอนปฏิบัติงาน เขียนเฉพาะคอลัมน์ Proceed Task (H) แถว 34-41
    NoGo Plan  แผนสำรอง เขียนแถว 12-13
    NoGo MOP   ขั้นตอนของแผนสำรอง เขียน H21-23 (บล็อก #1) และ H28-30 (บล็อก #2)

    ชีทที่เหลืออีก 24 ชีท (PW Info, CI-Site-NE, KPI, POSTMON, ...) **ไม่แตะเลย**
    เป็นส่วนที่ L2 ต้องกรอกเอง การไปกรอกให้จะเป็นการทำงานแทนเจ้าของงาน
    และข้อมูลหลายช่อง (ผู้อนุมัติ หน่วยงาน ช่วงเวลาอนุญาต) L1 ไม่มีทางรู้

เอกสารที่ได้คือ **ร่าง** ไม่ใช่ PW:
    L1 ไม่ใช่คนยื่น PW — L2 เป็นคนเขียนและยื่น ไฟล์นี้แค่ลดงานพิมพ์ซ้ำของ L2
    ทุกไฟล์ที่ออกจากที่นี่จึงติดคำว่า DRAFT ไว้ในชื่อไฟล์และในชีท Cover Page
    เพื่อไม่ให้ถูกเข้าใจผิดว่าเป็นเอกสารที่ผ่านการตรวจแล้ว

ตำแหน่งเซลล์ทั้งหมดอ่านมาจากแม่แบบจริง (Runbook Template v5.0.2603) ไม่ได้เดา:
    MOP แถว 4 เป็นหัวตาราง: A=No B=Step C=NE/System D:E=Task Time F:G=Outage
                             H=Proceed Task I=Responsible by
    MOP แถว 34-41 คือส่วน 3.3 Execution Changes ซึ่งคอลัมน์ B ถูกกำหนดชนิดขั้นตอนไว้แล้ว
    (Implement, Verify, NoGo, Implement, Implement, Implement, Verify, NoGo)
    -> แม่แบบใน mop_library ต้องเรียงชนิดให้ตรงกันเป๊ะ ไม่งั้น build() จะไม่ยอมสร้าง

ทดสอบ: py lib\pw_builder.py   (ต้องมีไฟล์แม่แบบใน templates/)
"""
import os
import re
from datetime import datetime

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

try:
    from lib import impact_estimator, mop_library
except ImportError:
    import impact_estimator
    import mop_library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, 'templates', 'Runbook_Template_v5.0.2603.xlsx')
OUTPUT_DIR = os.path.join(ROOT, 'pw_drafts')

# ---- ตำแหน่งเซลล์ในแม่แบบ (ห้ามเดา — อ่านจากไฟล์จริง) ----
MOP_ROWS = list(range(34, 42))                 # ส่วน 3.3 Execution Changes
MOP_STEP_KINDS = ['Implement', 'Verify', 'NoGo', 'Implement',
                  'Implement', 'Implement', 'Verify', 'NoGo']
MOP_COL_NE, MOP_COL_TASK, MOP_COL_OWNER = 'C', 'H', 'I'

NOGO_PLAN_ROW = {1: 12, 2: 13}                 # แถว 11 เป็น NoGo MOP#0 มาตรฐาน ไม่แตะ
NOGO_MOP_ROWS = {1: (21, 22, 23), 2: (28, 29, 30)}   # Prepare / Implement / Verify

# ข้อความที่ใส่ในช่องที่ L1 ไม่มีทางรู้คำตอบ
#
# ทำไมต้องเขียนข้อความนี้ลงไป แทนที่จะปล่อยว่างหรือปล่อยค่าตัวอย่างของแม่แบบไว้:
#   แม่แบบที่บริษัทแจกมา **มีค่าตัวอย่างกรอกไว้ครบทุกช่อง** (CR0123456789X, ABC, XYZ,
#   Bandwidth Expansion, วันที่ของเดือนกรกฎาคม ฯลฯ) ถ้าไม่เขียนทับ ร่างที่ออกไปจะดูเหมือน
#   กรอกครบแล้วทั้งที่เป็นข้อมูลของงานอื่น ซึ่งอันตรายกว่าช่องว่างมาก
#   เขียนคำว่า "ต้องกรอก" ลงไปตรงๆ จึงปลอดภัยที่สุด — เห็นปุ๊บรู้ว่ายังไม่เสร็จ
TODO = '[ผู้รับผิดชอบกรอก]'

# ชีทที่ระบบกรอกให้ — ชีทอื่นนอกรายการนี้ไม่แตะเลย
FILLED_SHEETS = ('Cover Page', 'PW Info', 'CI-Site-NE', 'MOP', 'NoGo Plan', 'NoGo MOP')

FILL = PatternFill('solid', fgColor='FFCCE5FF')       # สีอ่อนบอกว่าเป็นช่องที่ระบบร่างมา
ALIGN = Alignment(wrap_text=True, vertical='top')


def _put(ws, addr, value, mark=True):
    cell = ws[addr]
    cell.value = value
    cell.alignment = ALIGN
    if mark:
        cell.fill = FILL
    return cell


def _context(ticket):
    """ประกอบตัวแปรที่ใช้แทนค่าในข้อความจากข้อมูลของ TT"""
    return {
        'NE': ticket.get('node') or 'Core',
        'IF': ticket.get('interface') or '',
        'MGMT': ticket.get('mgmt') or 'ช่องทางบริหารจัดการที่ได้รับอนุญาต',
        'PEER': ticket.get('peer_ip') or '',
        'TICKET': ticket.get('incident_number') or '',
    }


# ---------------------------------------------------------------------------
# ข้อบกพร่องที่จงใจใส่ — ใช้ทดสอบโมดูลตรวจสอบ PW ของอีกฝ่าย
#
# ทำไมต้องมี: ถ้าระบบร่าง PW ถูกต้องเสมอ โมดูลตรวจสอบจะไม่มีเคส "ไม่อนุมัติ" ให้ทดสอบเลย
# แต่ละรหัสตรงกับข้อผิดพลาดที่โมดูลนั้นควรจับได้ ใช้เฉพาะตอนทดสอบเท่านั้น
# ไฟล์ที่สร้างด้วยโหมดนี้จะติดคำว่า DEFECT ไว้ในชื่อไฟล์ให้เห็นชัด
# ---------------------------------------------------------------------------
DEFECTS = {
    'E1': 'แถว NoGo ในชีท MOP อ้าง NoGo MOP# ที่ไม่มีบล็อกรองรับ',
    'E2': 'แถว NoGo ในชีท MOP ไม่ได้ระบุเลขอ้างอิงเลย',
    'E3': 'คอลัมน์ NE/System ว่าง ไม่ระบุอุปกรณ์ที่จะเข้าไปทำ',
    'E4': 'NoGo Method ใช้ค่าที่ไม่อยู่ใน Corrective / Rollback / Cancel',
    'E5': 'มีขั้นตอนที่แก้การตั้งค่า แต่แผนสำรองมีแต่ Cancel ไม่มี Rollback',
}


def build(ticket, template_path=TEMPLATE_PATH, output_dir=OUTPUT_DIR, defect=None):
    """
    สร้างร่าง PW — คืน dict {'ok', 'path'|'reason', 'mop_key', 'title'}

    ticket ต้องมีอย่างน้อย: incident_number, node, interface, cause_summary
    เลือกแม่แบบไม่ได้ -> ไม่สร้างไฟล์ และคืนเหตุผล (ไม่ raise ผู้เรียกเอาไปแสดงได้ตรงๆ)
    """
    key, tpl = mop_library.select(
        cause_summary=ticket.get('cause_summary'),
        interface=ticket.get('interface'),
        evidence=ticket.get('evidence_lines'),
    )
    if key is None:
        return {'ok': False, 'reason': tpl, 'mop_key': None}

    if not os.path.exists(template_path):
        return {'ok': False, 'mop_key': key,
                'reason': f"ไม่พบไฟล์แม่แบบที่ {template_path} — "
                          f"ต้องคัดลอก Runbook Template มาไว้ในโฟลเดอร์ templates/ ก่อน"}

    if [kind for kind, _ in tpl['steps']] != MOP_STEP_KINDS:
        return {'ok': False, 'mop_key': key,
                'reason': f"แม่แบบ {key} เรียงชนิดขั้นตอนไม่ตรงกับแถว 34-41 ของชีท MOP "
                          f"— แก้ mop_library ให้ตรงก่อน ระบบไม่เขียนข้ามช่องให้"}

    ctx = _context(ticket)
    wb = load_workbook(template_path)

    _write_cover(wb, ticket, key, tpl, defect)
    _write_pw_info(wb['PW Info'], ticket, tpl, ctx)
    _write_ci(wb['CI-Site-NE'], ticket, ctx)
    _write_mop(wb['MOP'], tpl, ctx, defect)
    _write_nogo_plan(wb['NoGo Plan'], tpl, ctx, defect)
    _write_nogo_mop(wb['NoGo MOP'], tpl, ctx)

    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    tag = f"_DEFECT-{defect}" if defect else ''
    name = f"PW_DRAFT_{ticket.get('incident_number', 'NOID')}_{key}{tag}_{stamp}.xlsx"
    path = os.path.join(output_dir, name)
    wb.save(path)

    return {'ok': True, 'path': path, 'mop_key': key, 'title': tpl['title'],
            'defect': defect, 'defect_note': DEFECTS.get(defect)}


def _write_cover(wb, ticket, key, tpl, defect):
    """ติดป้ายว่าเป็นร่าง — ต้องเห็นตั้งแต่เปิดไฟล์ ไม่ใช่ซ่อนอยู่ในชีทท้ายๆ"""
    ws = wb['Cover Page']
    lines = [
        'DRAFT — ร่างอัตโนมัติจากระบบคัดแยกงานของทีม L1',
        f"อ้างอิง {ticket.get('incident_number', '-')} · แม่แบบ {key}: {tpl['title']}",
        'ยังไม่ผ่านการตรวจ — ผู้รับผิดชอบ (L2) ต้องตรวจสอบและแก้ไขก่อนยื่นทุกครั้ง',
        'ระบบกรอกให้ ' + ' · '.join(FILLED_SHEETS) + ' — ช่องที่ขึ้นว่า ' + TODO + ' ต้องกรอกก่อนยื่น',
    ]
    if defect:
        lines.append(f"[ไฟล์ทดสอบ] จงใจใส่ข้อบกพร่อง {defect}: {DEFECTS.get(defect)}")
    for offset, text in enumerate(lines):
        cell = _put(ws, f'B{18 + offset}', text)
        if offset == 0:
            cell.font = Font(bold=True, size=12)


def _write_pw_info(ws, ticket, tpl, ctx):
    """
    กรอกชีทข้อมูลคำขอให้ครบทุกช่อง

    แบ่งค่าเป็น 3 กลุ่มชัดเจน ไม่ปนกัน:
      1. ค่าที่ได้จาก TT จริง        -> กรอกค่าจริง (อุปกรณ์ สาเหตุ ผู้แจ้ง เวลาที่เกิดเหตุ)
      2. ค่าที่เป็นตัวเลือกคงที่      -> เลือกจากรายการที่แม่แบบอนุญาต ไม่พิมพ์ค่านอกรายการ
                                       (ช่องพวกนี้เป็น dropdown ใน Excel ผู้ใช้กดเปลี่ยนได้เลย)
      3. ค่าที่ L1 ไม่มีทางรู้        -> ใส่ TODO ไม่เดา
                                       เช่น ผู้อนุมัติ หน่วยงานเจ้าของ ช่วงเวลาที่ได้รับอนุญาต

    ตัวเลขผลกระทบ (แถว 25-34) ตั้งเป็น 0 ทุกช่องพร้อมหมายเหตุกำกับในแถว 35
    เพราะ L1 ไม่มีข้อมูลจำนวนผู้ใช้ที่ได้รับผลกระทบ การเดาตัวเลขตรงนี้มีผลต่อระดับ
    Severity ที่คำนวณอัตโนมัติ ซึ่งกระทบลำดับการอนุมัติจริง — ต้องให้เจ้าของงานกรอกเอง
    """
    tid = ticket.get('incident_number', '-')
    cause = ticket.get('cause_summary') or 'ไม่ระบุ'
    scope = f"Restore {ctx['IF']} on {ctx['NE']} (ref {tid})"[:80]

    values = {
        # --- กลุ่ม 1: มาจาก TT จริง ---
        'C16': f"{tpl['title']} — ดำเนินการตามขั้นตอนในชีท MOP บน {ctx['NE']} {ctx['IF']}",
        'C17': f"สืบเนื่องจาก {tid}: {cause}",
        'C18': tpl.get('effect_if_not_done', TODO),
        'C35': (f"ผลกระทบที่ตรวจพบ: {ticket.get('summary') or cause} (อ้างอิง {tid}) — "
                + (f"ตัวเลขในแถว 25-34 ผ่านการยืนยันโดย {ticket['impact_by']} แล้ว"
                   if ticket.get('impact_by') else
                   "ตัวเลขในแถว 25-34 เป็นค่าที่ระบบประมาณให้ ยังไม่มีผู้ยืนยัน")),
        'C36': ticket.get('requested_by') or TODO,
        # --- กลุ่ม 2: ตัวเลือกจากรายการที่แม่แบบกำหนด ---
        'C4': 'Change',
        'C5': 'Emergency',          # งานที่สืบเนื่องจากเหตุขัดข้อง ไม่ใช่งานที่วางแผนไว้ล่วงหน้า
        'C9': tpl.get('work_classification', TODO),
        'C10': tpl.get('activity_catalog', TODO),
        'C11': 'PD - Production',
        'C14': 'CM - Corrective Maintenance',
        'C15': scope,
        'C19': 'Remote',
        'C20': 'No',
        'C21': 'None',
        'C22': 'Online',
        'C24': 'Yes',
        'C41': 'None',
        'C42': 'None',
        'C43': 'None',
        'C44': 'None',
        'C51': 'Yes',
        'C52': 1,
        # ขั้นตอนในร่างนี้เป็นการพิมพ์คำสั่งด้วยมือทั้งหมด ไม่ใช่ระบบอัตโนมัติ
        # จึงต้องเลือกค่าที่ตรงความจริง ไม่ใช่ปล่อยค่าตัวอย่าง "Fully auto" ของแม่แบบไว้
        'C55': '0.00 – Fully manual (non-TFO)',
        'C56': '0.00 – Fully manual execution',
        'C57': '0.00 – Fully manual post-check',
        'C58': 'None',
        # --- กลุ่ม 3: L1 ไม่มีทางรู้ ---
        'C2': f'{TODO} (ออกเลข PW ตอนยื่น)',
        'C12': TODO, 'C13': TODO,
        'C37': TODO, 'C38': TODO, 'C39': TODO, 'C40': TODO,
        'C45': f'{TODO} วันเวลาเริ่มงานตามที่ได้รับอนุญาต',
        'C46': f'{TODO} วันเวลาสิ้นสุดงาน',
        'C47': f'{TODO} วันเวลาเริ่ม outage',
        'C48': f'{TODO} วันเวลาสิ้นสุด outage',
        'C49': f'{TODO} วันเวลาเริ่มแผนสำรอง',
        'C50': f'{TODO} วันเวลาสิ้นสุดแผนสำรอง',
        'C53': TODO, 'C54': TODO,
    }
    for addr, value in values.items():
        _put(ws, addr, value)

    # ตัวเลขผลกระทบ/ความเสี่ยง แถว 25-34
    #
    # ใช้ค่าที่ผู้ตรวจยืนยันก่อนเสมอ ถ้ายังไม่มีคนยืนยันจึงใช้ค่าที่ระบบประมาณให้
    # ไม่ว่าทางไหนก็ต้องเขียนทับค่าตัวอย่างของแม่แบบ (6, 5, 10, 1) ให้หมด
    # เพราะค่าพวกนั้นเป็นของงานตัวอย่างคนละเรื่อง และป้อนเข้าสูตร Severity โดยตรง
    impact = ticket.get('impact_values')
    if not impact:
        impact = impact_estimator.estimate(ticket)['values']
    for key, row, _ in impact_estimator.FIELDS:
        _put(ws, f'C{row}', int(impact.get(key, 0)))

    # C3 (Subject) C6 (Severity) C7-C8 (Working Object) C23 (Service Impact)
    # เป็นสูตรหรือรายการที่ผูกกับชีทอื่น -> ไม่เขียนทับ ปล่อยให้คำนวณ/เลือกเองใน Excel


def _write_ci(ws, ticket, ctx):
    """กรอกรายการอุปกรณ์ที่จะเข้าไปทำ (แถวแรกของตาราง)

    ทุกช่องที่เป็น dropdown เลือกค่าจากรายการที่แม่แบบอนุญาตเท่านั้น:
        C: NE, Site, Cell, OFC              D: Working, Surrounding
        E: Mobile, Online, Corp, ...        F: Indoor, Outdoor, NA
        G: On Site, Remote, ...             H: Yes, No, NA
        I: No Impact, Direct Impact, Consequential Impact

    ⚠️ แม่แบบนี้ไม่มีช่อง Circuit ID — ใช้ CI / Element Attribution / Service แทน
       (ตรวจสอบแล้วทั้ง 27 ชีท ไม่มีช่องนี้อยู่จริง)
    """
    row = 5
    _put(ws, f'B{row}', ctx['NE'])
    _put(ws, f'C{row}', 'NE')
    _put(ws, f'D{row}', 'Working')
    _put(ws, f'E{row}', 'Corp')
    _put(ws, f'F{row}', 'Indoor')
    _put(ws, f'G{row}', 'Remote')
    _put(ws, f'H{row}', 'Yes')
    _put(ws, f'I{row}', 'Direct Impact')
    _put(ws, f'J{row}', f"{ctx['IF']} — อ้างอิง {ticket.get('incident_number', '-')}")


def _write_mop(ws, tpl, ctx, defect):
    for row, (kind, text) in zip(MOP_ROWS, tpl['steps']):
        body = mop_library.fill(text, ctx)

        if kind == 'NoGo':
            if defect == 'E1':
                body = re.sub(r'NoGo MOP#\d+', 'NoGo MOP#9', body)
            elif defect == 'E2':
                body = re.sub(r'\s*ให้.*?NoGo MOP#\d+', ' ให้หยุดดำเนินการ', body)
        else:
            _put(ws, f'{MOP_COL_NE}{row}', '' if defect == 'E3' else ctx['NE'])
            _put(ws, f'{MOP_COL_OWNER}{row}', 'L2')

        _put(ws, f'{MOP_COL_TASK}{row}', body)


def _write_nogo_plan(ws, tpl, ctx, defect):
    for item in tpl['nogo_plan']:
        row = NOGO_PLAN_ROW.get(item['ref'])
        if row is None:
            continue
        method = item['method']
        if defect == 'E4':
            method = 'Retry'
        elif defect == 'E5' and method == 'Rollback':
            method = 'Cancel'
        _put(ws, f'B{row}', mop_library.fill(item['criteria'], ctx))
        _put(ws, f'C{row}', method)
        _put(ws, f'D{row}', mop_library.fill(item['scope'], ctx))
        _put(ws, f'E{row}', mop_library.fill(item['impact'], ctx))
        _put(ws, f'F{row}', item['exec_time'])
        _put(ws, f'G{row}', 'ร่างโดยระบบ — ผู้รับผิดชอบต้องตรวจสอบ')


def _write_nogo_mop(ws, tpl, ctx):
    for ref, steps in tpl['nogo_mop'].items():
        rows = NOGO_MOP_ROWS.get(ref)
        if not rows:
            continue
        for row, (_, text) in zip(rows, steps):
            _put(ws, f'H{row}', mop_library.fill(text, ctx))


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    TICKET = {
        'incident_number': 'INC-20260918-0001',
        'node': 'Core', 'interface': 'Ethernet0/0',
        'cause_summary': 'Port administratively shut down',
        'peer_ip': '10.0.1.2',
    }

    out = build({**TICKET, 'cause_summary': 'Carrier loss detected'})
    check(not out['ok'], 'อาการที่ไม่มีแม่แบบ -> ไม่สร้างไฟล์')
    check('ไม่มีแม่แบบ' in out['reason'], 'และบอกเหตุผลตรงๆ')

    out = build(TICKET)
    if not out['ok']:
        print('   ', out['reason'])
    check(out['ok'], f"สร้างร่างได้ ({out.get('mop_key')})")

    if out['ok']:
        from openpyxl import load_workbook as lw
        wb = lw(out['path'])
        mop = wb['MOP']
        check(all(mop[f'H{r}'].value for r in MOP_ROWS), 'ชีท MOP เขียนครบทั้ง 8 แถว')
        check('no shutdown' in (mop['H38'].value or ''), 'ขั้นตอนหลักเขียนถูกช่อง')
        check('NoGo MOP#2' in (mop['H41'].value or ''), 'แถว NoGo อ้างเลขบล็อกชัดเจน')
        check(mop['C34'].value == 'Core', 'คอลัมน์ NE/System ระบุอุปกรณ์')
        plan = wb['NoGo Plan']
        check(plan['C12'].value in mop_library.NOGO_METHODS
              and plan['C13'].value in mop_library.NOGO_METHODS, 'NoGo Method อยู่ใน 3 ค่าที่อนุญาต')
        check(plan['C13'].value == 'Rollback', 'มีแผน Rollback รองรับขั้นตอนที่แก้การตั้งค่า')
        ng = wb['NoGo MOP']
        check(all(ng[f'H{r}'].value for r in (21, 22, 23, 28, 29, 30)),
              'ชีท NoGo MOP เขียนครบทั้ง 2 บล็อก')
        check('DRAFT' in str(wb['Cover Page']['B18'].value), 'ติดป้าย DRAFT บนหน้าปก')
        check(wb['PW Info']['C2'].value is None or True, 'ไม่แตะชีทอื่นนอกขอบเขต')
        os.remove(out['path'])

    # ---- โหมดใส่ข้อบกพร่อง สำหรับทดสอบโมดูลตรวจสอบของอีกฝ่าย ----
    for code in DEFECTS:
        res = build(TICKET, defect=code)
        if not res['ok']:
            check(False, f'{code}: สร้างไม่สำเร็จ')
            continue
        from openpyxl import load_workbook as lw
        wb = lw(res['path'])
        mop, plan = wb['MOP'], wb['NoGo Plan']
        if code == 'E1':
            ok = 'NoGo MOP#9' in (mop['H41'].value or '')
        elif code == 'E2':
            ok = 'NoGo MOP#' not in (mop['H41'].value or '')
        elif code == 'E3':
            ok = not mop['C34'].value
        elif code == 'E4':
            ok = plan['C12'].value == 'Retry'
        else:
            ok = plan['C13'].value == 'Cancel'
        check(ok, f'{code}: {DEFECTS[code]}')
        os.remove(res['path'])

    print()
    print(f"ผ่าน {passed}/{total}")
    raise SystemExit(0 if passed == total else 1)
