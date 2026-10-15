"""
Step 3 - SQLite schema + db helper สำหรับ Automated Incident Triage

หน้าที่: เก็บ/อ่าน/ปิด TT (Trouble Ticket) ที่เกิดจาก syslog event
ทดสอบได้เองด้วยข้อมูลปลอมท้ายไฟล์นี้ ไม่ต้องมี lab จริง

วิธีรัน (ทดสอบตัวเอง):
    py db.py
"""
import json
import secrets
import sqlite3
from datetime import datetime

DB_PATH = 'tickets.db'


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _now_syslog_format():
    """เวลาปัจจุบันในรูปแบบเดียวกับที่ lib/syslog_parser.py เขียนลง event_start_time
    ('Aug 10 2026 15:57:07.123') — ต้องตรงกับ correlation.TIMESTAMP_FORMAT เป๊ะ

    ใช้เฉพาะช่อง event_start_time/event_end_time เท่านั้น (ช่องอื่นอย่าง classified_at
    ใช้ _now() ตามเดิม เพราะฝั่งอ่านเป็นคนละฟังก์ชันกัน)

    ทำไมต้องมี: correlation.parse_event_time() รับได้แค่รูปแบบนี้รูปแบบเดียว ถ้า TT ที่
    แจ้งด้วยตนเองเขียนเวลาเป็น '2026-08-10 15:57:07' แบบ _now() มันจะ parse ไม่ผ่านแล้ว
    คืน None เงียบๆ ผลคือ TT ใบนั้นหลุดจาก correlation (ไม่ถูกยกเป็น Network-Core P1
    แม้จะเป็น Site ที่ 3 ที่ล่มพร้อมกัน) และหลุดจากค่าเฉลี่ยเวลารับงานใน /api/stats
    (Phase 5 ข้อ C-2)
    """
    return datetime.now().strftime('%b %d %Y %H:%M:%S.%f')[:-3]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# column ที่เพิ่มเข้ามาใน Phase 2 (classification)
# แยกไว้เพื่อให้ migration เพิ่มทีหลังได้โดยไม่ทำข้อมูลเดิมหาย
PHASE2_COLUMNS = {
    'owner_group': 'TEXT',      # ทีมที่ควรรับผิดชอบ เช่น Network-Access
    'priority': 'TEXT',         # P1-P4
    'confidence': 'INTEGER',    # 0-100 ความมั่นใจของการจำแนก
    'classify_reason': 'TEXT',  # เหตุผลที่จำแนกแบบนี้ (อธิบายให้ Agent เข้าใจได้)
    'classified_at': 'TEXT',    # เวลาที่จำแนกเสร็จ
}

# column ที่เพิ่มเข้ามาใน Phase 4 (Dashboard: human-in-the-loop + handover)
# ดู BRIEF_Phase4_Dashboard.md ข้อ 3.3 — pending_handover_to/technician_assigned/
# last_reported_at ไม่ได้อยู่ในตารางของ BRIEF ตรงๆ แต่ต้องมีเพื่อให้ฟีเจอร์ Handover/
# sub-status ที่ Dashboard เดิมทำได้ดีอยู่แล้ว (ข้อ 1) ยังทำงานได้ต่อกับข้อมูลจริงใน DB
PHASE4_COLUMNS = {
    'proposed_group': 'TEXT',       # group ที่ agent (ไม่ใช่ admin) เสนอ รอ admin ยืนยัน
    'confirmed_by': 'TEXT',         # username คนที่ยืนยัน owner_group ล่าสุด
    'confirmed_at': 'TEXT',
    'corrected_group': 'TEXT',      # ถ้าคนแก้ group จากที่ระบบจำแนกไว้ เก็บค่าที่ถูกไว้เทียบ % ความแม่นยำ
    'acknowledged_by': 'TEXT',
    'acknowledged_at': 'TEXT',
    'current_sub_status': 'TEXT',   # assigned/en_route/on_site/working/completed
    'track_token': 'TEXT',          # token สำหรับหน้า track.html สาธารณะ
    'pending_handover_to': 'TEXT',  # username ที่รอรับ handover
    'technician_assigned': 'INTEGER',
    'last_reported_at': 'TEXT',     # ครั้งล่าสุดที่ agent log update ให้ลูกค้า (SLA)
}

# column ที่เพิ่มเข้ามาใน Phase 3 (Deep Diagnostic + หน้า Analyze)
# ดู docs/BRIEF_Phase3_Analyze.md ข้อ 2
PHASE3_COLUMNS = {
    'raw_output': 'TEXT',       # ผลดิบจาก 3 คำสั่ง — ห้ามว่าง (เกณฑ์ข้อ 4)
    'cause_summary': 'TEXT',    # สาเหตุที่สรุปได้
    'cause_source': 'TEXT',     # 'rule' | 'llm' | 'undetermined'
    'evidence_lines': 'TEXT',   # บรรทัดใน raw_output ที่ใช้อ้าง
    'llm_status': 'TEXT',       # 'done' | 'pending' | 'undetermined'
    'diagnosed_at': 'TEXT',     # เวลาที่ตรวจครั้งล่าสุด → UI แสดงเป็น "Last checked"
    'diagnose_state': 'TEXT',   # 'pending' | 'running' | 'done' | 'unreachable' | 'undetermined'
    'reviewed_by': 'TEXT',      # username คนที่กดยืนยันในหน้า Analyze
    'reviewed_at': 'TEXT',
}

# column ที่เพิ่มใน Phase 3.5 (ชั้นวิเคราะห์ด้วยแบบจำลองภาษา)
# ดู docs/Plan.md 3.5.3 — เขียนโดย analyze_pending.py ผ่าน update_diagnosis()
PHASE35_COLUMNS = {
    'llm_explanation': 'TEXT',  # ข้อความเรียบเรียงให้คนอ่าน (prompt B) — ว่างได้ ไม่ใช่ของบังคับ
    'llm_note': 'TEXT',         # เหตุผลที่ปฏิเสธคำตอบของโมเดล (ด่านตรวจ 1 หรือ 2) — ไว้ตรวจย้อนกลับ
    'analyzed_at': 'TEXT',      # เวลาที่ชั้นวิเคราะห์ทำงานล่าสุด (คนละเวลากับ diagnosed_at ที่เป็นเวลา SSH)
}


# column ที่เพิ่มใน Phase 6 (ชั้นตัดสิน "ประเภทงาน" — lib/disposition.py)
#
# ทำไมต้องแยกจาก owner_group ที่มีอยู่แล้ว:
#     owner_group ตอบว่า "ปัญหาอยู่ในพื้นที่ของทีมไหน" (Network-Access / Network-Core / ...)
#     disposition ตอบคนละคำถาม คือ "เป็นงานชนิดไหน ต้องทำอะไรต่อ"
#     Network-Access ไม่ได้บอกว่าต้องส่ง L2 ไปแก้ config หรือส่งช่างไปเปลี่ยนสาย
#     ซึ่งเป็นคนละคนและคนละกระบวนการ — สองแกนนี้ต้องไม่ยุบรวมเป็นค่าเดียว
#
# disposition           = ค่าที่ระบบ "เสนอ"
# confirmed_disposition = ค่าที่คน "ยืนยัน" — เก็บแยกกันเสมอ เพื่อวัดความแม่นยำย้อนหลังได้
#                         และเพื่อให้เห็นชัดว่าระบบไม่เคยตัดสินแทนคน (เกณฑ์ข้อ 2 = 0%)
PHASE6_COLUMNS = {
    'disposition': 'TEXT',              # configuration|physical|customer|nofault|undetermined
    'disposition_route': 'TEXT',        # ข้อความบอกปลายทางให้คนอ่าน
    'disposition_action': 'TEXT',       # draft_pw|dispatch|notify_customer|propose_close|ask_agent
    'disposition_confidence': 'TEXT',   # 'high' | 'low'
    'disposition_reasons': 'TEXT',      # เหตุผลที่สรุปแบบนี้ (บรรทัดละข้อ)
    'disposition_ruled_out': 'TEXT',    # ประเภทที่ตัดออกและเหตุผล (บรรทัดละข้อ)
    'disposition_evidence': 'TEXT',     # บรรทัด log ที่ใช้อ้าง (ถ้ามี)
    'disposition_probe': 'TEXT',        # ผลดิบของ ping/show ที่ใช้ตัดสิน (JSON) — ไว้ตรวจย้อนกลับ
    'disposition_at': 'TEXT',           # เวลาที่ชั้นตัดสินทำงานล่าสุด
    'confirmed_disposition': 'TEXT',    # ค่าที่ agent ยืนยัน (อาจต่างจากที่ระบบเสนอ)
    'disposition_confirmed_by': 'TEXT',
    'disposition_confirmed_at': 'TEXT',
    # ร่าง PW ที่สร้างให้ L2 เมื่อผู้ตรวจยืนยันว่าเป็นงานประเภท configuration
    # เก็บแค่ "ที่อยู่ไฟล์" กับ "แม่แบบที่ใช้" ไม่เก็บเนื้อหาไฟล์ลงฐานข้อมูล
    # เพราะเอกสารตัวจริงต้องเป็นไฟล์ Excel ที่ L2 เปิดแก้ได้ ไม่ใช่ข้อความในฐานข้อมูล
    'pw_draft_path': 'TEXT',
    'pw_draft_mop': 'TEXT',        # รหัสแม่แบบที่เลือกใช้ เช่น M-01
    'pw_draft_note': 'TEXT',       # เหตุผลที่ร่างไม่ได้ (ถ้าร่างไม่ได้)
    'pw_draft_at': 'TEXT',
    # ตัวเลขผลกระทบสำหรับชีท PW Info แถว 25-34
    # แยก "ค่าที่ระบบเสนอ" กับ "ค่าที่คนยืนยัน" เหมือนแกน disposition ด้วยเหตุผลเดียวกัน
    'impact_suggested': 'TEXT',    # JSON — ค่าที่ lib/impact_estimator.py เสนอ พร้อมเหตุผล
    'impact_values': 'TEXT',       # JSON — ค่าที่ผู้ตรวจยืนยัน (อาจแก้จากที่ระบบเสนอ)
    'impact_by': 'TEXT',
    'impact_at': 'TEXT',
}


def init_db():
    """
    สร้าง table ถ้ายังไม่มี + เพิ่ม column ใหม่ถ้าเป็น db เก่าจาก Phase ก่อนหน้า
    เรียกได้ซ้ำๆ ปลอดภัย (idempotent) — ข้อมูลเดิมไม่หาย
    """
    conn = get_connection()

    # syslog_server.py เขียนตลอดเวลาขณะที่ Dashboard API อ่าน — WAL กัน "database is locked"
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            incident_number TEXT PRIMARY KEY,
            node TEXT NOT NULL,
            interface TEXT,
            summary TEXT,
            status TEXT DEFAULT 'Assigned',
            event_start_time TEXT,
            event_end_time TEXT,
            raw_syslog TEXT
        )
    """)

    # --- migration: เพิ่ม column ของ Phase 2/3/4 ถ้ายังไม่มี ---
    existing = {row['name'] for row in conn.execute("PRAGMA table_info(tickets)")}
    for column, coltype in {**PHASE2_COLUMNS, **PHASE4_COLUMNS, **PHASE3_COLUMNS,
                            **PHASE35_COLUMNS, **PHASE6_COLUMNS}.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {column} {coltype}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_number TEXT NOT NULL,
            sub_status TEXT,
            note TEXT,
            event_time TEXT,
            technician_name TEXT,
            technician_contact TEXT,
            created_by TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            team TEXT,
            is_admin INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    # Phase 3 — ประวัติการรันคำสั่งวินิจฉัย (ทั้งที่ระบบรันเองและที่คนสั่งรันเอง)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diagnostic_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_number TEXT NOT NULL,
            device TEXT NOT NULL,
            command TEXT NOT NULL,
            output TEXT,
            success INTEGER,
            run_by TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def update_classification(incident_number, owner_group, priority, confidence, reason):
    """เติมผลการจำแนกลง TT ที่มีอยู่แล้ว (เรียกทันทีหลัง create_ticket)"""
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET owner_group = ?, priority = ?, confidence = ?, "
        "classify_reason = ?, classified_at = ? WHERE incident_number = ?",
        (owner_group, priority, confidence, reason,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), incident_number)
    )
    conn.commit()
    conn.close()


def list_open_site_tickets():
    """TT ของ Access-Site ที่เหตุยังไม่จบ — ใช้ตรวจ correlation (หลาย Site ล่มพร้อมกัน)"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE event_end_time IS NULL AND node LIKE 'Access-Site%' "
        "ORDER BY event_start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_unclassified_tickets():
    """TT ที่ยังไม่ถูกจำแนก — ใช้ตอนอยาก backfill TT เก่าจาก Phase 1"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE owner_group IS NULL ORDER BY event_start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _generate_incident_number(conn, today_str):
    """สร้างเลข INC-YYYYMMDD-#### โดยนับต่อจากใบล่าสุดของวันนั้น"""
    prefix = f"INC-{today_str}-"
    row = conn.execute(
        "SELECT incident_number FROM tickets WHERE incident_number LIKE ? ORDER BY incident_number DESC LIMIT 1",
        (prefix + '%',)
    ).fetchone()
    if row is None:
        seq = 1
    else:
        seq = int(row['incident_number'].split('-')[-1]) + 1
    return f"{prefix}{seq:04d}"


def find_open_ticket(node, interface):
    """
    หา TT ที่ "เหตุยังไม่จบ" ของ node+interface นี้

    ⚠️ ใช้ event_end_time IS NULL เป็นตัวตัดสินหลัก **ไม่ใช่ status**
    เพราะทั้งสองอย่างเป็นคนละเรื่องกัน:
      - event_end_time = สัญญาณกลับมาแล้วหรือยัง   (เครือข่ายเป็นคนบอก)
      - status         = คนทำงานถึงขั้นไหนแล้ว      (คนเป็นคนบอก)

    ⚠️ บั๊กที่เจอจริง 4 ส.ค. 2026 — "TT หลุมดำ": ถ้าเจ้าหน้าที่กด Resolved/Closed
    บน Dashboard ไปแล้ว แต่ event 'up' ยังไม่เคยเข้ามา (เช่น syslog server ปิดอยู่
    ตอนสัญญาณกลับ) event_end_time จะค้างเป็น NULL ตลอดไป ทำให้ TT ใบนั้นดูด
    fault ใหม่ทุกครั้งของ node+interface เดิม ทั้งที่คนถือว่าใบเก่าจบไปแล้ว
    -> ต้องกันด้วยการไม่นับใบที่ปิดงานแล้วเป็น "เหตุยังไม่จบ" อีก
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM tickets WHERE node = ? AND interface = ? AND event_end_time IS NULL "
        "AND status NOT IN ('Resolved', 'Closed') "
        "ORDER BY event_start_time DESC LIMIT 1",
        (node, interface)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_ticket(node, interface, summary, event_start_time, raw_syslog=''):
    """
    สร้าง TT ใหม่ (เรียกตอน parser เจอ event state=down)
    ถ้ามี TT เปิดอยู่แล้วสำหรับ node+interface นี้ -> ไม่สร้างซ้ำ คืนใบเดิมแทน
    คืนค่า: incident_number ของใบที่ได้ (ใหม่หรือเดิม)
    """
    existing = find_open_ticket(node, interface)
    if existing:
        return existing['incident_number']

    conn = get_connection()
    today_str = datetime.now().strftime('%Y%m%d')
    incident_number = _generate_incident_number(conn, today_str)

    conn.execute(
        "INSERT INTO tickets (incident_number, node, interface, summary, status, event_start_time, raw_syslog) "
        "VALUES (?, ?, ?, ?, 'Assigned', ?, ?)",
        (incident_number, node, interface, summary, event_start_time, raw_syslog)
    )
    conn.commit()
    conn.close()
    return incident_number


def record_recovery(node, interface, event_end_time):
    """
    บันทึกว่า "สัญญาณกลับมาแล้ว" (เรียกตอน parser เจอ event state=up)

    ⚠️ **ไม่แตะ status** — ระบบไม่ปิดงานเอง
    สัญญาณกลับมาไม่ได้แปลว่างานเสร็จ ต้องมีคนรับงานและทำตามขั้นตอนจนจบก่อน
    (คนกด Acknowledge -> In Progress, ทำครบขั้นตอน -> Resolved -> Closed)

    คืนค่า: incident_number ที่บันทึก หรือ None ถ้าไม่เจอเหตุที่ค้างอยู่
    """
    existing = find_open_ticket(node, interface)
    if not existing:
        return None

    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET event_end_time = ? WHERE incident_number = ?",
        (event_end_time, existing['incident_number'])
    )
    conn.commit()
    conn.close()
    return existing['incident_number']


# ชื่อเดิมจาก Phase 1 — เก็บไว้ให้โค้ดเก่าเรียกได้ แต่พฤติกรรมเปลี่ยนแล้ว (ไม่ปิดงานเอง)
close_ticket = record_recovery


def set_manual_event_end(incident_number, event_end_time):
    """บันทึกเวลาที่เหตุจบ "จากการกรอกของคน" สำหรับ TT ที่แจ้งด้วยตนเอง (Phase 5 ข้อ C-5)

    ทำไมต้องมีแยกจาก record_recovery(): record_recovery() ค้นหา ticket จาก node+interface
    ที่ syslog รายงานมา แต่ TT ที่คนแจ้งเองไม่มี syslog 'up' ตามมาเลย จึงไม่มีวันถูกเรียก
    ผลคือ event_end_time เป็น NULL ตลอดไป และไปติดด่านกันปิดงานใน /sub-status ที่บังคับว่า
    ต้องมี event_end_time ก่อนถึงจะกด Resolved ได้ = **TT แจ้งเองปิดงานไม่ได้เลย**

    ⚠️ ค่าที่ได้เป็นข้อมูลจากความจำของคน ไม่ใช่จากอุปกรณ์ — คนละชั้นคุณภาพกับ
    record_recovery() ที่มาจาก syslog จริง ต้องระบุข้อจำกัดนี้ในเล่ม (บท Out of Scope)

    ไม่แตะ status เหมือน record_recovery() — คนยังต้องเดินขั้นตอนปิดงานเองอยู่ดี
    """
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET event_end_time = ? WHERE incident_number = ?",
        (event_end_time, incident_number)
    )
    conn.commit()
    conn.close()


def clear_all_tickets():
    """ลบ TT ทั้งหมด (ไม่แตะตาราง users) — ใช้ตอนอยากเริ่มทดสอบใหม่จากศูนย์"""
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS c FROM tickets").fetchone()['c']
    conn.execute("DELETE FROM tickets")
    for table in ('ticket_events',):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass   # ตารางยังไม่ถูกสร้าง (ยังไม่ได้รัน Dashboard) — ข้ามไป
    conn.commit()
    conn.close()
    return n


def list_all_tickets():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tickets ORDER BY event_start_time DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Phase 4 — Dashboard: human-in-the-loop, handover, manual entry, users
# (ดู BRIEF_Phase4_Dashboard.md) เรียกจาก ProjectxkmuttcTT/backend/app.py เท่านั้น
# ---------------------------------------------------------------------------

def get_ticket(incident_number):
    conn = get_connection()
    row = conn.execute("SELECT * FROM tickets WHERE incident_number = ?", (incident_number,)).fetchone()
    conn.close()
    return dict(row) if row else None


def propose_group(incident_number, group):
    """agent (ไม่ใช่ admin) เสนอ group ใหม่ รอ admin ยืนยัน — ไม่แตะ owner_group จริง"""
    conn = get_connection()
    conn.execute("UPDATE tickets SET proposed_group = ? WHERE incident_number = ?", (group, incident_number))
    conn.commit()
    conn.close()


def assign_group(incident_number, group, confirmed_by):
    """admin กำหนด group ทันที (คำตัดสินสุดท้าย) — เทียบกับ owner_group เดิมเพื่อรู้ว่าระบบจำแนกผิดหรือเปล่า
    (ใช้ทำ % ความแม่นยำ — corrected_group ไม่ว่างแปลว่าคนต้องแก้ระบบ)

    ⚠️ ต้องเคลียร์ corrected_group กลับเป็น NULL ทุกครั้งที่ group ที่ assign ตรงกับ
    owner_group ปัจจุบันแล้ว (ไม่ใช่แค่ตั้งตอน mismatch) ไม่งั้นถ้าเคย assign ผิดมาก่อน
    หนึ่งครั้งแล้วแก้กลับมาให้ตรง ticket ใบนั้นจะยังถูกนับเป็น "ผิด" ใน
    classificationAccuracyPct ตลอดไป (เจอจริง 6 ส.ค. 2026 ดู docs/Plan.md ข้อ 4)
    """
    ticket = get_ticket(incident_number)
    if ticket is None:
        return
    corrected = group if ticket['owner_group'] != group else None
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET owner_group = ?, proposed_group = NULL, corrected_group = ?, "
        "confirmed_by = ?, confirmed_at = ? WHERE incident_number = ?",
        (group, corrected, confirmed_by, _now(), incident_number)
    )
    conn.commit()
    conn.close()


def confirm_proposed_group(incident_number, confirmed_by):
    """admin ยืนยัน group ที่ agent เสนอไว้ (proposed_group) ให้กลายเป็น owner_group จริง"""
    ticket = get_ticket(incident_number)
    if ticket is None or not ticket['proposed_group']:
        return
    assign_group(incident_number, ticket['proposed_group'], confirmed_by)


def reject_proposed_group(incident_number):
    conn = get_connection()
    conn.execute("UPDATE tickets SET proposed_group = NULL WHERE incident_number = ?", (incident_number,))
    conn.commit()
    conn.close()


def acknowledge_ticket(incident_number, username):
    """agent กด acknowledge — ถ้า status ยังเป็น Assigned ให้ขยับเป็น In Progress (BRIEF ข้อ 3.4)"""
    ticket = get_ticket(incident_number)
    if ticket is None:
        return
    new_status = 'In Progress' if ticket['status'] == 'Assigned' else ticket['status']
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET acknowledged_by = ?, acknowledged_at = ?, status = ? WHERE incident_number = ?",
        (username, _now(), new_status, incident_number)
    )
    conn.commit()
    conn.close()


def ensure_track_token(incident_number):
    """คืน track_token ที่มีอยู่ หรือสร้างใหม่ครั้งแรกที่ถูกถาม (TT เก่าจาก Phase 1 ไม่มี token มาแต่แรก)"""
    ticket = get_ticket(incident_number)
    if ticket is None:
        return None
    if ticket['track_token']:
        return ticket['track_token']
    token = secrets.token_hex(6)
    conn = get_connection()
    conn.execute("UPDATE tickets SET track_token = ? WHERE incident_number = ?", (token, incident_number))
    conn.commit()
    conn.close()
    return token


def find_by_track_token(token):
    conn = get_connection()
    row = conn.execute("SELECT * FROM tickets WHERE track_token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_current_sub_status(incident_number, sub_status, technician_assigned=None, status=None):
    conn = get_connection()
    sets, params = ["current_sub_status = ?"], [sub_status]
    if technician_assigned is not None:
        sets.append("technician_assigned = ?")
        params.append(1 if technician_assigned else 0)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    params.append(incident_number)
    conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE incident_number = ?", params)
    conn.commit()
    conn.close()


def touch_last_reported(incident_number):
    conn = get_connection()
    conn.execute("UPDATE tickets SET last_reported_at = ? WHERE incident_number = ?", (_now(), incident_number))
    conn.commit()
    conn.close()


def add_ticket_event(incident_number, sub_status, note=None, event_time=None,
                      technician_name=None, technician_contact=None, created_by=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO ticket_events (incident_number, sub_status, note, event_time, "
        "technician_name, technician_contact, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (incident_number, sub_status, note, event_time, technician_name, technician_contact, created_by, _now())
    )
    conn.commit()
    conn.close()


def list_ticket_events(incident_number):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ticket_events WHERE incident_number = ? ORDER BY id", (incident_number,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_handover(incident_number, to_username):
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET pending_handover_to = ? WHERE incident_number = ?", (to_username, incident_number)
    )
    conn.commit()
    conn.close()


def confirm_handover(incident_number):
    ticket = get_ticket(incident_number)
    if ticket is None or not ticket['pending_handover_to']:
        return
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET acknowledged_by = ?, acknowledged_at = ?, pending_handover_to = NULL "
        "WHERE incident_number = ?",
        (ticket['pending_handover_to'], _now(), incident_number)
    )
    conn.commit()
    conn.close()


def reject_handover(incident_number):
    conn = get_connection()
    conn.execute("UPDATE tickets SET pending_handover_to = NULL WHERE incident_number = ?", (incident_number,))
    conn.commit()
    conn.close()


def create_manual_ticket(node, interface, summary, owner_group, priority, reported_by):
    """สร้าง TT จากการแจ้งเหตุด้วยตนเอง (BRIEF ข้อ 7) — raw_syslog เป็น NULL เพื่อบอกว่าไม่ได้มาจาก syslog จริง
    เลข incident_number ใช้ prefix ต่างจาก syslog (MAN- ไม่ใช่ INC-) กันชนกับเลขที่ syslog_server.py ออกเอง
    """
    conn = get_connection()
    today_str = datetime.now().strftime('%Y%m%d')
    prefix = f"MAN-{today_str}-"
    row = conn.execute(
        "SELECT incident_number FROM tickets WHERE incident_number LIKE ? ORDER BY incident_number DESC LIMIT 1",
        (prefix + '%',)
    ).fetchone()
    seq = 1 if row is None else int(row['incident_number'].split('-')[-1]) + 1
    incident_number = f"{prefix}{seq:04d}"

    conn.execute(
        "INSERT INTO tickets (incident_number, node, interface, summary, status, event_start_time, "
        "raw_syslog, owner_group, priority, confidence, classify_reason, classified_at, confirmed_by, confirmed_at) "
        "VALUES (?, ?, ?, ?, 'Assigned', ?, NULL, ?, ?, 100, ?, ?, ?, ?)",
        # event_start_time ต้องใช้ _now_syslog_format() ไม่ใช่ _now() — ดูเหตุผลที่ตัวฟังก์ชัน
        (incident_number, node, interface, summary, _now_syslog_format(), owner_group, priority,
         f'แจ้งเหตุด้วยตนเองโดย {reported_by} — ไม่ผ่านการจำแนกอัตโนมัติ', _now(), reported_by, _now())
    )
    conn.commit()
    conn.close()
    return incident_number


# ---------------------------------------------------------------------------
# Phase 3 — Deep Diagnostic + หน้า Analyze (ดู docs/BRIEF_Phase3_Analyze.md)
# ---------------------------------------------------------------------------

def record_diagnostic_run(incident_number, device, command, output, success, run_by=None):
    """บันทึกประวัติการรันคำสั่งวินิจฉัยแต่ละครั้ง — ทั้งที่ diagnose_worker.py รันเอง
    (run_by=None) และที่คนสั่งรันจาก Manual Investigation (run_by=username)
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO diagnostic_runs (incident_number, device, command, output, success, run_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_number, device, command, output, 1 if success else 0, run_by, _now())
    )
    conn.commit()
    conn.close()


def list_diagnostic_runs(incident_number):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM diagnostic_runs WHERE incident_number = ? ORDER BY id", (incident_number,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_diagnose_state(incident_number, state):
    """ตั้ง diagnose_state เดี่ยวๆ — ใช้ตอนเริ่มรัน (ตั้งเป็น 'running') ก่อนผลจริงจะพร้อม
    ให้ปุ่ม Re-check คืนทันทีไม่ต้องรอ SSH จบ (ห้าม block ตามข้อห้าม #2)
    """
    conn = get_connection()
    conn.execute("UPDATE tickets SET diagnose_state = ? WHERE incident_number = ?", (state, incident_number))
    conn.commit()
    conn.close()


def update_diagnosis(incident_number, raw_output, diagnose_state, cause_summary=None,
                      cause_source=None, evidence_lines=None, llm_status=None):
    """บันทึกผลวินิจฉัยรอบล่าสุด

    ⚠️ raw_output ห้ามว่าง (ข้อห้าม #4) — แม้ SSH เข้าไม่ได้เลยก็ต้องมีข้อความอธิบาย
    ("unreachable") ผู้เรียก (lib/diagnostics.py) เป็นคนรับผิดชอบเรื่องนี้ ฟังก์ชันนี้
    ไม่เช็คซ้ำเพื่อไม่ให้ logic ตรวจสอบกระจายอยู่หลายที่
    """
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET raw_output = ?, diagnose_state = ?, cause_summary = ?, cause_source = ?, "
        "evidence_lines = ?, llm_status = ?, diagnosed_at = ? WHERE incident_number = ?",
        (raw_output, diagnose_state, cause_summary, cause_source, evidence_lines, llm_status,
         _now(), incident_number)
    )
    conn.commit()
    conn.close()


def update_analysis(incident_number, cause_summary=None, cause_source=None, evidence_lines=None,
                    llm_status=None, llm_explanation=None, llm_note=None):
    """บันทึกผลจากชั้นวิเคราะห์ Phase 3.5 (lib/analyzer.py) — เรียกโดย analyze_pending.py

    ⚠️ **จงใจแยกจาก update_diagnosis() ไม่ยัดรวมกัน** ด้วยเหตุผล 3 ข้อ:

    1. `raw_output` ไม่อยู่ในนี้เลย → เป็นไปไม่ได้ที่ชั้นวิเคราะห์จะเขียนทับผลดิบจากอุปกรณ์
       (เกณฑ์ข้อ 4 ของเล่ม: ผลดิบต้องไม่ถูกดัดแปลง 100%) บังคับด้วยโครงสร้าง ไม่ใช่ด้วยวินัย
    2. `diagnose_state` ก็ไม่อยู่ในนี้ → ชั้นวิเคราะห์เปลี่ยนสถานะการวินิจฉัยของ Phase 3 ไม่ได้
    3. `status` / `owner_group` / `priority` ไม่อยู่ในนี้ → **แบบจำลองภาษาไม่มีทางปิดงานเอง
       หรือเปลี่ยนทีมรับผิดชอบได้เลย** ต่อให้โค้ดผู้เรียกจะพยายามก็ตาม (เกณฑ์ข้อ 2 = 0%)

    เก็บ analyzed_at แยกจาก diagnosed_at เพราะเป็นคนละเหตุการณ์: diagnosed_at คือเวลาที่
    SSH เข้าไปเก็บผล ส่วน analyzed_at คือเวลาที่เอาผลนั้นมาวิเคราะห์ (ห่างกันได้เป็นชั่วโมง
    ตามสถาปัตยกรรม 2 ช่วงใน Plan 3.5.2 ที่ต้องปิด CML ก่อนถึงจะเปิด LLM ได้)
    """
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET cause_summary = ?, cause_source = ?, evidence_lines = ?, "
        "llm_status = ?, llm_explanation = ?, llm_note = ?, analyzed_at = ? "
        "WHERE incident_number = ?",
        (cause_summary, cause_source, evidence_lines, llm_status, llm_explanation, llm_note,
         _now(), incident_number)
    )
    conn.commit()
    conn.close()


def update_disposition(incident_number, result, probe_raw=None):
    """บันทึกผลจากชั้นตัดสินประเภทงาน (lib/disposition.py)

    ⚠️ **จงใจเขียนได้เฉพาะ column ที่ขึ้นต้นด้วย disposition_ เท่านั้น** ด้วยเหตุผลเดียวกับ
    update_analysis() คือบังคับด้วยโครงสร้าง ไม่ใช่ด้วยวินัยของคนเขียนโค้ด:

    1. `raw_output` ไม่อยู่ในนี้ -> ชั้นตัดสินเขียนทับผลดิบจากอุปกรณ์ไม่ได้ (เกณฑ์ข้อ 4)
    2. `status` / `owner_group` / `priority` ไม่อยู่ในนี้ -> ระบบเปลี่ยนสถานะงานหรือทีม
       รับผิดชอบเองไม่ได้เลย ต่อให้ตัดสินว่าเป็นงานของ L2 ก็ยัง "เสนอ" เท่านั้น (เกณฑ์ข้อ 2 = 0%)
    3. `confirmed_disposition` ไม่อยู่ในนี้ -> ระบบยืนยันแทนคนไม่ได้ ต้องผ่าน
       confirm_disposition() ซึ่งบังคับให้มีชื่อผู้ยืนยันเสมอ

    result   : dict ที่ได้จาก disposition.decide()
    probe_raw: ผลดิบของ ping/show ที่ใช้ตัดสิน (dict) — เก็บเป็น JSON ไว้ตรวจย้อนกลับ
    """
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET disposition = ?, disposition_route = ?, disposition_action = ?, "
        "disposition_confidence = ?, disposition_reasons = ?, disposition_ruled_out = ?, "
        "disposition_evidence = ?, disposition_probe = ?, disposition_at = ? "
        "WHERE incident_number = ?",
        (
            result.get('disposition'),
            result.get('route'),
            result.get('action'),
            result.get('confidence'),
            '\n'.join(result.get('reasons') or []),
            '\n'.join(result.get('ruled_out') or []),
            result.get('evidence_lines'),
            json.dumps(probe_raw, ensure_ascii=False) if probe_raw else None,
            _now(),
            incident_number,
        )
    )
    conn.commit()
    conn.close()


def confirm_disposition(incident_number, disposition, confirmed_by):
    """agent ยืนยันประเภทงาน — เป็นทางเดียวที่ confirmed_disposition จะถูกเขียน

    บังคับให้มี confirmed_by เสมอ ถ้าไม่มีชื่อผู้ยืนยันจะ raise ทิ้ง ไม่ยอมบันทึก
    เพราะค่านี้คือหลักฐานว่า "มีคนตัดสิน" ถ้าเขียนได้โดยไม่มีชื่อ หลักฐานนั้นก็ไม่มีความหมาย

    ค่าที่ยืนยันอาจต่างจากที่ระบบเสนอ (disposition) ได้ — เก็บทั้งสองค่าไว้เทียบกัน
    เพื่อวัดว่าระบบเสนอถูกกี่เปอร์เซ็นต์ (เกณฑ์ข้อ 3)
    """
    if not confirmed_by:
        raise ValueError("confirm_disposition ต้องระบุ confirmed_by เสมอ")
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET confirmed_disposition = ?, disposition_confirmed_by = ?, "
        "disposition_confirmed_at = ? WHERE incident_number = ?",
        (disposition, confirmed_by, _now(), incident_number)
    )
    conn.commit()
    conn.close()


def set_impact_suggestion(incident_number, suggestion):
    """บันทึกตัวเลขผลกระทบที่ระบบเสนอ — ไม่แตะค่าที่คนยืนยันไว้แล้ว

    เก็บทั้งตัวเลขและเหตุผลไว้ด้วยกันเป็น JSON ก้อนเดียว เพราะตัวเลขที่ไม่มีที่มา
    ตรวจสอบย้อนกลับไม่ได้ ถ้าเก็บแยกกันแล้ววันหนึ่งหลุดจากกัน จะไม่รู้ว่าเลขนี้มาจากไหน
    """
    conn = get_connection()
    conn.execute("UPDATE tickets SET impact_suggested = ? WHERE incident_number = ?",
                 (json.dumps(suggestion, ensure_ascii=False), incident_number))
    conn.commit()
    conn.close()


def set_impact_values(incident_number, values, confirmed_by):
    """บันทึกตัวเลขผลกระทบที่ผู้ตรวจยืนยัน — ต้องมีชื่อผู้ยืนยันเสมอ

    ตัวเลขชุดนี้ถูกนำไปกรอกลงเอกสารที่ส่งให้อีกทีมและมีผลต่อระดับ Severity ที่คำนวณอัตโนมัติ
    ถ้าบันทึกได้โดยไม่มีชื่อ ก็จะไม่มีใครรับผิดชอบตัวเลขที่ปรากฏในเอกสาร
    """
    if not confirmed_by:
        raise ValueError("set_impact_values ต้องระบุ confirmed_by เสมอ")
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET impact_values = ?, impact_by = ?, impact_at = ? "
        "WHERE incident_number = ?",
        (json.dumps(values, ensure_ascii=False), confirmed_by, _now(), incident_number)
    )
    conn.commit()
    conn.close()


def set_pw_draft(incident_number, path=None, mop_key=None, note=None):
    """บันทึกผลการร่าง PW — ทั้งกรณีสร้างได้และกรณีร่างไม่ได้

    กรณีร่างไม่ได้ก็ต้องบันทึก (path=None, note=เหตุผล) เพราะ "ระบบไม่ยอมร่างเพราะอะไร"
    เป็นข้อมูลที่ต้องตรวจย้อนกลับได้พอๆ กับตัวร่างเอง ถ้าไม่เก็บไว้ จะไม่มีทางรู้ว่าระบบ
    เงียบเพราะยังไม่ได้ลอง หรือเพราะลองแล้วปฏิเสธ
    """
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET pw_draft_path = ?, pw_draft_mop = ?, pw_draft_note = ?, "
        "pw_draft_at = ? WHERE incident_number = ?",
        (path, mop_key, note, _now(), incident_number)
    )
    conn.commit()
    conn.close()


def list_disposition_accuracy():
    """TT ที่มีทั้งค่าที่ระบบเสนอและค่าที่คนยืนยัน — ใช้คำนวณความแม่นยำของชั้นตัดสิน"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT incident_number, node, interface, disposition, confirmed_disposition, "
        "disposition_confidence, disposition_confirmed_by, disposition_confirmed_at "
        "FROM tickets WHERE disposition IS NOT NULL AND confirmed_disposition IS NOT NULL "
        "ORDER BY disposition_confirmed_at DESC"
    ).fetchall()
    conn.close()
    return rows


def list_pending_llm_tickets():
    """TT ที่ผ่านการวินิจฉัยแล้วแต่กฎสรุปสาเหตุไม่ได้ (llm_status='pending') — คิวของ Phase 3.5

    เงื่อนไข raw_output ต้องไม่ว่าง เพราะไม่มีผลดิบก็ไม่มีอะไรให้วิเคราะห์ และการส่งค่าว่าง
    เข้าแบบจำลองภาษามีแต่จะได้คำตอบที่เดาล้วน

    ไม่กรอง status ออกเหมือน list_undiagnosed_tickets() เพราะขั้นนี้ไม่ต้อง SSH เข้าอุปกรณ์
    (ทำงานกับข้อมูลที่เก็บไว้แล้ว) TT ที่ปิดไปแล้วก็ยังวิเคราะห์ย้อนหลังได้ ไม่เสียอะไร
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE llm_status = 'pending' "
        "AND raw_output IS NOT NULL AND raw_output != '' ORDER BY event_start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_undiagnosed_tickets():
    """TT ที่ยังไม่เคยวินิจฉัยเลย (diagnose_state ว่าง) — ใช้โดย diagnose_worker.py หา TT ที่ต้องทำ
    ไม่รวม TT ที่ปิดงานไปแล้ว (Resolved/Closed) จะได้ไม่เสียเวลา SSH ไปวินิจฉัยเหตุที่จบแล้ว
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE diagnose_state IS NULL AND status NOT IN ('Resolved', 'Closed') "
        "ORDER BY event_start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_recheck_requested_tickets():
    """TT ที่ถูกกด Re-check จากหน้า Analyze (diagnose_state ถูกตั้งเป็น 'running' รอ
    diagnose_worker.py หยิบไปทำจริง) — แยกจาก list_undiagnosed_tickets() (diagnose_state
    ว่างเปล่า = ยังไม่เคยตรวจเลย) เพราะเป็นคนละเหตุการณ์กัน แต่ worker ต้อง poll ทั้งคู่
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE diagnose_state = 'running' ORDER BY event_start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_pending_review_tickets():
    """TT ที่วินิจฉัยแล้วแต่ยังไม่มีคนตรวจ (reviewed_by ว่าง) — ใช้ทำหน้ารวมของเมนู Analyze
    กรอง status = 'Assigned' เพราะใบที่ผ่าน mark_reviewed() ไปแล้วจะเป็น 'In Progress' ทันที
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tickets WHERE reviewed_by IS NULL AND status = 'Assigned' "
        "ORDER BY event_start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_reviewed(incident_number, reviewed_by, group=None):
    """เจ้าหน้าที่กด "ตรวจสอบแล้ว · ส่งเข้า In Progress" ในหน้า Analyze — ทำ 3 อย่างพร้อมกัน:
      1) บันทึกว่าใครตรวจเมื่อไหร่ (reviewed_by/reviewed_at)
      2) แก้ owner_group ถ้าเจ้าหน้าที่เปลี่ยนจากที่ระบบเสนอ (logic เดียวกับ assign_group()
         เพื่อให้ % ความแม่นยำใน /api/stats วัดถูก — เคลียร์ corrected_group กลับเป็น NULL
         ถ้า group ที่เลือกตรงกับที่ระบบจำแนกไว้แล้ว)
      3) เลื่อน TT เข้า In Progress จริง (acknowledged_by/at + status) — แทนที่
         acknowledge_ticket() เดิมสำหรับเส้นทางนี้ เพราะ Phase 3 บังคับว่าต้องผ่านการตรวจ
         ในหน้า Analyze ก่อนเสมอ ถึงจะเข้า In Progress ได้ (checkpoint ข้อ 10)
    """
    ticket = get_ticket(incident_number)
    if ticket is None:
        return
    if group:
        final_group = group
        corrected = None if ticket['owner_group'] == group else group
    else:
        final_group = ticket['owner_group']
        corrected = ticket['corrected_group']
    new_status = 'In Progress' if ticket['status'] == 'Assigned' else ticket['status']

    # ⚠️ ต้องเขียน confirmed_by/confirmed_at ด้วย ไม่ใช่แค่ reviewed_by (เพิ่ม 20 ส.ค. 2026)
    #
    # เดิมฟังก์ชันนี้เขียนแค่ reviewed_by แต่ตัววัด % ความแม่นยำทั้งสองที่ —
    # /api/stats (dashboard/backend/app.py) และ check_accuracy.py — นับ "ตัวหาร" จาก
    # confirmed_by ผลคือ Ticket ที่คนตรวจแล้วจริงไม่ถูกนับเลยสักใบ ตัวหารเป็น 0
    # และ % ความแม่นยำคำนวณไม่ได้ตลอดกาล โดยไม่มี error ให้เห็น
    #
    # เลือกแก้ที่ฝั่ง "เขียน" ไม่ใช่ฝั่ง "อ่าน" เพราะ confirmed_by เป็นนิยามเดียวที่ทั้งระบบ
    # ใช้ร่วมกันอยู่แล้ว ถ้าไปแก้ให้ตัววัดนับ reviewed_by แทนต้องไล่แก้ 2 ที่ ลืมที่ใดที่หนึ่ง
    # จะได้ตัวเลขสองค่าที่ไม่ตรงกันเงียบๆ = บั๊กชนิดเดียวกับที่กำลังแก้อยู่นี่เอง
    #
    # การกดปุ่มบนหน้า Analyze *คือ* การยืนยันของคนตามเจตนา human-in-the-loop ของ Phase 4
    # อยู่แล้ว (หน้านี้ถูกเพิ่มมาเป็นด่านบังคับว่าคนต้องตรวจก่อนงานจะเดินต่อ — checkpoint
    # ข้อ 10) การบันทึก confirmed_by จึงเป็นการบันทึกสิ่งที่เกิดขึ้นจริง ไม่ใช่การยัดค่าให้
    # ตัวเลขสวย ส่วน logic ถูก-ผิด (corrected_group ด้านบน) ไม่ต้องแก้ เพราะทำงานถูกอยู่แล้ว
    #
    # 📌 ใช้ now ตัวเดียวกันทุกช่อง — เดิมเรียก _now() หลายครั้งในบรรทัดเดียว ซึ่งได้เวลา
    # ต่างกันเล็กน้อยและทำให้ reviewed_at กับ confirmed_at ของ "การกดครั้งเดียวกัน" ไม่ตรงกัน
    now = _now()
    conn = get_connection()
    conn.execute(
        "UPDATE tickets SET owner_group = ?, corrected_group = ?, reviewed_by = ?, reviewed_at = ?, "
        "confirmed_by = ?, confirmed_at = ?, "
        "acknowledged_by = ?, acknowledged_at = ?, status = ? WHERE incident_number = ?",
        (final_group, corrected, reviewed_by, now, reviewed_by, now, reviewed_by, now,
         new_status, incident_number)
    )
    conn.commit()
    conn.close()


def list_users():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user(username):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(username, password_hash, first_name, last_name, team, is_admin=False, status='pending'):
    conn = get_connection()
    conn.execute(
        "INSERT INTO users (username, password_hash, first_name, last_name, team, is_admin, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (username, password_hash, first_name, last_name, team, 1 if is_admin else 0, status, _now())
    )
    conn.commit()
    conn.close()


def set_user_status(user_id, status):
    conn = get_connection()
    conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# ทดสอบด้วยข้อมูลปลอม (ไม่ต้องมี lab จริง) — รัน: py db.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import os

    # ใช้ไฟล์แยกต่างหากสำหรับ self-test เสมอ — ผูกกับตำแหน่งไฟล์นี้ (__file__) ไม่ใช่
    # cwd ตอนรัน กัน self-test ไปทับ tickets.db จริงถ้าเผลอรันจาก root ของโปรเจกต์
    # (เคยเกิดจริงมาแล้ว ดู docs/Plan.md บันทึกผลตรวจสอบ 6 ส.ค. 2026 ข้อ 1)
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_db_selftest.db')

    # เริ่มจากไฟล์ db สะอาดทุกครั้งที่ทดสอบ
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    init_db()
    print("1) init_db() ผ่าน — สร้างตาราง tickets แล้ว")

    # จำลอง event down จาก Core Ethernet0/1 (= สาย Core<->Access-Site-B)
    inc1 = create_ticket(
        node='Access-Site-B',
        interface='Ethernet0/1',
        summary='Link down: Core Ethernet0/1 <-> Access-Site-B',
        event_start_time='2026-07-30 14:31:05.123',
        raw_syslog='%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down'
    )
    print(f"2) create_ticket() -> {inc1}")
    assert inc1.startswith('INC-')

    # ยิงซ้ำ (จำลอง syslog ซ้ำ/retransmit) -> ต้องได้เลขเดิม ไม่สร้างใบใหม่
    inc1_dup = create_ticket(
        node='Access-Site-B',
        interface='Ethernet0/1',
        summary='Link down: Core Ethernet0/1 <-> Access-Site-B',
        event_start_time='2026-07-30 14:31:06.000',
        raw_syslog='duplicate event'
    )
    assert inc1 == inc1_dup, "ควรได้ incident_number เดิม ไม่ใช่ใบใหม่"
    print(f"3) กันสร้างซ้ำผ่าน -> ยังเป็น {inc1_dup}")

    # จำลอง fault อีก node พร้อมกัน (Access-Site-A) -> ต้องแยกใบ
    inc2 = create_ticket(
        node='Access-Site-A',
        interface='Ethernet0/0',
        summary='Link down: Core Ethernet0/0 <-> Access-Site-A',
        event_start_time='2026-07-30 14:31:10.000',
        raw_syslog='%LINK-3-UPDOWN: Interface Ethernet0/0, changed state to down'
    )
    assert inc2 != inc1
    print(f"4) node อื่นแยกใบถูก -> {inc2}")

    # จำลอง event up -> บันทึกว่าสัญญาณกลับมา
    recovered = record_recovery('Access-Site-B', 'Ethernet0/1', '2026-07-30 14:35:00.000')
    assert recovered == inc1
    print(f"5) record_recovery() บันทึกใบ {recovered} สำเร็จ")

    # ⭐ สำคัญ: สัญญาณกลับมาแล้ว แต่ status ต้องยัง "Assigned" อยู่ (ระบบไม่ปิดงานเอง)
    row = [t for t in list_all_tickets() if t['incident_number'] == inc1][0]
    assert row['event_end_time'] is not None, "ต้องบันทึกเวลาที่สัญญาณกลับมา"
    assert row['status'] == 'Assigned', f"status ต้องไม่ถูกเปลี่ยนเอง แต่ได้ {row['status']}"
    print("6) สัญญาณกลับมาแล้วแต่ status ยังเป็น Assigned -> ระบบไม่ปิดงานเอง ✓")

    # ตรวจว่า find_open_ticket ไม่เจอใบที่เหตุจบแล้ว (จะได้สร้างใบใหม่ได้ถ้าล่มซ้ำ)
    assert find_open_ticket('Access-Site-B', 'Ethernet0/1') is None
    print("7) เหตุที่จบแล้วไม่ถูกนับเป็นเหตุค้าง -> ล่มซ้ำจะได้ TT ใบใหม่")

    # ⭐ เคส "TT หลุมดำ" (เจอจริง 4 ส.ค. 2026): เจ้าหน้าที่กด Resolved
    # ก่อนที่ event 'up' จะเข้ามา -> event_end_time ยังเป็น NULL อยู่
    # find_open_ticket ต้องไม่หยิบใบนี้มาใช้ซ้ำ เพราะคนถือว่าใบนี้ปิดไปแล้ว
    inc3 = create_ticket(
        node='Access-Site-C',
        interface='Ethernet0/2',
        summary='Link down: Core Ethernet0/2 <-> Access-Site-C',
        event_start_time='2026-07-30 15:00:00.000',
        raw_syslog='%LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down'
    )
    conn = get_connection()
    conn.execute("UPDATE tickets SET status = 'Resolved' WHERE incident_number = ?", (inc3,))
    conn.commit()
    conn.close()
    # ยังไม่มี event 'up' เข้ามาเลย (event_end_time ยัง NULL) แต่คนกด Resolved ไปแล้ว
    assert find_open_ticket('Access-Site-C', 'Ethernet0/2') is None, \
        "TT ที่สถานะ Resolved/Closed ต้องไม่ถูกนับเป็นเหตุค้าง แม้ event_end_time จะยังว่าง"
    print("8) กันบั๊ก 'TT หลุมดำ' ผ่าน -> ใบที่คนปิดแล้วไม่ถูกดึงมาใช้ซ้ำอีก")

    # ล่มซ้ำที่จุดเดิมหลังคนปิดงานไปแล้ว -> ต้องได้ TT ใบใหม่ ไม่ใช่ inc3
    inc3_new = create_ticket(
        node='Access-Site-C',
        interface='Ethernet0/2',
        summary='Link down: Core Ethernet0/2 <-> Access-Site-C',
        event_start_time='2026-08-04 09:00:00.000',
        raw_syslog='%LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down'
    )
    assert inc3_new != inc3, "ล่มซ้ำหลังปิดงานแล้วต้องได้ TT ใบใหม่ ไม่ใช่ใบเดิมที่ถูกปิดไปแล้ว"
    print(f"9) ล่มซ้ำหลังปิดงาน -> ได้ TT ใบใหม่ {inc3_new} (ไม่ใช่ {inc3} ใบเก่าที่ปิดแล้ว)")

    # --- Phase 3: diagnostic_runs + diagnosis + review (docs/BRIEF_Phase3_Analyze.md) ---

    # inc2 ('Access-Site-A') ยังไม่เคยวินิจฉัย -> ต้องโผล่ในรายการที่ diagnose_worker.py ต้องทำ
    undiagnosed_ids = [t['incident_number'] for t in list_undiagnosed_tickets()]
    assert inc2 in undiagnosed_ids, "TT ที่ diagnose_state ยังว่างต้องโผล่ใน list_undiagnosed_tickets()"
    print("10) list_undiagnosed_tickets() เห็น TT ที่ยังไม่วินิจฉัย ✓")

    set_diagnose_state(inc2, 'running')
    assert get_ticket(inc2)['diagnose_state'] == 'running'
    assert inc2 in [t['incident_number'] for t in list_recheck_requested_tickets()], \
        "TT ที่ diagnose_state='running' ต้องโผล่ใน list_recheck_requested_tickets() ให้ worker หยิบไปทำ"
    print("11) set_diagnose_state() ตั้ง 'running' ระหว่างรอผลได้ + worker เห็นผ่าน list_recheck_requested_tickets() ✓")

    record_diagnostic_run(inc2, device='Core', command='show ip interface brief', output='...', success=True)
    runs = list_diagnostic_runs(inc2)
    assert len(runs) == 1 and runs[0]['success'] == 1
    print("12) record_diagnostic_run()/list_diagnostic_runs() บันทึกประวัติได้ ✓")

    update_diagnosis(
        inc2, raw_output='Ethernet0/0 is down, line protocol is down (disabled)',
        diagnose_state='done', cause_summary='พอร์ตถูกสั่งปิดจากการตั้งค่า', cause_source='rule',
        evidence_lines='Ethernet0/0 is administratively down', llm_status='done',
    )
    diagnosed = get_ticket(inc2)
    assert diagnosed['diagnose_state'] == 'done' and diagnosed['raw_output']
    assert inc2 not in [t['incident_number'] for t in list_undiagnosed_tickets()], \
        "วินิจฉัยเสร็จแล้วต้องไม่ค้างใน list_undiagnosed_tickets() อีก"
    print("13) update_diagnosis() บันทึกผล + หลุดจากคิว undiagnosed ✓")

    # ยังไม่ได้ review -> ต้องโผล่ในคิวรอตรวจของหน้า Analyze
    pending_ids = [t['incident_number'] for t in list_pending_review_tickets()]
    assert inc2 in pending_ids, "TT ที่วินิจฉัยแล้วแต่ยัง reviewed_by ว่าง ต้องโผล่ใน list_pending_review_tickets()"
    print("14) list_pending_review_tickets() เห็น TT ที่รอตรวจ ✓")

    # กด "ตรวจสอบแล้ว · ส่งเข้า In Progress" พร้อมแก้ group -> ต้อง promote เข้า In Progress จริง
    original_group = diagnosed['owner_group']
    mark_reviewed(inc2, reviewed_by='agent.soc', group='Network-Core')
    reviewed = get_ticket(inc2)
    assert reviewed['status'] == 'In Progress', f"ต้องเลื่อนเข้า In Progress แต่ได้ {reviewed['status']}"
    assert reviewed['reviewed_by'] == 'agent.soc' and reviewed['acknowledged_by'] == 'agent.soc'
    assert reviewed['owner_group'] == 'Network-Core'
    assert reviewed['corrected_group'] == ('Network-Core' if original_group != 'Network-Core' else None)
    assert inc2 not in [t['incident_number'] for t in list_pending_review_tickets()], \
        "ตรวจแล้วต้องหลุดจากคิวรอตรวจ"
    print("15) mark_reviewed() บันทึกคนตรวจ + แก้ group ถูก + เลื่อนเข้า In Progress จริง ✓")

    # กด review ซ้ำโดยเลือก group เดิมที่ตรงกับระบบ -> corrected_group ต้องเคลียร์กลับเป็น None
    mark_reviewed(inc2, reviewed_by='agent.soc', group='Network-Core')
    assert get_ticket(inc2)['corrected_group'] is None, \
        "group ที่เลือกตรงกับ owner_group อยู่แล้วต้องเคลียร์ corrected_group กลับเป็น None"
    print("16) mark_reviewed() เคลียร์ corrected_group เมื่อ group ตรงกันแล้ว (แก้บั๊ก accuracy ค้าง) ✓")

    # ---- Phase 3.5: คิวของชั้นวิเคราะห์ + การเขียนผลกลับ ----

    # สร้าง TT ที่กฎสรุปสาเหตุไม่ได้ (llm_status='pending') ให้เป็นคิวของ Phase 3.5
    inc3 = create_ticket('Core', 'Ethernet0/2', 'ทดสอบคิว LLM', 'Aug 10 2026 09:00:00.000')
    update_diagnosis(
        inc3, raw_output='Ethernet0/2 is up, line protocol is up\n  1247 collisions',
        diagnose_state='undetermined', cause_summary=None, cause_source='undetermined',
        evidence_lines=None, llm_status='pending',
    )
    pending_llm = [t['incident_number'] for t in list_pending_llm_tickets()]
    assert inc3 in pending_llm, "TT ที่ llm_status='pending' ต้องอยู่ในคิวของ Phase 3.5"
    assert inc2 not in pending_llm, "TT ที่กฎสรุปได้แล้ว (llm_status='done') ต้องไม่อยู่ในคิว"
    print("17) list_pending_llm_tickets() เห็นเฉพาะ TT ที่กฎสรุปไม่ได้ ✓")

    before = get_ticket(inc3)
    update_analysis(
        inc3, cause_summary='Excessive collisions on the link', cause_source='llm',
        evidence_lines='1247 collisions', llm_status='done',
        llm_explanation='The link shows a high collision count.', llm_note=None,
    )
    after = get_ticket(inc3)
    assert after['cause_source'] == 'llm' and after['llm_status'] == 'done'
    assert after['llm_explanation'] and after['analyzed_at']
    assert inc3 not in [t['incident_number'] for t in list_pending_llm_tickets()], \
        "วิเคราะห์เสร็จแล้วต้องหลุดจากคิว"
    print("18) update_analysis() บันทึกผล + หลุดจากคิว pending ✓")

    # ⭐ ข้อบังคับสำคัญที่สุด: ชั้นวิเคราะห์ต้องแตะผลดิบและสถานะงานไม่ได้เลย
    assert after['raw_output'] == before['raw_output'], \
        "update_analysis() ต้องไม่แตะ raw_output (เกณฑ์ข้อ 4 — ผลดิบห้ามถูกดัดแปลง)"
    assert after['diagnose_state'] == before['diagnose_state'], \
        "update_analysis() ต้องไม่แตะ diagnose_state ของ Phase 3"
    assert after['status'] == before['status'], \
        "update_analysis() ต้องไม่แตะ status (เกณฑ์ข้อ 2 — ระบบปิดงานเองไม่ได้)"
    assert after['owner_group'] == before['owner_group'] and after['priority'] == before['priority'], \
        "update_analysis() ต้องไม่แตะ owner_group/priority ที่ Phase 2 จำแนกไว้"
    print("19) update_analysis() แตะผลดิบ/สถานะ/กลุ่ม/ความด่วน ไม่ได้เลย (บังคับด้วยโครงสร้าง) ✓")

    # สรุปทั้งหมดในตาราง
    print("\n--- ตารางทั้งหมด ---")
    for t in list_all_tickets():
        print(t)

    print(f"\n✅ ผ่านทุก test case (19/19)")

    # เก็บกวาดไฟล์ทดสอบทิ้ง ไม่ให้ค้างในโฟลเดอร์ lib/
    os.remove(DB_PATH)
    for suffix in ('-wal', '-shm'):
        sidecar = DB_PATH + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)
