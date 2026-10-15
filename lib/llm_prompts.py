r"""
Phase 3.5 - เก็บ prompt แบบ A และ B แยกจาก logic (docs/Plan.md 3.5.3 ขั้นที่ 2)

แนวคิดเดียวกับที่ Phase 2 แยก mapping.py (config) ออกจาก classifier.py (logic):
อยากปรับถ้อยคำที่ส่งให้โมเดล -> แก้ไฟล์นี้ไฟล์เดียว ไม่ต้องแตะ analyzer.py

มีอะไรในไฟล์นี้:
  - prompt แบบ A (ตีความ)  : ใช้เมื่อ lib/cause_rules.py สรุปไม่ได้ (llm_status='pending')
  - prompt แบบ B (เรียบเรียง): ใช้ทุกกรณี ทั้งที่กฎสรุปได้และที่ A สรุปให้
  - ตัวแกะรูปแบบคำตอบ (parse_*) : ผูกกับรูปแบบใน prompt โดยตรง จึงเก็บไว้คู่กัน
    ถ้าแก้รูปแบบใน prompt ต้องแก้ parser ในไฟล์เดียวกันนี้ให้ตรงกันเสมอ

⚠️ ภาษาของ output เป็นภาษาอังกฤษล้วน เพราะหน้า Analyze เป็นอังกฤษทั้งหน้าแล้วตั้งแต่
   Phase 3.9 (docs/Plan.md 3.9) — คอมเมนต์ในโค้ดยังเป็นไทยตามข้อตกลงโปรเจกต์

ทดสอบได้เองโดยไม่ต้องเปิด Ollama และไม่ต้องเปิด lab: py lib\llm_prompts.py
"""
import re

# ---------------------------------------------------------------------------
# คำที่โมเดลต้องตอบกลับมาเมื่อข้อมูลไม่พอสรุป — analyzer.py จะเช็คคำนี้แล้วตีกลับเป็น
# "ไม่สามารถสรุปได้ กรุณาตรวจสอบด้วยตนเอง" (กลไกกันตอบมั่วข้อ 3 ใน 3.5.3 ขั้นที่ 3)
#
# เหตุผลที่ใช้คำเฉพาะแทนการเดาจากข้อความ: ถ้าปล่อยให้โมเดลเขียนอิสระว่า "ไม่แน่ใจ"
# เราจะต้องมานั่งเดาว่าประโยคไหนแปลว่าไม่แน่ใจ — บังคับให้ตอบคำนี้คำเดียวตรวจง่ายกว่ามาก
# ---------------------------------------------------------------------------
INSUFFICIENT = 'INSUFFICIENT_DATA'

# จำนวนบรรทัดหลักฐานสูงสุดที่ยอมรับจากโมเดล — กันกรณีโมเดลลอกผลทั้งก้อนกลับมาแทนที่จะ
# เลือกเฉพาะบรรทัดที่เกี่ยวข้อง (เจอบ่อยกับโมเดลเล็ก) ถ้าเกินนี้ถือว่ามันไม่ได้เลือกจริง
MAX_EVIDENCE_LINES = 10

# เส้นคั่นบล็อกผล show command ใน prompt
#
# ⚠️ เดิมใช้ '---' แล้วเจอปัญหาจริงใน live test 10 ส.ค. 2026: โมเดลคัดลอกเส้นคั่นมาเป็น
# บรรทัดหลักฐานด้วย ทำให้ตัวตรวจปฏิเสธทั้งใบ (เส้นคั่นไม่มีอยู่ใน raw_output จริง)
# แก้สองชั้น: ตั้งชื่อเส้นคั่นให้ชัดว่าไม่ใช่เนื้อหา + กรองทิ้งตอนแกะคำตอบ
BEGIN_MARK = '===== BEGIN DEVICE OUTPUT ====='
END_MARK = '===== END DEVICE OUTPUT ====='

# ข้อความที่ lib/llm_client.shrink_to_fit() แทรกกลาง prompt เมื่อผลลัพธ์ยาวเกินเพดาน
# — เป็นข้อความของเรา ไม่ใช่ของอุปกรณ์ จึงอ้างเป็นหลักฐานไม่ได้เช่นกัน
# ⚠️ ต้องตรงกับ TRUNCATION_MARK ใน llm_client.py (จงใจไม่ import ข้ามไฟล์เพื่อไม่ให้
#    ไฟล์ prompt ต้องพึ่งไฟล์ที่คุยกับเครือข่าย) — แก้ที่ไหนต้องแก้อีกที่ด้วยเสมอ
TRUNCATION_PHRASE = 'truncated to fit model context'

# ---------------------------------------------------------------------------
# prompt แบบ A — ตีความ
# ใช้เมื่อ: กฎใน lib/cause_rules.py สรุปไม่ได้ (cause_source='undetermined')
# input   : ผลดิบจาก 3 show command + ข้อมูล TT เท่าที่มี
# output  : สาเหตุที่เป็นไปได้ + บรรทัดจริงที่ใช้อ้าง (หรือ INSUFFICIENT_DATA)
# ---------------------------------------------------------------------------
INTERPRET_SYSTEM = f"""You are a network operations assistant for a Cisco IOS network.
Your only job is to read raw command output from a router and state the most likely cause of an incident.

Hard rules you must never break:
1. Use ONLY the command output given to you. Never use outside knowledge to invent a cause.
2. Every conclusion must be backed by lines copied EXACTLY from the DEVICE COMMAND OUTPUT
   section, character for character.
3. The incident context (device name, interface, syslog message) is background only.
   It is NOT evidence. Never quote the syslog message as evidence — it is the alert that
   started the ticket, not proof of what is wrong. Quote device command output only.
4. If the device command output does not contain enough information to identify a cause,
   reply with exactly {INSUFFICIENT} and nothing else. Guessing is worse than admitting
   uncertainty, and so is quoting the alert back at us instead of real evidence.
5. Every line you quote as evidence must itself show something wrong: a down state, a
   failure, or a counter greater than zero. Lines showing "up", "is up", or zero counters
   prove nothing is wrong and must never be quoted to support a fault.
6. If every line in the device output shows a healthy state (up, zero errors), then this
   output does not explain the fault. Reply {INSUFFICIENT}. Never restate a healthy status
   as if it were a cause, and never claim a state that the output does not show.
7. Do not recommend actions, do not assign teams, do not set priority. Cause only.
8. Answer in English, in the exact format shown. No greetings, no extra commentary.

Answer format (exactly two labelled sections):
CAUSE: <one sentence, max 20 words>
EVIDENCE: <one or more lines copied verbatim from the device command output, one per line>"""

INTERPRET_USER = """Incident context (background only — NOT evidence, never quote this section):
- Device reporting the fault: {node}
- Interface involved: {interface}
- Syslog message that opened the ticket: {summary}

DEVICE COMMAND OUTPUT (the only section you may quote as evidence).
The two marker lines below are not part of the output. Never quote them.
{begin_mark}
{cause_output}
{end_mark}

State the most likely cause based on the device command output above,
or reply {insufficient} if that output is not enough."""


def build_interpret_prompt(cause_output, node=None, interface=None, summary=None):
    """สร้าง prompt แบบ A — คืน (system, user) ให้ llm_client.py ส่งต่อให้ Ollama

    cause_output ต้องเป็น 'ผลที่สำเร็จจริง' เท่านั้น (ฟิลด์ cause_output จาก
    lib/diagnostics.diagnose_from_best_available_node) ห้ามส่ง raw_output ที่รวม
    ข้อความ error ตอน SSH เข้า Site ไม่ได้เข้ามาด้วย — เป็นบั๊กเดียวกับที่เจอกับ
    ชุดกฎเมื่อ 6 ส.ค. 2026 (docs/Plan.md 3.8 บั๊ก #2) LLM ก็หลงทางแบบเดียวกันได้
    """
    user = INTERPRET_USER.format(
        node=node or 'unknown',
        interface=interface or 'unknown',
        summary=summary or 'not available',
        cause_output=(cause_output or '').strip(),
        insufficient=INSUFFICIENT,
        begin_mark=BEGIN_MARK,
        end_mark=END_MARK,
    )
    return INTERPRET_SYSTEM, user


def _strip_code_fences(text):
    """ตัดบรรทัด ``` ที่โมเดลชอบครอบคำตอบมาให้ แม้ prompt จะไม่ได้ขอ

    ไม่ตัดทั้งบรรทัดที่ 'มี' ``` แต่ตัดเฉพาะบรรทัดที่ 'เป็น' รั้วโค้ดล้วนๆ —
    ป้องกันการเผลอลบบรรทัด output จริงที่บังเอิญมีอักขระนี้ปน
    """
    return '\n'.join(
        line for line in text.splitlines()
        if not line.strip().startswith('```')
    )


def _is_quotable(line):
    """บรรทัดนี้เป็น 'เนื้อหาจริง' ที่อ้างเป็นหลักฐานได้ไหม

    ทิ้ง 2 อย่าง: เส้นคั่นบล็อกของเราเอง และบรรทัดที่มีแต่เครื่องหมายไม่มีตัวอักษรเลย
    (เช่น '---', '======') — โมเดลลอกมาบ่อยเพราะมันเห็นเป็นส่วนหนึ่งของข้อความที่ให้ไป
    """
    stripped = (line or '').strip()
    if not stripped:
        return False
    if stripped in (BEGIN_MARK, END_MARK):
        return False
    if TRUNCATION_PHRASE in stripped.lower():
        return False
    return any(ch.isalnum() for ch in stripped)


def parse_interpretation(response):
    """แกะคำตอบแบบ A -> dict {'cause_summary', 'evidence_lines'} หรือ None ถ้าสรุปไม่ได้

    คืน None เมื่อ: ตอบ INSUFFICIENT_DATA, ตอบว่างเปล่า, ตอบผิดรูปแบบจนหา CAUSE: ไม่เจอ,
    หรือ 'มี CAUSE แต่ไม่มี EVIDENCE เลย' — ทั้งหมดถือเป็น 'สรุปไม่ได้' เหมือนกัน ไม่เดาต่อ

    ⚠️ เหตุผลที่ไม่มี evidence = ตีเป็นสรุปไม่ได้: เกณฑ์ข้อ 4 ของเล่มบังคับว่าผลวิเคราะห์
    ต้องแสดงคู่หลักฐานเสมอ 100% — คำตอบที่ไม่มีบรรทัดอ้างอิงจึงใช้ไม่ได้ตั้งแต่ต้น
    ไม่ว่าเนื้อหาจะฟังดูสมเหตุสมผลแค่ไหน

    ⚠️ ฟังก์ชันนี้ไม่ได้ตรวจว่าบรรทัดหลักฐาน 'มีอยู่จริงใน raw_output ไหม' — โมเดลแต่ง
    บรรทัดขึ้นมาเองได้ การตรวจสอบนั้นเป็นหน้าที่ของ analyzer.py (3.5.3 ขั้นที่ 3)
    """
    text = _strip_code_fences((response or '').strip()).strip()
    if not text or INSUFFICIENT in text:
        return None

    cause, evidence = None, []
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith('CAUSE:'):
            cause = stripped[len('CAUSE:'):].strip()
            section = 'cause'
        elif upper.startswith('EVIDENCE:'):
            first = stripped[len('EVIDENCE:'):].strip()
            if first:
                evidence.append(first)
            section = 'evidence'
        elif section == 'evidence' and stripped:
            evidence.append(stripped)

    evidence = [line for line in evidence if _is_quotable(line)]

    # ต้องมีครบทั้งสาเหตุและหลักฐาน ขาดอย่างใดอย่างหนึ่ง = ใช้ไม่ได้
    if not cause or not evidence:
        return None

    return {
        'cause_summary': cause,
        'evidence_lines': '\n'.join(evidence[:MAX_EVIDENCE_LINES]),
    }


# ---------------------------------------------------------------------------
# prompt แบบ B — เรียบเรียง
# ใช้เมื่อ: ทุกกรณี (ทั้งสาเหตุที่มาจากกฎ และที่มาจาก A)
# input   : cause_summary ที่สรุปมาแล้ว + evidence + ข้อมูล TT
# output  : ข้อความ 2-3 บรรทัดที่เจ้าหน้าที่อ่านแล้วเข้าใจทันที
#
# ⚠️ ขั้นนี้ห้ามเพิ่มข้อเท็จจริงใหม่เด็ดขาด — หน้าที่เดียวคือ "เปลี่ยนภาษาเครื่องเป็น
#    ภาษาคน" ถ้าปล่อยให้ตีความเพิ่มตรงนี้ จะกลายเป็นช่องให้ตอบมั่วโดยไม่มีใครตรวจ
#    (นี่คือเหตุผลที่ทางถอย A ใน 3.5.6 ตัดเหลือแค่ prompt นี้แล้วยังปลอดภัยอยู่)
# ---------------------------------------------------------------------------
EXPLAIN_SYSTEM = """You are writing a short note for a network operations engineer who has 10 seconds to read it.

Hard rules you must never break:
1. Rephrase ONLY what you are given. Never add a cause, a fact, or a recommendation that is not already stated.
2. Never contradict or soften the given cause. It has already been verified.
3. Never state that an interface is up or down unless the verified cause or the supporting
   lines say so. If they describe error counters, write about error counters — do not
   assume the interface went down.
4. Do not assign teams and do not set priority. Those are decided elsewhere.
5. Plain English, 2 to 3 short sentences, no bullet points, no headings, no greeting.
6. Write for someone who knows networking. Do not explain what an interface is."""

# ⚠️ จงใจไม่ส่งข้อความ syslog เข้ามาในนี้ (ต่างจาก prompt A ที่ส่งเป็น context)
# ที่มา: live test 10 ส.ค. 2026 — เมื่อก่อนส่ง "Original syslog: %LINK-3-UPDOWN ...
# changed state to down" เข้ามาด้วย โมเดลก็หยิบคำว่า down มาเขียนว่า "Interface is down"
# ทั้งที่ผลจริงบอกว่า interface ยัง up (เจอ 2 ใน 4 เคส) — prompt นี้มีหน้าที่แค่เปลี่ยน
# ภาษาของสาเหตุที่ยืนยันแล้ว ไม่ต้องรู้จัก syslog เลย ตัดออกคือทางแก้ที่ตรงต้นเหตุที่สุด
EXPLAIN_USER = """Incident:
- Device: {node}
- Interface: {interface}
- Verified cause: {cause_summary}
- Supporting output lines:
{evidence_lines}

Rewrite the verified cause as 2-3 short sentences an engineer can act on.
Describe only what the cause and the supporting lines actually say."""


def build_explain_prompt(cause_summary, evidence_lines=None, node=None, interface=None):
    """สร้าง prompt แบบ B — คืน (system, user)

    ใช้ได้กับสาเหตุจากทั้งสองทาง (กฎ หรือ A) เพราะ input เหมือนกันหมดคือ
    'สาเหตุที่ยืนยันแล้ว + บรรทัดหลักฐาน' — จงใจไม่บอกโมเดลว่าสาเหตุมาจากไหน
    เพื่อไม่ให้มันปรับน้ำหนักความมั่นใจเองตามแหล่งที่มา

    ⚠️ ไม่รับ summary (ข้อความ syslog) เป็นพารามิเตอร์โดยเจตนา — ดูเหตุผลเหนือ EXPLAIN_USER
    """
    user = EXPLAIN_USER.format(
        node=node or 'unknown',
        interface=interface or 'unknown',
        cause_summary=(cause_summary or '').strip(),
        evidence_lines=(evidence_lines or '(none provided)').strip(),
    )
    return EXPLAIN_SYSTEM, user


def parse_explanation(response):
    """แกะคำตอบแบบ B -> string ที่พร้อมโชว์ หรือ None ถ้าโมเดลไม่ตอบอะไรที่ใช้ได้

    ตัดบรรทัดว่างและ bullet ที่โมเดลชอบใส่มาเองแม้สั่งห้ามแล้ว (พฤติกรรมปกติของ LLM)
    """
    text = _strip_code_fences((response or '').strip()).strip()
    if not text or INSUFFICIENT in text:
        return None

    cleaned = []
    for line in text.splitlines():
        stripped = line.strip().lstrip('-*• ').strip()
        # ตัดเลขนำหน้าแบบ "1. " / "2) " ที่โมเดลใส่มาเองแม้สั่งห้าม bullet แล้ว
        stripped = re.sub(r'^\d+[.)]\s+', '', stripped)
        if stripped:
            cleaned.append(stripped)

    return ' '.join(cleaned) if cleaned else None


# ---------------------------------------------------------------------------
# ทดสอบเองไม่ต้องพึ่ง Ollama และไม่ต้องเปิด lab -- รัน: py lib\llm_prompts.py
# ทดสอบว่า "สร้าง prompt ถูก" และ "แกะคำตอบถูก" เท่านั้น ไม่ได้ทดสอบคุณภาพคำตอบโมเดล
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = 0
    total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    print("=== prompt แบบ A (ตีความ) ===\n")

    system, user = build_interpret_prompt(
        cause_output="Ethernet0/1 is up, line protocol is up\n     0 input errors, 0 CRC",
        node='Access-Site-B', interface='Ethernet0/1',
        summary='%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down',
    )
    check('Ethernet0/1 is up' in user, "ผลดิบถูกใส่ลงใน prompt จริง")
    check('Access-Site-B' in user, "ชื่อ node ถูกใส่ลงใน prompt")
    check(INSUFFICIENT in system and INSUFFICIENT in user, "บอกทางออก INSUFFICIENT_DATA ทั้งใน system และ user")
    check('Never use outside knowledge' in system, "มีข้อห้ามใช้ความรู้นอกข้อมูลที่ให้")
    check('EXACTLY' in system, "มีข้อบังคับให้คัดลอกบรรทัดหลักฐานตรงๆ")

    # regression: บั๊กจริงจาก live test 10 ส.ค. 2026 — โมเดลหยิบข้อความ syslog ในช่อง context
    # มาอ้างเป็นหลักฐาน แล้วถูกตัวตรวจปฏิเสธ (เพราะ syslog ไม่ได้อยู่ในผล show command)
    # ทำให้เคสที่มีเบาะแสจริงกลับสรุปไม่ได้ ต้องระบุให้ชัดว่าอ้างได้จากส่วนไหนเท่านั้น
    check('NOT evidence' in system, "system ต้องบอกชัดว่า incident context ไม่ใช่หลักฐาน")
    check('Never quote the syslog message as evidence' in system,
          "system ต้องห้ามอ้างข้อความ syslog เป็นหลักฐานโดยตรง")
    check('NOT evidence' in user and 'DEVICE COMMAND OUTPUT' in user,
          "user ต้องติดป้ายแยกให้ชัดว่าส่วนไหนอ้างได้ ส่วนไหนอ้างไม่ได้")

    # ไม่มีข้อมูลเลย ก็ยังต้องสร้าง prompt ได้ ไม่ระเบิด (ของจริงมี field ว่างได้)
    s2, u2 = build_interpret_prompt(None)
    check('unknown' in u2, "ข้อมูลว่างเปล่าต้องแทนด้วย 'unknown' ไม่ใช่ None หรือพัง")

    print("\n=== แกะคำตอบแบบ A ===\n")

    good = ("CAUSE: Port was administratively shut down by configuration\n"
            "EVIDENCE: Ethernet0/1 is administratively down, line protocol is down")
    r = parse_interpretation(good)
    check(r and r['cause_summary'].startswith('Port was administratively'), "แกะ CAUSE ได้ถูก")
    check(r and 'administratively down' in r['evidence_lines'], "แกะ EVIDENCE ได้ถูก")

    multi = ("CAUSE: Physical layer signal loss on the link\n"
             "EVIDENCE: Ethernet0/0 is down, line protocol is down\n"
             "     5 input errors, 3 CRC, 0 frame")
    r = parse_interpretation(multi)
    check(r and len(r['evidence_lines'].splitlines()) == 2, "EVIDENCE หลายบรรทัดเก็บครบทุกบรรทัด")

    fenced = ("```\n"
              "CAUSE: Port was administratively shut down\n"
              "EVIDENCE: Ethernet0/1 is administratively down, line protocol is down\n"
              "```")
    r = parse_interpretation(fenced)
    check(r is not None, "โมเดลครอบคำตอบด้วย ``` ต้องยังแกะได้")
    check(r and '```' not in r['evidence_lines'], "รั้วโค้ด ``` ต้องไม่ปนเข้าไปใน evidence_lines")

    check(parse_interpretation("CAUSE: Port shut down\nEVIDENCE:") is None,
          "มี CAUSE แต่ไม่มี EVIDENCE เลย ต้องได้ None (เกณฑ์ข้อ 4 บังคับต้องมีหลักฐานคู่เสมอ)")

    # regression: บั๊กจริงจาก live test 10 ส.ค. 2026 — โมเดลลอกเส้นคั่นบล็อกมาเป็นหลักฐาน
    # ทำให้ตัวตรวจใน analyzer ปฏิเสธทั้งใบ ทั้งที่บรรทัดอื่นถูกต้อง
    with_marks = ("CAUSE: Excessive collisions on the link\n"
                  f"EVIDENCE: {BEGIN_MARK}\n"
                  "     1247 output errors, 1247 collisions, 0 interface resets\n"
                  "---\n"
                  f"{END_MARK}")
    r = parse_interpretation(with_marks)
    # หมายเหตุ: parser strip ช่องว่างหน้าบรรทัดออกอยู่แล้ว (analyzer เทียบแบบยุบช่องว่าง
    # จึงไม่กระทบการตรวจหลักฐาน) — คาดหวังผลแบบ strip แล้วเท่านั้น
    check(r and r['evidence_lines'] == '1247 output errors, 1247 collisions, 0 interface resets',
          "เส้นคั่นบล็อกและบรรทัดที่มีแต่เครื่องหมาย ต้องถูกกรองทิ้ง เหลือเนื้อหาจริง")
    check(parse_interpretation(f"CAUSE: x\nEVIDENCE: ---\n{END_MARK}") is None,
          "ถ้าเหลือแต่เส้นคั่นล้วนๆ ต้องถือว่าไม่มีหลักฐาน")

    # บั๊กชนิดเดียวกับเส้นคั่น: ข้อความ '...[truncated to fit model context]...' ที่
    # llm_client แทรกกลาง prompt ตอนผลลัพธ์ยาวเกิน มีตัวอักษรจึงรอดตัวกรอง isalnum()
    # ถ้าโมเดลลอกมาจะไม่มีใน raw_output แล้วทำให้คำตอบทั้งใบถูกปฏิเสธ
    with_trunc = ("CAUSE: Physical layer signal loss\n"
                  "EVIDENCE: ...[truncated to fit model context]...\n"
                  "Ethernet0/0 is down, line protocol is down")
    r = parse_interpretation(with_trunc)
    check(r and r['evidence_lines'] == 'Ethernet0/0 is down, line protocol is down',
          "ข้อความ truncated ที่เราแทรกเอง ต้องถูกกรองทิ้ง ไม่ใช่ปล่อยไปให้ตัวตรวจปฏิเสธทั้งใบ")
    check(BEGIN_MARK in u2 and 'Never quote them' in u2,
          "prompt ต้องบอกชัดว่าเส้นคั่นไม่ใช่เนื้อหาและห้ามอ้าง")

    flood = "CAUSE: Something\nEVIDENCE: " + "\n".join(f"line {i}" for i in range(30))
    r = parse_interpretation(flood)
    check(r and len(r['evidence_lines'].splitlines()) == MAX_EVIDENCE_LINES,
          f"โมเดลลอกผลมาทั้งก้อน ต้องถูกตัดเหลือ {MAX_EVIDENCE_LINES} บรรทัด")

    check(parse_interpretation(f"{INSUFFICIENT}") is None, "ตอบ INSUFFICIENT_DATA ต้องได้ None")
    check(parse_interpretation("") is None, "ตอบว่างเปล่าต้องได้ None")
    check(parse_interpretation("I think the cable is broken.") is None,
          "ตอบผิดรูปแบบ (ไม่มี CAUSE:) ต้องได้ None ไม่ใช่พยายามเดาต่อ")
    check(parse_interpretation(f"CAUSE: something\nNote: {INSUFFICIENT}") is None,
          "มีคำว่า INSUFFICIENT_DATA ปนอยู่ที่ไหนก็ตาม ต้องตีเป็นสรุปไม่ได้ (ปลอดภัยไว้ก่อน)")

    print("\n=== prompt แบบ B (เรียบเรียง) ===\n")

    system_b, user_b = build_explain_prompt(
        cause_summary='Port administratively shut down',
        evidence_lines='Ethernet0/1 is administratively down, line protocol is down',
        node='Access-Site-B', interface='Ethernet0/1',
    )
    check('Port administratively shut down' in user_b, "สาเหตุที่ยืนยันแล้วถูกใส่ลง prompt")

    # regression: live test 10 ส.ค. 2026 — ส่งข้อความ syslog เข้า prompt B แล้วโมเดลหยิบ
    # คำว่า "down" มาเขียนว่า interface down ทั้งที่ผลจริงบอกว่า up (เจอ 2 ใน 4 เคส)
    check('%LINK' not in user_b and 'syslog' not in user_b.lower(),
          "prompt B ต้องไม่มีข้อความ syslog เลย (ไม่จำเป็นต่อการเรียบเรียง และทำให้เข้าใจผิด)")
    check('Never state that an interface is up or down' in system_b,
          "system ต้องห้ามระบุสถานะ up/down ที่ไม่ได้มาจากสาเหตุหรือหลักฐาน")
    check('Never add a cause' in system_b, "มีข้อห้ามเพิ่มข้อเท็จจริงใหม่")
    check('priority' in system_b.lower() and 'teams' in system_b.lower(),
          "ห้ามแตะ Owner Group / Priority (LLM ไม่มีสิทธิ์เปลี่ยนผลของ Phase 2)")

    print("\n=== แกะคำตอบแบบ B ===\n")

    check(parse_explanation("- Someone shut the port.\n- Check the change log.")
          == 'Someone shut the port. Check the change log.',
          "ตัด bullet ที่โมเดลใส่มาเองแม้สั่งห้าม แล้วรวมเป็นย่อหน้าเดียว")
    check(parse_explanation("1. Someone shut the port.\n2. Check the change log.")
          == 'Someone shut the port. Check the change log.',
          "ตัดเลขนำหน้าแบบ '1. ' ที่โมเดลใส่มาเอง")
    check(parse_explanation("   ") is None, "ตอบมีแต่ช่องว่างต้องได้ None")
    check(parse_explanation(INSUFFICIENT) is None, "ตอบ INSUFFICIENT_DATA ต้องได้ None")

    print(f"\nผลรวม: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case")
