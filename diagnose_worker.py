"""
Phase 3 - diagnose_worker.py: process แยกที่คอย SSH ไปวินิจฉัย TT ที่ยังไม่ถูกตรวจ

ทำไมต้องแยก process จาก syslog_server.py: SSH กินเวลาหลายวินาที ถ้าเรียกตอนสร้าง TT
ตรงๆ จะทำให้การสร้าง TT ค้างรอ แล้วพังเกณฑ์ Quick Status ≤ 5 วินาที (ข้อห้าม #2 ของ
docs/BRIEF_Phase3_Analyze.md) — worker ตัวนี้จึงแค่ poll หา TT ที่ diagnose_state ยังว่าง
หรือถูกกด Re-check (diagnose_state='running') แล้ว SSH ไปตรวจแยกต่างหาก ไม่บล็อกใครเลย

วิธีรัน (ปล่อยค้างไว้ตลอด เหมือน syslog_server.py):
    py diagnose_worker.py

กด Ctrl+C เพื่อหยุด
"""
import time
from datetime import datetime

from lib import cause_rules
from lib import db
from lib import diagnostics
from lib import mapping

POLL_SECONDS = 5


def log(msg):
    """print พร้อมเวลาเครื่อง เพื่อดูว่า worker ทำงานถึงไหนแล้ว (เหมือน syslog_server.py)"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def diagnose_ticket(ticket):
    incident_number = ticket['incident_number']
    node = ticket['node']
    interface = ticket['interface']

    log(f"🔍 เริ่มวินิจฉัย {incident_number} | {node} {interface or ''}")
    db.set_diagnose_state(incident_number, 'running')

    result = diagnostics.diagnose_from_best_available_node(node, interface)

    for run in result['runs']:
        db.record_diagnostic_run(
            incident_number, device=run['device'], command=run['command'],
            output=run['output'], success=run['success'], run_by=None,
        )

    # raw_output ต้องไม่ว่าง (ข้อห้าม #4) — diagnostics.py รับประกันไว้แล้วเสมอ
    # (ทั้งกรณีสำเร็จและกรณี unreachable ก็คืนข้อความอธิบายมาให้)
    assert result['raw_output'], "diagnostics.py ต้องไม่คืน raw_output ว่างเปล่า"

    # ใช้ cause_output (ไม่ใช่ raw_output เต็ม) สรุปสาเหตุ — กันคำว่า "unreachable" จากตอน
    # พยายาม SSH เข้า Site ที่ล้มเหลวปนเข้าไปทำให้สรุปสาเหตุผิด ทั้งที่ Core บอกสาเหตุจริงมาแล้ว
    cause = cause_rules.summarize(result['cause_output'])

    if not result['success']:
        diagnose_state = 'unreachable'
    elif cause['cause_source'] == 'undetermined':
        diagnose_state = 'undetermined'
    else:
        diagnose_state = 'done'

    db.update_diagnosis(
        incident_number,
        raw_output=result['raw_output'],
        diagnose_state=diagnose_state,
        cause_summary=cause['cause_summary'],
        cause_source=cause['cause_source'],
        evidence_lines=cause['evidence_lines'],
        llm_status=cause['llm_status'],
    )
    log(f"   ↳ diagnose_state={diagnose_state} | สาเหตุ: {cause['cause_summary'] or '(ยังสรุปไม่ได้)'}")


def run_once():
    """วินิจฉัย TT ที่ค้างอยู่ทั้งหมด ณ ตอนนี้ 1 รอบ — คืนจำนวนใบที่ทำ (ใช้ทั้งจาก main() loop
    และจาก __main__ self-test)
    """
    pending = db.list_undiagnosed_tickets() + db.list_recheck_requested_tickets()
    for ticket in pending:
        try:
            diagnose_ticket(ticket)
        except Exception as e:
            # ห้ามให้ worker พังจน TT ค้าง 'running' ตลอดไป — บันทึก error ลง raw_output ไปเลย
            # (ยังคงเงื่อนไขข้อห้าม #4 ที่ raw_output ห้ามว่าง แม้ตัว worker เองจะพัง)
            log(f"   ⚠ วินิจฉัย {ticket['incident_number']} ไม่สำเร็จ: {e}")
            db.update_diagnosis(
                ticket['incident_number'],
                raw_output=f"Diagnose worker error: {e}",
                diagnose_state='undetermined', cause_source='undetermined', llm_status='pending',
            )
    return len(pending)


def main():
    print("=" * 60)
    print("  Deep Diagnostic worker — SSH ตรวจ TT ที่ยังไม่ถูกวินิจฉัย")
    print("  เปิดหน้าต่างนี้ค้างไว้ — กด Ctrl+C เพื่อหยุด")
    print("=" * 60)
    print()

    db.init_db()
    log(f"เตรียมฐานข้อมูล {db.DB_PATH} เรียบร้อย — poll ทุก {POLL_SECONDS} วินาที")
    log(f"node ที่ตรวจได้: {', '.join(mapping.DEVICE_SSH)}")
    print()

    try:
        while True:
            run_once()
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log("หยุดการทำงาน")


if __name__ == '__main__':
    main()
