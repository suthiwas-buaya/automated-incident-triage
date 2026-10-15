"""Phase 4 Dashboard backend — reads/writes the real tickets.db from Phase 1-3.

Data flow: CML Lab -> syslog_server.py -> tickets.db (SQLite) <- this Flask API -> frontend/
See BRIEF_Phase4_Dashboard.md for the full spec this file implements.

ห้ามแก้ syslog_server.py / lib/classifier.py / lib/correlation.py / lib/syslog_parser.py —
ไฟล์นี้แค่ "อ่าน/เขียน" tickets.db ผ่าน lib/db.py เท่านั้น ไม่มี logic จำแนกซ้ำที่นี่
"""

import re
from datetime import datetime

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

import json
import os
import secrets
import sys

# ทำให้ import `lib` (อยู่ที่ root ของ ProjectxKMUTT) ได้ ไม่ว่าจะรัน app.py จากโฟลเดอร์ไหน
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
from lib import db  # noqa: E402
from lib import correlation  # noqa: E402
from lib import mapping  # noqa: E402
from lib import diagnostics  # noqa: E402
from lib import syslog_parser  # noqa: E402
from lib import disposition as disposition_rules  # noqa: E402
from lib import reachability  # noqa: E402
from lib import pw_builder  # noqa: E402
from lib import impact_estimator  # noqa: E402

db.DB_PATH = os.path.join(PROJECT_ROOT, 'tickets.db')  # DB_PATH เดิมเป็น relative path หาไฟล์ไม่เจอถ้ารันข้ามโฟลเดอร์
db.init_db()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "allow_headers": ["Content-Type", "Authorization"],
    "methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
}})

# 4 Owner Group จริงจาก Phase 2 (lib/mapping.py) — ใช้จัดกลุ่ม TT ที่อ่านจาก tickets.db
DB_OWNER_GROUPS = ['Network-Core', 'Network-Access', 'Transmission', 'NOC-Monitoring']

# token -> {'username': str, 'last_seen': datetime}
# session อยู่แค่ในหน่วยความจำ ตั้งใจ — restart API แล้วทุกคนต้อง login ใหม่
TOKENS = {}

# หมดอายุเมื่อไม่มีการใช้งานติดต่อกันกี่นาที (idle timeout)
# 30 นาที = ยาวพอไม่กวนคนที่กำลังทำงานเคสอยู่ · สั้นพอที่คนเปลี่ยนกะแล้วต้อง login ใหม่
IDLE_TIMEOUT_MINUTES = 30
ACTIVITY_LOG = []  # เช่นเดียวกัน ไม่ persist — เป็นแค่ log กิจกรรม admin ระหว่าง session


def log_activity(message):
    ACTIVITY_LOG.append({'createdAt': now_iso(), 'message': message})


def now_iso():
    return datetime.now().astimezone().isoformat()


def local_timestamp_to_iso(text):
    """แปลง timestamp ที่ lib/db.py สร้างเอง (format '%Y-%m-%d %H:%M:%S') เป็น ISO 8601
    คืนค่าเดิมถ้า parse ไม่ได้ — กัน endpoint พังเพราะ timestamp ใบเดียว
    """
    if not text:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').astimezone().isoformat()
    except ValueError:
        return text


def syslog_timestamp_to_iso(text):
    """แปลง timestamp รูปแบบ syslog ('Jul 30 2026 11:10:17.076') ที่ lib/syslog_parser.py เขียนลง
    event_start_time/event_end_time เป็น ISO 8601 — คืนค่าเดิมถ้า parse ไม่ได้
    """
    parsed = correlation.parse_event_time(text)
    return parsed.isoformat() if parsed else text


# ---------------------------------------------------------------------------
# แปลง classify_reason (ข้อความอิสระจาก lib/classifier.py / lib/correlation.py —
# ทั้งคู่ห้ามแก้ตามข้อห้าม #1) ให้เป็นโครงสร้าง RULE / IMPACT / PRIORITY เป็นภาษาอังกฤษ
# สำหรับหน้า Analyze (docs/BRIEF_Phase3_Analyze.md ส่วนที่ 2/3) — ไม่แตะไฟล์ทั้งสองนั้น
# เลย แค่ parse ข้อความที่ format คงที่ (f-string ในโค้ดต้นทางไม่เปลี่ยน) แล้วประกอบใหม่
# ---------------------------------------------------------------------------

_CORRELATION_RE = re.compile(r'ตรวจพบ (\d+) Site หลุดพร้อมกันภายใน \d+ วินาที \(([^)]+)\).*?ยกระดับเป็น (P\d)')
_RULE_NAME_RE = re.compile(r'\(กฎ: ([\w-]+)\)')
_PRIORITY_NOTE_RE = re.compile(r'severity (\d+) -> (P\d)(?: แล้วยกเป็น (P\d) เพราะ Site หลุดจากเครือข่ายทั้ง Site)?')
_MANUAL_ENTRY_RE = re.compile(r'^แจ้งเหตุด้วยตนเองโดย (.+?) —')
_SYSLOG_PRI_PREFIX_RE = re.compile(r'^<\d+>\d+:\s*')


def _impact_line(node):
    if node == 'Core':
        return 'Core device issue - affects backbone routing'
    if node and node.startswith('Access-Site'):
        return f'{node} isolated from network'
    return f'Event on {node}' if node else None


def _priority_line(match):
    if not match:
        return None
    sev, base, escalated = match.group(1), match.group(2), match.group(3)
    line = f'severity {sev} -> {base}'
    if escalated:
        line += f' -> raised to {escalated} (site isolated)'
    return line


def build_reasoning(row):
    reason = row['classify_reason'] or ''
    node = row['node']
    priority = row['priority']

    corr = _CORRELATION_RE.search(reason)
    if corr:
        n, sites, p = corr.groups()
        return {
            'rule': 'core-device-correlated-failure',
            'impact': f'{n} sites down simultaneously: {sites}',
            'priority': f'Escalated to {p} - multiple sites affected simultaneously',
        }

    manual = _MANUAL_ENTRY_RE.match(reason)
    if manual:
        return {
            'rule': 'manual-entry',
            'impact': f'Reported manually by {manual.group(1)}',
            'priority': f'Set manually to {priority}' if priority else 'Set manually',
        }

    priority_line = _priority_line(_PRIORITY_NOTE_RE.search(reason))
    rule_match = _RULE_NAME_RE.search(reason)
    if rule_match:
        return {'rule': rule_match.group(1), 'impact': _impact_line(node), 'priority': priority_line}

    if reason.startswith('ไม่เข้ากฎใดเลย'):
        return {'rule': 'unclassified', 'impact': 'Could not be determined automatically', 'priority': priority_line}

    if not reason:
        return {'rule': None, 'impact': None, 'priority': None}

    # เผื่อ format ที่ไม่เข้าเงื่อนไขข้างบนเลย (ไม่ควรเกิดกับ 3 ไฟล์ปัจจุบัน แต่กันพังไว้)
    return {'rule': 'unrecognized', 'impact': _impact_line(node), 'priority': priority_line}


def _reported_by(raw_syslog):
    """hostname ที่ส่ง syslog มา (เช่น 'Core') — ดึงจาก raw_syslog ด้วย
    lib/syslog_parser.parse() (แค่เรียกใช้ ไม่ได้แก้ไฟล์นั้น) ไม่ได้เก็บเป็น column แยก
    """
    if not raw_syslog:
        return None
    parsed = syslog_parser.parse(raw_syslog)
    return parsed['hostname'] if parsed else None


def _strip_syslog_prefix(raw_syslog):
    """ตัด priority code ทิ้งตอนแสดงผล เช่น '<189>492: ' — ไม่มีประโยชน์กับคนอ่าน
    (docs/BRIEF_Phase3_Analyze.md ส่วนที่ 3.5) ตัวข้อมูลดิบเต็มยังอยู่ใน rawSyslog เดิม
    """
    if not raw_syslog:
        return raw_syslog
    return _SYSLOG_PRI_PREFIX_RE.sub('', raw_syslog)


# ---------------------------------------------------------------------------
# แปลง row จาก tickets.db -> JSON ที่ frontend/script.js คาดหวัง (BRIEF ข้อ 3.3)
# ---------------------------------------------------------------------------

def ticket_event_to_json(e):
    return {
        'subStatus': e['sub_status'],
        'note': e['note'],
        'eventTime': e['event_time'],
        'technicianName': e['technician_name'],
        'technicianContact': e['technician_contact'],
        'createdAt': local_timestamp_to_iso(e['created_at']),
        'createdBy': e['created_by'],  # username ที่กด Confirm/Save ให้ step นี้ — frontend ใช้แสดง "Responsible"
    }


def db_ticket_to_json(row):
    events = db.list_ticket_events(row['incident_number'])
    return {
        'id': row['incident_number'],
        'description': row['summary'],
        'status': row['status'],
        'tag': row['owner_group'],
        'proposedTag': row['proposed_group'],
        'assignedGroup': row['owner_group'],
        'startTime': syslog_timestamp_to_iso(row['event_start_time']),
        'endTime': syslog_timestamp_to_iso(row['event_end_time']),
        'priority': row['priority'],
        'confidence': row['confidence'],
        'classifyReason': row['classify_reason'],
        'reasoning': build_reasoning(row),
        'node': row['node'],
        'interface': row['interface'],
        'reportedBy': _reported_by(row['raw_syslog']),
        'rawSyslog': row['raw_syslog'],
        'rawSyslogDisplay': _strip_syslog_prefix(row['raw_syslog']),
        'acknowledged': bool(row['acknowledged_by']),
        'acknowledgedBy': row['acknowledged_by'],
        'acknowledgedAt': local_timestamp_to_iso(row['acknowledged_at']),
        'currentSubStatus': row['current_sub_status'],
        'subStatusTimeline': [ticket_event_to_json(e) for e in events],
        'assignedAt': local_timestamp_to_iso(row['confirmed_at']),
        'ticketReceivedAt': syslog_timestamp_to_iso(row['event_start_time']),
        'technicianAssigned': bool(row['technician_assigned']),
        'lastReportedAt': local_timestamp_to_iso(row['last_reported_at']),
        'pendingHandoverTo': row['pending_handover_to'],
        'pendingHandoverToUserId': row['pending_handover_to'],
        # Phase 3 — Deep Diagnostic (docs/BRIEF_Phase3_Analyze.md ข้อ 4)
        'causeSummary': row['cause_summary'],
        'causeSource': row['cause_source'],
        'evidenceLines': row['evidence_lines'],
        'rawOutput': row['raw_output'],
        'diagnoseState': row['diagnose_state'],
        'diagnosedAt': local_timestamp_to_iso(row['diagnosed_at']),
        'reviewedBy': row['reviewed_by'],
        'reviewedAt': local_timestamp_to_iso(row['reviewed_at']),
        # Phase 3.5 — ชั้นวิเคราะห์ด้วยแบบจำลองภาษา (docs/Plan.md 3.5)
        # llmExplanation = ข้อความเรียบเรียงให้คนอ่าน (อาจว่าง ไม่ใช่ของบังคับ)
        # llmNote        = เหตุผลที่ระบบ "ปฏิเสธ" คำตอบของโมเดล — ต้องแสดงให้เห็น
        #                  เพราะเป็นหลักฐานว่ากลไกกันตอบมั่วทำงาน ไม่ใช่ error ที่ต้องซ่อน
        'llmExplanation': row['llm_explanation'],
        'llmNote': row['llm_note'],
        'analyzedAt': local_timestamp_to_iso(row['analyzed_at']),
        # Phase 6 — ชั้นตัดสินประเภทงาน (lib/disposition.py)
        # disposition          = ค่าที่ระบบ "เสนอ"
        # confirmedDisposition = ค่าที่คน "ยืนยัน"
        # ส่งออกไปคู่กันเสมอ เพื่อให้หน้าจอแสดงได้ว่าอันไหนมาจากระบบ อันไหนมาจากคน
        **disposition_to_json(row),
    }


def _impact_to_json(row):
    """ตัวเลขผลกระทบสำหรับชีท PW Info แถว 25-34

    ส่งทั้งค่าที่ระบบเสนอและค่าที่คนยืนยันออกไปคู่กันเสมอ เพื่อให้หน้าจอแสดงได้ว่า
    ตัวเลขที่เห็นมาจากไหน และถ้าคนแก้ไปแล้ว ยังเห็นได้ว่าเดิมระบบเสนอเท่าไร
    """
    suggested = json.loads(row['impact_suggested']) if row['impact_suggested'] else None
    confirmed = json.loads(row['impact_values']) if row['impact_values'] else None
    return {
        'impactFields': [
            {'key': key, 'row': r, 'label': label}
            for key, r, label in impact_estimator.FIELDS
        ],
        'impactSuggested': (suggested or {}).get('values'),
        'impactReasons': (suggested or {}).get('reasons'),
        'impactBasis': (suggested or {}).get('basis'),
        'impactConfidence': (suggested or {}).get('confidence'),
        'impactValues': confirmed,
        'impactBy': row['impact_by'],
        'impactAt': local_timestamp_to_iso(row['impact_at']),
    }


def disposition_to_json(row):
    """แปลงคอลัมน์ disposition_* เป็น JSON — ใช้ร่วมกันทั้ง /tickets และ /diagnosis"""
    key = row['disposition']
    meta = disposition_rules.ROUTES.get(key, {})
    return {
        'disposition': key,
        'dispositionLabel': meta.get('label'),
        'dispositionRoute': row['disposition_route'],
        'dispositionAction': row['disposition_action'],
        'dispositionConfidence': row['disposition_confidence'],
        'dispositionReasons': (row['disposition_reasons'] or '').splitlines(),
        'dispositionRuledOut': (row['disposition_ruled_out'] or '').splitlines(),
        'dispositionEvidence': row['disposition_evidence'],
        'dispositionAt': local_timestamp_to_iso(row['disposition_at']),
        'confirmedDisposition': row['confirmed_disposition'],
        'dispositionConfirmedBy': row['disposition_confirmed_by'],
        'dispositionConfirmedAt': local_timestamp_to_iso(row['disposition_confirmed_at']),
        # ร่าง PW — ส่งแค่ชื่อไฟล์ ไม่ส่ง path เต็มออกไปให้หน้าจอ
        # (path เต็มเป็นโครงสร้างโฟลเดอร์ของเครื่องที่รันระบบ ไม่ใช่ข้อมูลที่ผู้ใช้ต้องรู้)
        'pwDraftFile': os.path.basename(row['pw_draft_path']) if row['pw_draft_path'] else None,
        'pwDraftMop': row['pw_draft_mop'],
        'pwDraftNote': row['pw_draft_note'],
        'pwDraftAt': local_timestamp_to_iso(row['pw_draft_at']),
        **_impact_to_json(row),
        # ตัวเลือกทั้งหมดที่ agent เลือกได้ — ส่งมาจาก backend เพื่อไม่ให้ frontend
        # ไปตั้งรายการเองแล้วหลุดจากกฎจริง (ข้อห้าม #5 ห้ามเชื่อ frontend)
        'dispositionOptions': [
            {'key': k, 'label': v['label'], 'route': v['route']}
            for k, v in disposition_rules.ROUTES.items()
        ],
    }


def diagnostic_run_to_json(r):
    return {
        'device': r['device'],
        'command': r['command'],
        'output': r['output'],
        'success': bool(r['success']),
        'runBy': r['run_by'],  # None = diagnose_worker.py รันเอง
        'createdAt': local_timestamp_to_iso(r['created_at']),
    }


def user_to_json(u):
    return {
        'id': u['id'],
        'username': u['username'],
        'firstName': u['first_name'],
        'lastName': u['last_name'],
        'team': u['team'],
        'isAdmin': bool(u['is_admin']),
        'status': u['status'],
        'createdAt': local_timestamp_to_iso(u['created_at']),
    }


# ---------------------------------------------------------------------------
# Seed users — เขียนลง tickets.db ครั้งแรกที่ตาราง users ยังว่างอยู่เท่านั้น
# (ครั้งต่อไปจะไม่ทับบัญชีที่ approve/สมัครเพิ่มไปแล้ว)
# ---------------------------------------------------------------------------

def seed_users_if_empty():
    if db.list_users():
        return
    db.create_user('admin', generate_password_hash('admin123'), 'Admin', 'Demo', None,
                    is_admin=True, status='approved')
    db.create_user('agent.soc', generate_password_hash('demo123'), 'Agent', 'SOC', 'Network-Core',
                    is_admin=False, status='approved')
    db.create_user('agent.l1', generate_password_hash('demo123'), 'Agent', 'L1', 'Network-Access',
                    is_admin=False, status='approved')
    db.create_user('agent.pending', generate_password_hash('demo123'), 'Pending', 'Agent', 'Transmission',
                    is_admin=False, status='pending')


seed_users_if_empty()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user():
    """คืน user จาก token ใน header — และต่ออายุ session ทุกครั้งที่มีการใช้งานจริง

    ⏱ หมดอายุเมื่อไม่มีการใช้งานติดต่อกัน IDLE_TIMEOUT_MINUTES นาที (ไม่ใช่หมดตามเวลาที่
    login) เพราะเหตุผลของการหมดอายุคือ "คนเดินออกจากหน้าจอไปแล้ว" ไม่ใช่ "login มานานแล้ว"

    ทำไมโปรเจกต์นี้ต้องมี — ไม่ใช่แค่เรื่องความปลอดภัยทั่วไป:
    ระบบบันทึกว่า *ใคร* เป็นคนยืนยันการจำแนก (confirmed_by) ใครตรวจ (reviewed_by)
    ใครสั่งงานช่าง — ถ้า session เปิดค้างตลอดกาลบนเครื่องที่ NOC ใช้ร่วมกันหลายกะ
    ชื่อที่บันทึกไว้จะไม่ใช่คนที่กดจริง **หลักฐานการตัดสินใจของคนซึ่งเป็นหัวใจของ
    Human-in-the-loop จะเชื่อถือไม่ได้ทั้งระบบ**
    """
    auth = request.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else ''
    session = TOKENS.get(token)
    if not session:
        return None

    idle = (datetime.now() - session['last_seen']).total_seconds() / 60
    if idle > IDLE_TIMEOUT_MINUTES:
        TOKENS.pop(token, None)   # ทิ้ง token ทันที ใช้ซ้ำไม่ได้อีก
        return None

    session['last_seen'] = datetime.now()   # มีการใช้งาน -> ต่ออายุ
    return db.get_user(session['username'])


def require_auth():
    user = current_user()
    if not user:
        return None, (jsonify({'message': 'Unauthorized'}), 401)
    return user, None


def require_ticket(tid):
    ticket = db.get_ticket(tid)
    if not ticket:
        return None, (jsonify({'message': 'Ticket not found'}), 404)
    return ticket, None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post('/api/login')
def login():
    body = request.get_json(silent=True) or {}
    username, password = body.get('username', ''), body.get('password', '')
    user = db.get_user(username)
    if not user or user['status'] != 'approved' or not check_password_hash(user['password_hash'], password):
        return jsonify({'message': 'Invalid username or password'}), 401
    token = secrets.token_hex(16)
    TOKENS[token] = {'username': user['username'], 'last_seen': datetime.now()}
    return jsonify({
        'token': token, 'firstName': user['first_name'], 'lastName': user['last_name'],
        'team': user['team'], 'isAdmin': bool(user['is_admin']),
        'idleTimeoutMinutes': IDLE_TIMEOUT_MINUTES,
    })


@app.post('/api/register')
def register():
    body = request.get_json(silent=True) or {}
    username = body.get('username', '')
    if not username or db.get_user(username):
        return jsonify({'message': 'Username already exists'}), 400
    db.create_user(
        username, generate_password_hash(body.get('password', '')),
        body.get('firstName', ''), body.get('lastName', ''), body.get('team'),
        is_admin=False, status='pending',
    )
    return jsonify({'message': 'Registered — waiting for admin approval.'})


@app.post('/api/logout')
def logout():
    auth = request.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else ''
    TOKENS.pop(token, None)
    return '', 204


# ---------------------------------------------------------------------------
# Ticket endpoints
# ---------------------------------------------------------------------------

@app.get('/api/tickets')
def get_tickets():
    user, err = require_auth()
    if err:
        return err
    rows = db.list_all_tickets()
    groups = {tag: [] for tag in DB_OWNER_GROUPS}
    unassigned = []
    for row in rows:
        ticket = db_ticket_to_json(row)
        if ticket['tag'] in groups:
            groups[ticket['tag']].append(ticket)
        else:
            unassigned.append(ticket)
    return jsonify({
        'uploadedGroups': groups,
        'uploadedUnassigned': unassigned,
        'uploadInfo': f'{len(rows)} tickets (tickets.db)',
    })


# "แจ้งเหตุด้วยตนเอง" (BRIEF ข้อ 7) — แทนที่ import Excel/CSV เดิม
# ผู้ใช้เลือก group/priority เอง (human judgement ตรงๆ ไม่ใช่ auto-classify ฝั่ง frontend)
# raw_syslog เป็น NULL เพื่อบอกว่า TT ใบนี้ไม่ได้มาจาก syslog จริง
@app.post('/api/tickets/upload')
def manual_entry():
    user, err = require_auth()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    node = (body.get('node') or '').strip()
    description = (body.get('description') or '').strip()
    owner_group = body.get('ownerGroup')
    priority = body.get('priority')
    if not node or not description or owner_group not in DB_OWNER_GROUPS or priority not in ('P1', 'P2', 'P3', 'P4'):
        return jsonify({'message': 'ต้องกรอกอุปกรณ์ / คำอธิบาย / เลือก Owner Group และ Priority ให้ครบ'}), 400

    # node ต้องเป็นเครื่องที่ SSH เข้าได้จริงเท่านั้น (Phase 5 ข้อ C-1) — ห้ามเชื่อ frontend
    # แม้ dropdown จะล็อกไว้แล้ว (ข้อห้าม #5) เพราะถ้าชื่อไม่ตรง mapping.DEVICE_SSH ตัว
    # diagnostics.diagnose_from_best_available_node() จะ "ตกไป SSH เข้า Core แทนเงียบๆ"
    # แล้วบันทึกผลของ Core เป็นผลวินิจฉัยของ TT ใบนี้ = ข้อมูลถูกแต่ผิดเครื่อง โดยไม่มีอะไรฟ้อง
    # (อาการเดียวกับบั๊ก #3 ของ Phase 4) — logic เดียวกับที่ /run-command ใช้อยู่แล้ว
    if node not in mapping.DEVICE_SSH:
        return jsonify({'message': f"อุปกรณ์ไม่ถูกต้อง — เลือกได้แค่ {', '.join(mapping.DEVICE_SSH)}"}), 400

    # interface ปล่อยว่างได้ (ไม่ใช่ทุกเหตุที่ระบุพอร์ตได้ เช่น ลูกค้าแจ้งว่าเน็ตช้าทั้งสาขา)
    # แต่ถ้าระบุมาต้องเป็นพอร์ตที่มีจริงของ node นั้น — ชื่อที่สะกดผิด (เช่น 'Eternet0/0')
    # จะทำให้คำสั่ง show interface ล้มเหลวตอนวินิจฉัย โดยที่ระบบไม่รู้ว่าล้มเหลวเพราะอะไร
    interface = (body.get('interface') or '').strip()
    allowed_ifaces = mapping.NODE_INTERFACES.get(node, [])
    if interface and interface not in allowed_ifaces:
        return jsonify({'message': f"interface ไม่ถูกต้องสำหรับ {node} — เลือกได้แค่ {', '.join(allowed_ifaces)}"}), 400

    incident_number = db.create_manual_ticket(
        node=node, interface=interface, summary=description,
        owner_group=owner_group, priority=priority, reported_by=user['username'],
    )

    # ต้องกระตุ้น correlation เองที่นี่ (Phase 5 ข้อ C-2 ส่วนที่ 2) — check_and_correlate()
    # ถูกเรียกจาก syslog_server.py ที่เดียวในทั้งระบบ ถ้าไม่เรียกตรงนี้ TT ที่คนแจ้งเองจะไม่มี
    # วันถูกรวมเป็นเหตุเดียวกับ TT จาก syslog เลย (เช่น 2 Site ล่มจาก syslog + Site ที่ 3
    # ลูกค้าโทรแจ้ง -> ต้องยกทั้ง 3 ใบเป็น Network-Core P1 เหมือนกัน)
    # เรียกใช้ได้ ไม่ผิดข้อห้าม #1 เพราะไม่ได้ "แก้" ไฟล์ lib/correlation.py
    corr = correlation.check_and_correlate()
    if corr:
        log_activity(f"correlation: รวม {len(corr['correlated'])} TT เป็นเหตุที่ Core "
                     f"หลังรับแจ้งเหตุด้วยตนเอง {incident_number}")

    return jsonify({'ok': True, 'id': incident_number})


@app.patch('/api/tickets/<tid>/assign')
def assign_ticket(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    tag = (request.get_json(silent=True) or {}).get('tag')
    if user['is_admin']:
        db.assign_group(tid, tag, user['username'])
    else:
        db.propose_group(tid, tag)
    return '', 204


@app.patch('/api/tickets/<tid>/confirm-assign')
def confirm_assign(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.confirm_proposed_group(tid, user['username'])
    return '', 204


@app.patch('/api/tickets/<tid>/reject-assign')
def reject_assign(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.reject_proposed_group(tid)
    return '', 204


@app.patch('/api/tickets/<tid>/acknowledge')
def acknowledge_ticket(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.acknowledge_ticket(tid, user['username'])
    return '', 204


@app.patch('/api/tickets/<tid>/sub-status')
def sub_status(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    step = body.get('sub_status')
    if step == 'received':
        db.set_current_sub_status(tid, None)
    elif body.get('advance'):
        # กันกด "Resolved" ก่อนสัญญาณกลับมาจริง — ไม่งั้น event 'up' ที่มาทีหลังจะหา
        # ticket ไม่เจอ (status เป็น Resolved แล้วไม่ถูกนับเป็น "เหตุค้างอยู่" อีก) แล้ว
        # event_end_time จะเป็น NULL ตลอดไปทั้งที่สายกลับมาแล้วจริง
        # (เจอจริง 6 ส.ค. 2026 ดู docs/Plan.md ข้อ 3)
        if step == 'completed' and not ticket['event_end_time']:
            # TT ที่แจ้งด้วยตนเอง (raw_syslog IS NULL) ไม่มีทางมี event 'up' จาก syslog เลย
            # ด่านข้างบนจึงบล็อกมันไว้ตลอดกาล = ปิดงานไม่ได้เลยสักใบ (Phase 5 ข้อ C-5)
            # ทางออกที่เลือก: ให้เจ้าหน้าที่กรอกเวลาที่เหตุจบเองในช่อง Event Time ของขั้น
            # Resolved แล้วรับค่านั้นเป็น event_end_time — เป็นข้อมูลจากคน ไม่ใช่จากอุปกรณ์
            # ต้องประกาศข้อจำกัดนี้ในเล่ม
            manual_end = body.get('event_time') if not ticket['raw_syslog'] else None
            if manual_end:
                db.set_manual_event_end(tid, manual_end)
            else:
                return jsonify({
                    'message': (
                        'ยังปิดงานไม่ได้ — TT ที่แจ้งด้วยตนเองไม่มีสัญญาณจากอุปกรณ์บอกว่าเหตุจบ '
                        'กรุณากรอกช่อง Event Time ว่าเหตุจบเมื่อไหร่ แล้วกด Save ก่อน จึงจะกด Resolved ได้'
                        if not ticket['raw_syslog'] else
                        'ยังปิดงานไม่ได้ — สัญญาณ/สายยังไม่กลับมาปกติ ต้องรอ event ว่าสายกลับมาก่อนถึงจะกด Resolved ได้'
                    )
                }), 409
        technician_assigned = True if step == 'assigned' else None
        status = 'Resolved' if step == 'completed' else None
        db.set_current_sub_status(tid, step, technician_assigned=technician_assigned, status=status)
    db.add_ticket_event(
        tid, step, note=body.get('note'), event_time=body.get('event_time'),
        technician_name=body.get('technician_name'), technician_contact=body.get('technician_contact'),
        created_by=user['username'],
    )
    if body.get('reset_customer_update'):
        db.touch_last_reported(tid)
    return '', 204


@app.patch('/api/tickets/<tid>/log-update')
def log_update(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.touch_last_reported(tid)
    return '', 204


@app.post('/api/tickets/<tid>/handover')
def handover(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    # to_user_id is a username here (see handover_candidates) — that's exactly
    # the value pendingHandoverTo needs so the frontend's
    # `t.pendingHandoverTo === state.username` incoming-handover check works.
    db.set_handover(tid, body.get('to_user_id'))
    return '', 204


@app.get('/api/tickets/<tid>/handover-candidates')
def handover_candidates(tid):
    user, err = require_auth()
    if err:
        return err
    candidates = [
        {'id': u['username'], 'name': f"{u['first_name']} {u['last_name']}", 'team': u['team']}
        for u in db.list_users()
        if u['status'] == 'approved' and not u['is_admin'] and u['username'] != user['username']
    ]
    return jsonify(candidates)


@app.patch('/api/tickets/<tid>/handover/confirm')
def handover_confirm(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.confirm_handover(tid)
    return '', 204


@app.patch('/api/tickets/<tid>/handover/reject')
def handover_reject(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.reject_handover(tid)
    return '', 204


# ---------------------------------------------------------------------------
# Phase 3 — Deep Diagnostic + หน้า Analyze (docs/BRIEF_Phase3_Analyze.md ข้อ 4)
# ---------------------------------------------------------------------------

@app.get('/api/tickets/pending-review')
def pending_review():
    user, err = require_auth()
    if err:
        return err
    rows = db.list_pending_review_tickets()
    return jsonify({'tickets': [db_ticket_to_json(r) for r in rows]})


@app.get('/api/tickets/<tid>/diagnosis')
def get_diagnosis(tid):
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    runs = db.list_diagnostic_runs(tid)
    return jsonify({
        'causeSummary': ticket['cause_summary'],
        'causeSource': ticket['cause_source'],
        'evidenceLines': ticket['evidence_lines'],
        'rawOutput': ticket['raw_output'],
        'diagnoseState': ticket['diagnose_state'],
        'diagnosedAt': local_timestamp_to_iso(ticket['diagnosed_at']),
        # Phase 3.5 — ส่งคู่กันเสมอกับ causeSource เพื่อให้ UI บอกได้ว่าสาเหตุมาจากไหน
        'llmExplanation': ticket['llm_explanation'],
        'llmNote': ticket['llm_note'],
        'analyzedAt': local_timestamp_to_iso(ticket['analyzed_at']),
        'runs': [diagnostic_run_to_json(r) for r in runs],
        **disposition_to_json(ticket),
    })


@app.post('/api/tickets/<tid>/diagnose')
def request_diagnose(tid):
    """ปุ่ม Re-check — แค่ตั้ง diagnose_state='running' แล้วคืนทันที (ห้าม block ตามข้อห้าม #2)
    diagnose_worker.py เป็นคน poll เจอแล้วไป SSH จริงต่างหาก (ดู list_recheck_requested_tickets())
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    db.set_diagnose_state(tid, 'running')
    return '', 204


@app.post('/api/tickets/<tid>/run-command')
def run_command(tid):
    """Manual Investigation — รันคำสั่งเดียวที่คนเลือกจากหน้า Analyze

    ต่างจาก Re-check ตรงที่นี่เป็น action เดี่ยวที่คนกดรอผลตรงๆ (ไม่ใช่ auto-diagnosis พื้นหลัง)
    จึงยอม SSH แบบ synchronous ในนี้ได้ — ข้อห้าม #2 ห้ามเฉพาะการเรียกจาก syslog_server.py
    (ที่ต้องสร้าง TT ให้เสร็จภายใน 5 วิ) ไม่ได้ห้ามทุก endpoint ที่ SSH
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    device = body.get('device')
    command = body.get('command')

    # ห้ามเชื่อ frontend — validate กับ whitelist ทั้งคู่เสมอ (ข้อห้าม #5)
    if device not in mapping.DEVICE_SSH:
        return jsonify({'message': f"device ไม่ถูกต้อง — เลือกได้แค่ {', '.join(mapping.DEVICE_SSH)}"}), 400
    if command not in mapping.MANUAL_COMMANDS:
        return jsonify({'message': 'คำสั่งนี้ไม่อยู่ใน whitelist ที่อนุญาต'}), 400

    result = diagnostics.run_single_command(device, command, ticket['interface'])
    db.record_diagnostic_run(
        tid, device=device, command=result['command'], output=result['output'],
        success=result['success'], run_by=user['username'],
    )
    return jsonify({
        'device': device, 'command': result['command'],
        'output': result['output'], 'success': result['success'],
    })


@app.post('/api/tickets/<tid>/assess-disposition')
def assess_disposition(tid):
    """ให้ระบบ "เสนอ" ประเภทงานของ TT ใบนี้ — ยังไม่ใช่การตัดสิน

    ทำไมยอม SSH แบบ synchronous ตรงนี้: เหมือน /run-command คือเป็น action ที่คนกดแล้วรอผล
    ไม่ใช่งานพื้นหลัง ข้อห้าม #2 ห้ามเฉพาะเส้นทางที่ syslog_server.py เรียก (ต้องเสร็จใน 5 วิ)

    ⚠️ endpoint นี้ **เปลี่ยนได้เฉพาะคอลัมน์ disposition_* เท่านั้น** เพราะ db.update_disposition()
    บังคับไว้ด้วยโครงสร้าง — เรียก endpoint นี้กี่ครั้งก็ไม่มีทางเปลี่ยน status, owner_group
    หรือ raw_output ของ TT ได้ (เกณฑ์ข้อ 2 = ระบบไม่ลงมือเอง 100%)
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err

    # เวลาที่ TT เปิด ใช้กรอง log — ถ้าไม่มีจะไม่นับ log เป็นหลักฐานเลย
    # ป้องกันการสรุปว่า "หายเอง" จาก log เก่าของเหตุการณ์อื่นที่ยังค้างในบัฟเฟอร์
    # ใช้ตัวแปลงเดิมของโปรเจ็ค (lib/correlation.parse_event_time) ไม่เขียน parser ใหม่
    # เพื่อให้รูปแบบเวลาตรงกับที่ lib/syslog_parser.py เขียนลง DB เสมอ
    since = correlation.parse_event_time(ticket['event_start_time']) \
        if ticket['event_start_time'] else None

    outcome = reachability.assess(
        node=ticket['node'], interface=ticket['interface'], since=since)

    db.update_disposition(tid, outcome['result'], probe_raw=outcome['raw'])

    # เสนอตัวเลขผลกระทบไปพร้อมกัน เพื่อให้ผู้ตรวจเห็นครบในหน้าจอเดียว ไม่ต้องกดสองรอบ
    # เป็นแค่ข้อเสนอ ยังไม่นับว่ามีผู้ยืนยัน จนกว่าจะกดบันทึกที่หน้าจอ
    db.set_impact_suggestion(tid, impact_estimator.estimate(dict(ticket)))
    log_activity(f"{user['username']} สั่งประเมินประเภทงานของ {tid} "
                 f"-> ระบบเสนอ '{outcome['result']['disposition']}'")

    ticket = db.get_ticket(tid)
    return jsonify({
        **disposition_to_json(ticket),
        'probe': outcome['raw'],
        'sinceUsed': since.isoformat() if since else None,
    })


@app.patch('/api/tickets/<tid>/disposition')
def confirm_disposition(tid):
    """agent ยืนยันประเภทงาน — เป็นทางเดียวที่ค่าจะกลายเป็น "ยืนยันแล้ว"

    agent เลือกค่าที่ต่างจากที่ระบบเสนอได้ ระบบเก็บทั้งสองค่าไว้เทียบกันภายหลัง
    ไม่เขียนทับค่าที่ระบบเสนอ เพราะนั่นคือข้อมูลที่ใช้วัดความแม่นยำ (เกณฑ์ข้อ 3)
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err

    body = request.get_json(silent=True) or {}
    choice = body.get('disposition')

    # ห้ามเชื่อ frontend — ค่าต้องอยู่ในรายการที่กฎรู้จักเท่านั้น (ข้อห้าม #5)
    if choice not in disposition_rules.ROUTES:
        return jsonify({
            'message': 'ประเภทงานไม่ถูกต้อง — เลือกได้แค่ '
                       + ', '.join(disposition_rules.ROUTES)
        }), 400

    db.confirm_disposition(tid, choice, user['username'])

    # ยืนยันว่าเป็นงานที่ต้องแก้การตั้งค่า -> ร่าง PW ให้ L2 ทันที
    #
    # ร่างหลังจากคนยืนยันแล้วเท่านั้น ไม่ร่างตอนระบบเสนอ — เพราะเอกสารที่ส่งต่อให้อีกทีม
    # ต้องมีคนรับผิดชอบเสมอว่าใครเป็นคนตัดสินว่างานนี้เป็นงานของ L2
    # และถ้าเลือกแม่แบบไม่ได้ ระบบจะไม่ร่างมั่ว แต่บันทึกเหตุผลไว้ให้เห็น
    if choice == 'configuration':
        # อ่านแถวใหม่หลังบันทึกการยืนยัน ไม่ใช้ตัวที่อ่านไว้ตอนต้นฟังก์ชัน
        # ไม่งั้นชื่อผู้ยืนยัน (ซึ่งเพิ่งเขียนลงไป) จะยังไม่ติดมาในร่าง
        result = _build_pw_draft(tid, db.get_ticket(tid))
        if result['ok']:
            log_activity(f"ระบบร่าง PW ให้ {tid} ด้วยแม่แบบ {result['mop_key']} "
                         f"({result['title']}) — รอ L2 ตรวจ")
        else:
            log_activity(f"ระบบไม่ร่าง PW ให้ {tid}: {result['reason']}")

    if ticket['disposition'] and choice != ticket['disposition']:
        log_activity(f"{user['username']} แก้ประเภทงานของ {tid} "
                     f"จาก '{ticket['disposition']}' เป็น '{choice}'")
    else:
        log_activity(f"{user['username']} ยืนยันประเภทงานของ {tid} เป็น '{choice}'")

    return jsonify(disposition_to_json(db.get_ticket(tid)))


def _pw_allowed(ticket):
    """ร่าง PW ให้ TT ใบนี้ได้ไหม — คืน (ได้ไหม, เหตุผลถ้าไม่ได้)

    ร่างได้ตั้งแต่ระบบ "เสนอ" ว่าเป็นงานประเภท configuration ไม่ต้องรอคนยืนยันก่อน
    เพราะร่างเป็นแค่เอกสารตั้งต้นที่ยังไม่มีผลอะไร การให้เห็นเร็วช่วยให้ผู้ตรวจ
    ดูเนื้อหาประกอบการตัดสินใจได้ด้วยซ้ำ

    แต่ถ้าคน "ยืนยันไปแล้ว" ว่าเป็นประเภทอื่น ต้องไม่ร่าง — คำตัดสินของคนอยู่เหนือ
    ข้อเสนอของระบบเสมอ ไม่ใช่ว่าระบบเคยเสนอ configuration ไว้แล้วร่างได้ตลอดไป
    """
    confirmed = ticket['confirmed_disposition']
    if confirmed:
        if confirmed != 'configuration':
            return False, (f"ผู้ตรวจยืนยันว่า TT ใบนี้เป็นงานประเภท '{confirmed}' "
                           f"ซึ่งไม่ต้องใช้ PW")
        return True, None
    if ticket['disposition'] == 'configuration':
        return True, None
    return False, ('ร่าง PW ได้เฉพาะงานประเภท Configuration — '
                   'กดประเมินประเภทงานก่อน หรือเลือกประเภทเองแล้วยืนยัน')


def _build_pw_draft(tid, ticket, requested_by=None):
    """ร่าง PW ให้ TT ใบหนึ่ง แล้วบันทึกผล — ใช้ร่วมกันทั้งตอนยืนยันและตอนสั่งร่างเอง

    คืน dict ของ pw_builder ตรงๆ ผู้เรียกเอาไปตัดสินใจเองว่าจะแสดงอะไร
    บันทึกลง DB ทุกกรณี รวมถึงกรณีร่างไม่ได้ (เก็บเหตุผลไว้ใน pw_draft_note)
    """
    site = reachability.site_of(ticket['node'], ticket['interface'])
    result = pw_builder.build({
        'incident_number': tid,
        'node': ticket['node'],
        'interface': ticket['interface'],
        'cause_summary': ticket['cause_summary'],
        'evidence_lines': ticket['evidence_lines'],
        'summary': ticket['summary'],
        # ผู้ยืนยันว่างานนี้เป็นงานของ L2 = ผู้ขอเปลี่ยนแปลง (Change Requester)
        # เป็นค่าเดียวในกลุ่มรายชื่อที่ระบบรู้จริง ที่เหลือต้องให้เจ้าของงานกรอกเอง
        # ถ้ายังไม่มีคนยืนยัน ให้ใช้ชื่อคนที่กดสั่งร่างแทน เอกสารจะได้มีชื่อผู้ขอเสมอ
        'requested_by': ticket['disposition_confirmed_by'] or requested_by,
        'impact_values': json.loads(ticket['impact_values']) if ticket['impact_values'] else None,
        'impact_by': ticket['impact_by'],
        'classify_reason': ticket['classify_reason'],
        'peer_ip': (mapping.SITE_ADDRESSES.get(site or '') or {}).get('peer_ip'),
    })
    if result['ok']:
        db.set_pw_draft(tid, path=result['path'], mop_key=result['mop_key'])
    else:
        db.set_pw_draft(tid, path=None, mop_key=result.get('mop_key'), note=result['reason'])
    return result


@app.patch('/api/tickets/<tid>/impact')
def confirm_impact(tid):
    """ผู้ตรวจยืนยันหรือแก้ตัวเลขผลกระทบ (ชีท PW Info แถว 25-34)

    ตัวเลขชุดนี้ป้อนเข้าสูตรคำนวณ Severity ของแม่แบบ ซึ่งกำหนดลำดับการอนุมัติจริง
    ระบบจึง "เสนอ" ได้ แต่ต้องมีคนยืนยันก่อนที่จะถือว่าเป็นตัวเลขของเอกสาร

    ตรวจค่าที่ส่งมาทุกครั้งด้วย impact_estimator.sanitize() ไม่เชื่อหน้าจอ (ข้อห้าม #5)
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err

    body = request.get_json(silent=True) or {}
    values, error = impact_estimator.sanitize(body.get('values'))
    if error:
        return jsonify({'message': error}), 400

    db.set_impact_values(tid, values, user['username'])
    log_activity(f"{user['username']} ยืนยันตัวเลขผลกระทบของ {tid}")

    # ถ้าร่าง PW ไปแล้ว ต้องร่างใหม่ให้ตัวเลขในเอกสารตรงกับที่เพิ่งยืนยัน
    # ไม่งั้นไฟล์ที่ดาวน์โหลดได้จะยังเป็นตัวเลขชุดเก่าโดยไม่มีอะไรบอก
    ticket = db.get_ticket(tid)
    if ticket['pw_draft_path'] and _pw_allowed(ticket)[0]:
        _build_pw_draft(tid, ticket, requested_by=user['username'])

    return jsonify(_impact_to_json(db.get_ticket(tid)))


@app.post('/api/tickets/<tid>/draft-pw')
def draft_pw(tid):
    """สั่งร่าง PW ใหม่ด้วยตนเอง

    ทำไมต้องมีแยกจากปุ่มยืนยัน: ถ้าผูกการร่างไว้กับการยืนยันอย่างเดียว พอร่างไม่สำเร็จ
    (แม่แบบยังไม่ถูกคัดลอกมา สาเหตุยังสรุปไม่ได้ ฯลฯ) จะไม่มีทางสั่งร่างซ้ำได้อีกเลย
    เพราะยืนยันไปแล้วและช่องถูกล็อก — ต้องมีทางแก้ที่ไม่ต้องรื้อ TT ทิ้ง

    ร่างได้ตั้งแต่ระบบเสนอว่าเป็น configuration ไม่ต้องรอคนยืนยัน (ดู _pw_allowed)
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err

    allowed, reason = _pw_allowed(ticket)
    if not allowed:
        return jsonify({'message': reason}), 400

    result = _build_pw_draft(tid, ticket, requested_by=user['username'])
    if result['ok']:
        log_activity(f"{user['username']} สั่งร่าง PW ให้ {tid} ด้วยแม่แบบ {result['mop_key']}")
    else:
        log_activity(f"{user['username']} สั่งร่าง PW ให้ {tid} แต่ระบบไม่ร่าง: {result['reason']}")
    return jsonify(disposition_to_json(db.get_ticket(tid)))


@app.get('/api/tickets/<tid>/pw-draft')
def download_pw_draft(tid):
    """ดาวน์โหลดร่าง PW ของ TT ใบนี้

    ส่งไฟล์จาก path ที่ "ระบบบันทึกไว้เอง" เท่านั้น ไม่รับชื่อไฟล์จากผู้เรียก
    ถ้ารับชื่อไฟล์มาจากหน้าจอ จะกลายเป็นช่องให้ขอไฟล์อื่นในเครื่องได้ (path traversal)
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err

    path = ticket['pw_draft_path']
    if not path or not os.path.exists(path):
        return jsonify({'message': ticket['pw_draft_note']
                        or 'ยังไม่มีร่าง PW สำหรับ TT ใบนี้'}), 404
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


@app.patch('/api/tickets/<tid>/review')
def review_ticket(tid):
    """ปุ่ม "ตรวจสอบแล้ว · ส่งเข้า In Progress" — ไม่ใช่ปุ่มเปลี่ยนหน้า เป็นการบันทึกว่ามีคนตรวจ
    แล้วเลื่อน TT เข้า In Progress จริง ถ้าไม่กดปุ่มนี้ TT จะไม่โผล่ในหน้า In Progress เลย
    (checkpoint ข้อ 10) — แทนที่ /api/tickets/<tid>/acknowledge เดิมสำหรับเส้นทางนี้
    """
    user, err = require_auth()
    if err:
        return err
    ticket, err = require_ticket(tid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    group = body.get('group')
    if group and group not in DB_OWNER_GROUPS:
        return jsonify({'message': 'Owner Group ไม่ถูกต้อง'}), 400
    db.mark_reviewed(tid, user['username'], group)
    return '', 204


# ---------------------------------------------------------------------------
# Stats — % ความแม่นยำการจำแนก, จำนวนที่ไม่มั่นใจ, เวลาเฉลี่ยถึงมีคนรับงาน (BRIEF ข้อ 6)
# ---------------------------------------------------------------------------

@app.get('/api/stats')
def stats():
    user, err = require_auth()
    if err:
        return err
    rows = db.list_all_tickets()

    # % ความแม่นยำต้องนับเฉพาะ TT ที่ผ่าน classifier จริง (Phase 5 ข้อ C-3)
    # TT ที่แจ้งด้วยตนเอง (raw_syslog IS NULL) คนเลือก Owner Group เองตั้งแต่ตอนสร้าง และ
    # create_manual_ticket() ก็เขียน confirmed_by/confirmed_at ให้เลยโดยไม่มี corrected_group
    # ถ้านับเข้าไปจะกลายเป็น "จำแนกถูก" ฟรีทุกใบ -> ยิ่งแจ้งเองมาก accuracy ยิ่งเข้าใกล้ 100%
    # ทั้งที่ระบบไม่ได้จำแนกอะไรเลย ตัวเลขที่ใช้ตอบเกณฑ์ข้อ 3 ของเล่มจึงจะสูงเกินจริง
    manual = [r for r in rows if not r['raw_syslog']]
    confirmed = [r for r in rows if r['confirmed_by'] and r['raw_syslog']]
    correct = [r for r in confirmed if not r['corrected_group']]
    accuracy_pct = round(100 * len(correct) / len(confirmed), 1) if confirmed else None

    low_confidence = [r for r in rows if r['confidence'] is not None and r['confidence'] < 50]

    ack_deltas = []
    for r in rows:
        if not r['acknowledged_at']:
            continue
        start = correlation.parse_event_time(r['event_start_time'])
        try:
            ack = datetime.strptime(r['acknowledged_at'], '%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError):
            ack = None
        if start and ack:
            ack_deltas.append((ack - start).total_seconds())
    avg_ack_seconds = round(sum(ack_deltas) / len(ack_deltas), 1) if ack_deltas else None

    return jsonify({
        'totalTickets': len(rows),
        'confirmedTickets': len(confirmed),
        # แสดงจำนวนที่ถูกกันออกให้เห็น ไม่ซ่อน — หลักการเดียวกับที่ Phase 3.5 จงใจโชว์
        # llm_note ตอนปฏิเสธคำตอบของโมเดล คนอ่านตัวเลขต้องรู้ว่ามีอะไรไม่ถูกนับบ้าง
        'manualTickets': len(manual),
        'classificationAccuracyPct': accuracy_pct,
        'lowConfidenceCount': len(low_confidence),
        'avgAcknowledgeSeconds': avg_ack_seconds,
    })


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.get('/api/admin/users/pending')
def pending_users():
    user, err = require_auth()
    if err:
        return err
    return jsonify([user_to_json(u) for u in db.list_users() if u['status'] == 'pending'])


@app.patch('/api/admin/users/<int:uid>/approve')
def approve_user(uid):
    user, err = require_auth()
    if err:
        return err
    target = next((u for u in db.list_users() if u['id'] == uid), None)
    if not target:
        return jsonify({'message': 'User not found'}), 404
    db.set_user_status(uid, 'approved')
    log_activity(f"{user['first_name']} approved user {target['username']}")
    return '', 204


@app.patch('/api/admin/users/<int:uid>/reject')
def reject_user(uid):
    user, err = require_auth()
    if err:
        return err
    target = next((u for u in db.list_users() if u['id'] == uid), None)
    if not target:
        return jsonify({'message': 'User not found'}), 404
    db.set_user_status(uid, 'rejected')
    log_activity(f"{user['first_name']} rejected user {target['username']}")
    return '', 204


@app.get('/api/admin/activity-log')
def activity_log():
    user, err = require_auth()
    if err:
        return err
    return jsonify(list(reversed(ACTIVITY_LOG)))


# ---------------------------------------------------------------------------
# Public tracking endpoint (no auth — token is the access control)
# ---------------------------------------------------------------------------

@app.get('/api/track/<token>')
def track(token):
    ticket = db.find_by_track_token(token)
    if not ticket:
        return jsonify({'message': 'Not found'}), 404
    return jsonify({
        'id': ticket['incident_number'],
        'description': ticket['summary'],
        'ticketReceivedAt': syslog_timestamp_to_iso(ticket['event_start_time']),
        'technicianAssigned': bool(ticket['technician_assigned']),
        'currentSubStatus': ticket['current_sub_status'],
    })


if __name__ == '__main__':
    tickets = db.list_all_tickets()
    for t in tickets:
        db.ensure_track_token(t['incident_number'])
    print('Demo logins: admin/admin123 (admin), agent.soc/demo123, agent.l1/demo123')
    print(f'{len(tickets)} tickets loaded from tickets.db')
    print('Track links (open in track.html?token=...) — first 5 shown, rest via SQLite:')
    for t in tickets[:5]:
        print(f"  {t['incident_number']}: token={db.ensure_track_token(t['incident_number'])}")
    app.run(host='127.0.0.1', port=8000, debug=True)
