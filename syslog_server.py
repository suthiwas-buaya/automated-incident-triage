"""
Step 5 - Syslog Server ตัวจริง (Phase 1 deliverable)

ประกอบ 3 ส่วนเข้าด้วยกัน:
    รับ syslog UDP 514  ->  syslog_parser.parse()  ->  db.create_ticket() / close_ticket()

วิธีรัน (ปล่อยค้างไว้ตลอด):
    py syslog_server.py

กด Ctrl+C เพื่อหยุด
"""
import re
import socket
from datetime import datetime

from lib import db
from lib import syslog_parser
from lib import classifier
from lib import correlation

HOST = '0.0.0.0'
PORT = 514

# ตรวจจับ timestamp ที่ IOS ทำเครื่องหมาย * ไว้ = "เวลานี้ไม่ยืนยัน" (นาฬิกายังไม่ถูกตั้งหลังรีบูต)
# เช่น "Core: *Aug 12 2026 15:41:31.733: %LINK-3-UPDOWN: ..."
#
# ทำไมต้องมี: SYSLOG_RE ใน lib/syslog_parser.py ไม่รองรับ * ข้อความจึง parse ไม่ผ่าน "ทุกบรรทัด"
# แล้วระบบจะไม่สร้าง TT เลยสักใบ โดยหน้าจอขึ้นข้อความเดียวกับกรณีปกติ (เจอจริง 10 และ 12 ส.ค. 2026)
# ตรงนี้จึงแยกออกมาเตือนให้ชัด — ไม่ได้แก้ parser (ข้อห้าม #1) แค่ทำให้ความล้มเหลว "ดังพอ"
UNSET_CLOCK_RE = re.compile(r':\s+\*\w{3}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}')

_clock_warning_shown = False


def log(msg):
    """print พร้อมเวลาเครื่อง เพื่อดูว่าระบบทำงานถึงไหนแล้ว"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def warn_unset_clock(raw_message):
    """เตือนแบบเห็นชัดครั้งแรกครั้งเดียว ครั้งต่อไปเตือนสั้นๆ พอ ไม่ให้ท่วมหน้าจอ"""
    global _clock_warning_shown
    if _clock_warning_shown:
        log("⚠ ข้ามอีกข้อความ — นาฬิกายังไม่ถูกตั้ง (รัน: py check_clock.py --set)")
        return
    _clock_warning_shown = True
    print(flush=True)
    print("!" * 68, flush=True)
    print("  🔴 นาฬิกาของ Router ยังไม่ถูกตั้ง — ระบบจะไม่สร้าง TT ใดๆ ทั้งสิ้น", flush=True)
    print("!" * 68, flush=True)
    print(f"  ข้อความที่ได้รับ : {raw_message[:70]}", flush=True)
    print("  สังเกตเครื่องหมาย * หน้าเดือน = IOS บอกว่าเวลานี้เชื่อถือไม่ได้", flush=True)
    print("  ระบบใช้เวลาจาก syslog เป็นเวลาเกิดเหตุ จึงไม่รับข้อความที่เวลาไม่ยืนยัน", flush=True)
    print(flush=True)
    print("  วิธีแก้ (ใช้เวลา 10 วินาที) — เปิดอีกหน้าต่างแล้วรัน:", flush=True)
    print("        py check_clock.py --set", flush=True)
    print(flush=True)
    print("  ไม่ต้องปิดหน้าต่างนี้ ตั้งนาฬิกาเสร็จแล้วยิง fault ได้เลย", flush=True)
    print("!" * 68, flush=True)
    print(flush=True)


def handle_message(raw_message, source_ip):
    parsed = syslog_parser.parse(raw_message)

    if parsed is None:
        # แยก 2 กรณีให้ชัด: "อ่านไม่ออกเพราะนาฬิกาไม่ถูกตั้ง" (ระบบใช้งานไม่ได้จริง)
        # กับ "อ่านไม่ออกเพราะรูปแบบแปลก" (เกิดนานๆ ครั้ง ไม่ร้ายแรง)
        # บทเรียนวันที่ 10 ส.ค.: สองกรณีนี้เคยแสดงข้อความเหมือนกัน ทำให้ระบบพังเงียบอยู่หลายนาที
        if UNSET_CLOCK_RE.search(raw_message):
            warn_unset_clock(raw_message)
        else:
            log(f"⚠ parse ไม่ได้ (ข้ามไป): {raw_message[:80]}")
        return

    # กรองด้วยกฎ trigger ที่ confirm ไว้ (LINK-* เท่านั้น, ไม่เอา LINEPROTO/AMDP2/SYS, ไม่เอา E0/3)
    if not syslog_parser.is_trigger_event(parsed):
        log(f"· ignore [{parsed['facility']}-{parsed['severity']}-{parsed['mnemonic']}] จาก {parsed['hostname']}")
        return

    fields = syslog_parser.build_ticket_fields(parsed)

    if fields['state'] == 'down':
        incident_number = db.create_ticket(
            node=fields['node'],
            interface=fields['interface'],
            summary=fields['summary'],
            event_start_time=fields['event_start_time'],
            raw_syslog=fields['raw_syslog'],
        )
        log(f"🔴 เปิด TT {incident_number} | {fields['summary']} | เวลา {fields['event_start_time']}")

        # --- Phase 2: จำแนกทันที (inline) เพราะเป็นแค่ string matching ใช้เวลาไม่ถึงเสี้ยววินาที
        #     นี่คือส่วนที่ทำให้ "Quick Status" ขึ้นได้ใน 2-3 วิตามที่ Phase 4 ต้องการ
        try:
            c = classifier.classify(parsed, fields)
            db.update_classification(
                incident_number=incident_number,
                owner_group=c['owner_group'],
                priority=c['priority'],
                confidence=c['confidence'],
                reason=c['reason'],
            )
            log(f"   ↳ 🏷  {c['owner_group']} | {c['priority']} | มั่นใจ {c['confidence']}%")
            log(f"      เหตุผล: {c['reason']}")
        except Exception as e:
            # จำแนกพลาดไม่ควรทำให้ TT หาย — TT ยังอยู่ครบ แค่ยังไม่มี group
            log(f"   ↳ ⚠ จำแนกไม่สำเร็จ: {e} (TT ยังถูกบันทึกไว้แล้ว)")

        # --- Correlation: หลาย Site ล่มพร้อมกัน = ปัญหาที่ตัว Core ไม่ใช่สายแต่ละเส้น
        try:
            corr = correlation.check_and_correlate()
            if corr:
                log("")
                log(f"   ⚡ พบความเชื่อมโยง — {len(corr['sites'])} Site หลุดพร้อมกัน: {', '.join(corr['sites'])}")
                log(f"      จัดใหม่เป็น {correlation.CORRELATED_GROUP} | {correlation.CORRELATED_PRIORITY} "
                    f"จำนวน {len(corr['correlated'])} ใบ: {', '.join(corr['correlated'])}")
                log(f"      เหตุผล: ต้นเหตุน่าจะอยู่ที่อุปกรณ์แกนกลาง ไม่ใช่สายแต่ละเส้น")
                log("")
        except Exception as e:
            log(f"   ⚠ ตรวจ correlation ไม่สำเร็จ: {e}")

    else:  # state == 'up'
        # บันทึกแค่ว่าสัญญาณกลับมา — ไม่ปิดงานเอง งานจะปิดเมื่อคนทำครบขั้นตอนแล้วเท่านั้น
        incident_number = db.record_recovery(
            node=fields['node'],
            interface=fields['interface'],
            event_end_time=fields['event_start_time'],
        )
        if incident_number:
            log(f"🟢 สัญญาณกลับมาแล้ว | {fields['node']} {fields['interface']} | TT {incident_number}")
            log(f"      (TT ยังไม่ปิด — รอคนกด Acknowledge แล้วทำตามขั้นตอนจนจบ)")
        else:
            # เจอ up โดยไม่มี down มาก่อน (เช่น เพิ่งเปิด server หลัง fault เกิดไปแล้ว)
            log(f"⚠ ได้ event up ของ {fields['node']} {fields['interface']} แต่ไม่มีเหตุค้างอยู่ -> ข้าม")


def main():
    print("=" * 60)
    print("  ระบบดักจับ fault และสร้าง Trouble Ticket อัตโนมัติ")
    print("  เปิดหน้าต่างนี้ค้างไว้ — กด Ctrl+C เพื่อหยุด")
    print("=" * 60)
    print()

    db.init_db()
    log(f"เตรียมฐานข้อมูล {db.DB_PATH} เรียบร้อย")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    log(f"กำลังฟัง syslog UDP {HOST}:{PORT} — พร้อมรับ event แล้ว")
    print()

    try:
        while True:
            data, addr = sock.recvfrom(4096)
            raw_message = data.decode('utf-8', errors='ignore').strip()
            handle_message(raw_message, addr[0])
    except KeyboardInterrupt:
        log("หยุดการทำงาน")
    finally:
        sock.close()


if __name__ == '__main__':
    main()
