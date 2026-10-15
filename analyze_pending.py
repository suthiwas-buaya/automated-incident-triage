r"""
Phase 3.5 - analyze_pending.py: เอา TT ที่กฎสรุปสาเหตุไม่ได้ มาวิเคราะห์ด้วยแบบจำลองภาษา

ตำแหน่งในสถาปัตยกรรม 2 ช่วง (docs/Plan.md 3.5.2) — RAM 15.6 GB รัน CML กับ LLM พร้อมกันไม่ได้:

    ช่วงที่ 1 (เปิด CML)   syslog_server.py + diagnose_worker.py -> เก็บ TT + ผล show command
                          -> ปิด CML
    ช่วงที่ 2 (ปิด CML)    ** ไฟล์นี้ ** -> อ่านจาก tickets.db -> วิเคราะห์ -> เขียนกลับ
                          -> เปิด Dashboard ดูผล

ยังเป็น End-to-End เพราะข้อมูลไหลต่อกันผ่านฐานข้อมูลจริง ไม่ได้ป้อนมือ

⚠️ ต่างจาก diagnose_worker.py ตรงที่ **รันครั้งเดียวจบ ไม่ใช่ loop ค้างไว้** เพราะช่วงนี้
   CML ปิดอยู่ ไม่มี TT ใหม่เข้ามาระหว่างที่รันอยู่แล้ว

วิธีรัน (เปิด Ollama · แนะนำให้ปิด CML ก่อนถ้า RAM ตึง):
    py analyze_pending.py              วิเคราะห์คิวที่ค้างอยู่ตอนนี้แล้วจบ
    py analyze_pending.py --dry-run    ดูผลอย่างเดียว ไม่แตะ DB เลย
    py analyze_pending.py --watch      เฝ้าคิวไปเรื่อยๆ เจอ TT ใหม่เมื่อไหร่วิเคราะห์ทันที
    py analyze_pending.py --watch --interval 15    เปลี่ยนรอบการตรวจเป็น 15 วินาที

⚠️ โหมด --watch เพิ่มเข้ามาภายหลัง (14 ส.ค. 2026) หลังพบว่า CML กับ Ollama
   เปิดพร้อมกันได้จริง ไม่ตรงกับสมมติฐานตอนออกแบบสถาปัตยกรรม 2 ช่วง
   -> โหมดรันครั้งเดียวยังเป็นค่าเริ่มต้นและยังใช้ได้เหมือนเดิมทุกประการ
   -> --watch เหมาะกับตอนสาธิต เพราะไม่ต้องสลับหน้าต่างไปกดเอง
   -> ข้อแลกเปลี่ยน: โมเดลจะถูกเรียกซ้ำเรื่อยๆ และค้างอยู่ในหน่วยความจำ
      ซึ่งแย่ง RAM กับ CML — ถ้าเครื่องเริ่มอืดให้กลับไปใช้โหมดรันครั้งเดียว
"""
import sys
import time
from datetime import datetime

from lib import analyzer, db, llm_client


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def analyze_ticket(ticket, dry_run=False):
    """วิเคราะห์ TT หนึ่งใบ — คืน dict ผลลัพธ์จาก analyzer"""
    incident = ticket['incident_number']
    raw_output = ticket['raw_output']

    # ⚠️ ต้องกู้ cause_output ก่อนเสมอ — DB เก็บแค่ raw_output ที่รวมข้อความ SSH ล้มเหลวไว้ด้วย
    # ถ้าส่งทั้งก้อนเข้าไป คำว่า "unreachable" จะทำให้สรุปสาเหตุผิด (บั๊ก #2 วันที่ 6 ส.ค. 2026)
    cause_output = analyzer.recover_cause_output(raw_output)

    result = analyzer.analyze(
        raw_output=raw_output,
        cause_output=cause_output,
        node=ticket['node'],
        interface=ticket['interface'],
        summary=ticket['summary'],
    )

    if not dry_run:
        # ใช้ update_analysis() ไม่ใช่ update_diagnosis() — ฟังก์ชันนั้นไม่มีช่องให้เขียน
        # raw_output / status / owner_group / priority จึงเป็นไปไม่ได้ที่ชั้นนี้จะไปแตะของเดิม
        db.update_analysis(
            incident,
            cause_summary=result['cause_summary'],
            cause_source=result['cause_source'],
            evidence_lines=result['evidence_lines'],
            llm_status=result['llm_status'],
            llm_explanation=result['llm_explanation'],
            llm_note=result['llm_note'],
        )

    return result


def report(incident, result):
    """แสดงผลของ TT หนึ่งใบให้เห็นว่าเกิดอะไรขึ้น และถ้าถูกปฏิเสธ ปฏิเสธเพราะอะไร"""
    source = result['cause_source']
    if source == 'llm':
        log(f"  ✅ {incident} สรุปได้: {result['cause_summary']}")
        log(f"     หลักฐาน: {(result['evidence_lines'] or '').splitlines()[:1]}")
    elif source == 'rule':
        log(f"  ✅ {incident} กฎจับได้ในรอบนี้: {result['cause_summary']}")
    elif result['llm_note']:
        log(f"  ⛔ {incident} ปฏิเสธคำตอบของโมเดล — {result['llm_note']}")
    else:
        log(f"  ⏳ {incident} ยังสรุปไม่ได้ (โมเดลตอบว่าข้อมูลไม่พอ หรือเรียกไม่ติด) คงสถานะรอไว้")


def run_once(dry_run=False, quiet_when_empty=False):
    """วิเคราะห์คิวที่ค้างอยู่ ณ ขณะนี้จนหมด — คืนจำนวนใบที่วิเคราะห์ไป

    แยกออกมาจาก main() เพื่อให้โหมด --watch เรียกซ้ำได้โดยไม่ต้องคัดลอก logic
    """
    tickets = db.list_pending_llm_tickets()
    if not tickets:
        if not quiet_when_empty:
            log("ไม่มี TT ที่รอวิเคราะห์ (llm_status='pending') — ไม่มีอะไรต้องทำ")
            print()
            print("   หมายเหตุ: คิวนี้จะมี TT ก็ต่อเมื่อชุดกฎใน lib/cause_rules.py สรุปสาเหตุไม่ได้")
            print("   ถ้าอยากทดสอบ ให้ยิง fault ที่กฎยังไม่ครอบคลุม แล้วรอ diagnose_worker.py ตรวจก่อน")
        return 0

    log(f"พบ {len(tickets)} ใบที่รอวิเคราะห์ | โมเดล {llm_client.MODEL}")
    log("รอบแรกอาจนานถ้าโมเดลยังไม่อยู่ในหน่วยความจำ (cold start ~45 วินาที)")
    print()

    stats = {'llm': 0, 'rule': 0, 'rejected': 0, 'still_pending': 0}

    for ticket in tickets:
        incident = ticket['incident_number']
        log(f"🔍 {incident} | {ticket['node']} {ticket['interface'] or ''}")
        try:
            result = analyze_ticket(ticket, dry_run=dry_run)
        except Exception as e:
            # ห้ามให้ TT ใบเดียวทำให้ทั้งรอบพัง และห้ามเขียนอะไรลง DB เมื่อเกิดข้อผิดพลาด
            log(f"  ⚠ วิเคราะห์ไม่สำเร็จ: {e} (ข้ามใบนี้ ข้อมูลเดิมไม่ถูกแตะต้อง)")
            stats['still_pending'] += 1
            continue

        report(incident, result)
        if result['cause_source'] == 'llm':
            stats['llm'] += 1
        elif result['cause_source'] == 'rule':
            stats['rule'] += 1
        elif result['llm_note']:
            stats['rejected'] += 1
        else:
            stats['still_pending'] += 1

    print()
    print("=" * 68)
    print("  สรุปผล")
    print("=" * 68)
    print(f"  วิเคราะห์ทั้งหมด        : {len(tickets)} ใบ")
    print(f"  โมเดลสรุปได้ + ผ่านการตรวจ : {stats['llm']} ใบ")
    print(f"  กฎจับได้ในรอบนี้         : {stats['rule']} ใบ")
    print(f"  ปฏิเสธคำตอบของโมเดล      : {stats['rejected']} ใบ (ดูเหตุผลที่ช่อง llm_note)")
    print(f"  ยังสรุปไม่ได้ คงสถานะรอ    : {stats['still_pending']} ใบ")

    if dry_run:
        print("\n  ⚠ โหมดทดลอง — ยังไม่ได้เขียนอะไรลงฐานข้อมูล")
        print("    ถ้าผลถูกต้องแล้ว รันใหม่โดยไม่ใส่ --dry-run")
    else:
        print("\n  ✅ เขียนผลลงฐานข้อมูลแล้ว — เปิด Dashboard ดูได้เลย")
        print("     (สาเหตุที่มาจากโมเดลจะมี cause_source='llm' แยกจากที่มาจากกฎ)")

    return len(tickets)


def _arg_value(flag, default):
    """อ่านค่าที่ตามหลัง flag เช่น --interval 15"""
    if flag not in sys.argv:
        return default
    i = sys.argv.index(flag)
    if i + 1 >= len(sys.argv):
        return default
    try:
        return int(sys.argv[i + 1])
    except ValueError:
        return default


def main():
    dry_run = '--dry-run' in sys.argv
    watch = '--watch' in sys.argv
    interval = _arg_value('--interval', 10)

    print("=" * 68)
    print("  Phase 3.5 — วิเคราะห์ TT ที่กฎสรุปสาเหตุไม่ได้ ด้วยแบบจำลองภาษา")
    if dry_run:
        print("  โหมดทดลอง (--dry-run): ดูผลอย่างเดียว ไม่เขียนลงฐานข้อมูล")
    if watch:
        print(f"  โหมดเฝ้าคิว (--watch): ตรวจทุก {interval} วินาที — กด Ctrl+C เพื่อหยุด")
    print("=" * 68)
    print()

    # เช็ค Ollama ก่อนแตะฐานข้อมูล — ถ้าใช้ไม่ได้ต้องจบโดยไม่เปลี่ยนแปลงอะไรเลย
    if not llm_client.is_available():
        log("❌ Ollama ไม่ได้เปิดอยู่ — ไม่มีการแก้ไขฐานข้อมูลใดๆ ทั้งสิ้น")
        print()
        print("   วิธีแก้: เปิดโปรแกรม Ollama แล้วรันใหม่")
        print("   ถ้าเครื่องอืดหรือ RAM ไม่พอ ให้ปิด CML ก่อน (ดู Plan.md 3.5.2)")
        print()
        print("   💡 ระบบยังทำงานได้ปกติโดยไม่มีขั้นนี้ — TT ทุกใบมีผลจากกฎอยู่แล้ว")
        return

    db.init_db()   # เผื่อ DB ยังไม่มีคอลัมน์ของ Phase 3.5 (migration ปลอดภัย เรียกซ้ำได้)

    if not watch:
        run_once(dry_run=dry_run)
        return

    # ---------- โหมดเฝ้าคิว ----------
    log(f"เริ่มเฝ้าคิว — ตรวจทุก {interval} วินาที")
    print("   (เปิดหน้าต่างนี้ค้างไว้ เปิด TT ใหม่เมื่อไหร่ระบบจะวิเคราะห์ให้เอง)")
    print()
    try:
        while True:
            try:
                done = run_once(dry_run=dry_run, quiet_when_empty=True)
                if done:
                    print()
                    log(f"เฝ้าคิวต่อ — ตรวจทุก {interval} วินาที")
                    print()
            except Exception as e:
                # ห้ามให้รอบเดียวที่พังทำให้ตัวเฝ้าคิวตายทั้งตัว
                log(f"⚠ รอบนี้ผิดพลาด: {type(e).__name__}: {e} (จะลองใหม่รอบหน้า)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        log("หยุดเฝ้าคิวแล้ว")


if __name__ == '__main__':
    main()
