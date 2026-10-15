r"""
Phase 3.5 - ตัวประสาน: กฎมาก่อนเสมอ แล้วค่อยเรียก LLM (docs/Plan.md 3.5.3 ขั้นที่ 3)

ลำดับการทำงาน (ห้ามสลับ):
    ผลดิบ -> lib/cause_rules.py (กฎ)
        ├─ กฎสรุปได้      -> ใช้ผลของกฎ  cause_source='rule'
        └─ กฎสรุปไม่ได้    -> prompt A (ตีความ) -> **ตรวจหลักฐาน** -> cause_source='llm'
                                                └─ ตรวจไม่ผ่าน -> 'undetermined' (ให้คนตรวจเอง)
    จากนั้นทุกเส้นทาง -> prompt B (เรียบเรียงให้คนอ่าน) — ล้มเหลวได้ ไม่กระทบสาเหตุ

หลักการที่ยึด 4 ข้อ:
  1. **กฎมาก่อนเสมอ** — LLM ไม่มีสิทธิ์แก้ผลที่กฎสรุปได้แล้ว แตะได้แค่ถ้อยคำที่ใช้เล่า
  2. **ไม่มีหลักฐาน = ไม่นับ** — คำตอบของ LLM ที่อ้างบรรทัดซึ่งไม่มีอยู่ใน raw_output จริง
     ถูกทิ้งทั้งใบ ไม่ว่าจะฟังดูสมเหตุสมผลแค่ไหน (เกณฑ์ข้อ 4 ของเล่ม)
  3. **LLM ล่ม ระบบต้องเดินต่อได้** — ทุกจุดที่เรียก LLM เผื่อ None ไว้หมด
  4. **ห้ามแตะ Owner Group / Priority** — ไฟล์นี้ไม่คืนสองค่านั้นเลยแม้แต่ค่าเดียว
     ผลของ Phase 2 จึงเป็นไปไม่ได้ที่จะถูก LLM เปลี่ยน (บังคับด้วยโครงสร้าง ไม่ใช่ด้วยวินัย)

ทดสอบได้เองโดยไม่ต้องเปิด Ollama (ใช้ client จำลอง): py lib\analyzer.py
"""
import re

try:
    from lib import cause_rules, llm_client, llm_prompts
except ImportError:          # กรณีรันไฟล์นี้ตรงๆ จากในโฟลเดอร์ lib
    import cause_rules
    import llm_client
    import llm_prompts

# บรรทัด log ที่ diagnose_worker.py สร้างขึ้นเอง (จากการ SSH เข้าไปตรวจ) ไม่ใช่เหตุการณ์จริง
# บนเครือข่าย — กรองทิ้งก่อนส่งให้โมเดล ด้วยเหตุผล 2 ข้อ:
#   1. กินพื้นที่ context ฟรีๆ (Plan 3.9 บั๊ก #4 วัดได้ 58 บรรทัด มีประโยชน์จริง 2 บรรทัด)
#   2. ทำให้โมเดลไขว้เขวไปสรุปเรื่องการ login ของตัวเราเองแทนที่จะดูสาเหตุ fault
# ⚠️ กรองเฉพาะตอนส่งให้โมเดล — raw_output ที่เก็บลง DB และที่โชว์ใน UI ยังเต็มเหมือนเดิม
# (เกณฑ์ข้อ 4 บังคับให้เจ้าหน้าที่เห็นผลดิบที่ไม่ถูกดัดแปลง)
_NOISE_RE = re.compile(r'%(SSH|SEC_LOGIN|SYS-6-LOGOUT|SYS-5-CONFIG_I)', re.IGNORECASE)

# บรรทัดหลักฐานที่สั้นกว่านี้ไม่นับ — กันกรณีโมเดลอ้างเศษข้อความอย่าง "0" หรือ "down"
# ซึ่งไปเจอใน raw_output ได้แทบทุกใบ ทำให้การตรวจหลักฐานผ่านแบบไร้ความหมาย
MIN_EVIDENCE_CHARS = 12

UNVERIFIED_NOTE = 'LLM output rejected: cited evidence not found in device output'
UNSUPPORTED_NOTE = 'LLM output rejected: cited evidence does not support the stated cause'
NO_FAULT_NOTE = 'LLM output rejected: stated cause does not describe a fault'

# ---------------------------------------------------------------------------
# ด่านที่ 2 — ตรวจว่าหลักฐาน "สนับสนุนข้อสรุป" ไม่ใช่แค่ "มีอยู่จริง"
#
# ⚠️ ที่มา: live test 10 ส.ค. 2026 โมเดลสรุปว่า "Ethernet0/1 is administratively down"
# โดยอ้างบรรทัด "Ethernet0/1  10.0.2.1  YES manual up  up" ซึ่ง**มีอยู่จริงในผล** จึงผ่าน
# verify_evidence() ได้สบาย ทั้งที่บรรทัดนั้นบอกว่า interface ปกติดี ตรงข้ามกับข้อสรุป
#
# บทเรียน: การพิสูจน์ว่า "ข้อความนี้มีอยู่จริง" กับ "ข้อความนี้สนับสนุนข้อสรุป" เป็นคนละ
# เรื่องกันโดยสิ้นเชิง ต้องตรวจทั้งสองชั้น
# ---------------------------------------------------------------------------

# คำที่บ่งชี้ว่า "มีอะไรผิดปกติ" — ใช้ทั้งฝั่งข้อสรุปและฝั่งหลักฐาน
# รายการคำฝั่ง "หลักฐาน" — เข้มกว่า เพราะต้องอ่านบรรทัดผลดิบจากอุปกรณ์
#
# ⚠️ จงใจไม่ใส่ 'error'/'errors' ที่นี่ — เพราะบรรทัด "0 input errors" มีคำว่า error
# แต่แปลว่าปกติดี การนับตัวเลขเป็นหน้าที่ของ _NONZERO_COUNTER_RE ที่เช็คค่าจริง
# (บทเรียนเดียวกับบั๊ก los/lost ของ Phase 2 — คำเดียวกันความหมายกลับกันได้)
_EVIDENCE_FAULT_WORDS = (
    'down', 'fail', 'failed', 'failure', 'fault', 'faulty', 'loss', 'lost', 'mismatch', 'unreachable',
    'notconnect', 'err-disabled', 'errdisable', 'flap', 'flapping', 'cut', 'disconnect',
    'disconnected', 'shut', 'shutdown', 'reset', 'drop', 'dropped', 'congestion',
    'collision', 'collisions', 'crc', 'overrun', 'timeout', 'degraded',
)

# รายการคำฝั่ง "ข้อสรุป" — กว้างกว่าได้ เพราะเป็นประโยคที่คนเขียนบรรยายปัญหา
# ไม่ใช่บรรทัดผลดิบที่มีตัวนับเป็น 0 ปนอยู่
#
# ⚠️ ที่มา: live test 10 ส.ค. 2026 โมเดลตอบว่า "has a high number of output errors"
# ซึ่งบรรยายความผิดปกติถูกต้อง แต่ถูกปฏิเสธเพราะใช้รายการคำเดียวกับฝั่งหลักฐานที่ตัด
# 'error' ออกไป — เหตุผลที่ตัดออกใช้ได้กับฝั่งหลักฐานเท่านั้น ไม่ใช่ฝั่งข้อสรุป
_CAUSE_FAULT_WORDS = _EVIDENCE_FAULT_WORDS + (
    'error', 'errors', 'issue', 'problem', 'excessive', 'abnormal', 'high',
    'degradation', 'misconfigured', 'misconfiguration', 'saturated', 'exceeded',
    # เพิ่มจากคำที่โมเดลใช้จริงตอนรัน compare_approaches.py รอบแรก (เคส C07 พอร์ตกระพริบ
    # โมเดลตอบถูกว่า "intermittent connectivity" แต่ถูกปฏิเสธเพราะไม่มีคำนี้ในรายการ)
    'intermittent', 'unstable', 'instability', 'interrupted', 'repeatedly',
)

# ⚠️ ข้อจำกัดเชิงระเบียบวิธีที่ต้องประกาศในเล่ม:
# รายการคำข้างบนถูกปรับจากคำที่โมเดลใช้จริง "ในชุดเคสเดียวกับที่ใช้รายงานความแม่นยำ"
# ซึ่งทำให้ตัวเลขที่ได้ดูดีกว่าความเป็นจริงเล็กน้อย (ปรับเครื่องมือให้เข้ากับข้อสอบ)
# → ชุดเคส 20-30 เคสของ Phase 6.2 ต้อง **สร้างขึ้นใหม่โดยไม่ปรับรายการคำตามผลของมัน**
#   แล้วรายงานตัวเลขจากชุดนั้นเป็นผลหลัก จึงจะเป็นการวัดที่ตรงไปตรงมา

# ตัวนับที่ "มากกว่า 0" ถือเป็นสัญญาณผิดปกติ — ต้องเช็คค่าด้วย ไม่ใช่แค่เจอคำว่า error
# เพราะ "0 input errors" มีคำว่า error แต่แปลว่าปกติดี (บทเรียนเดียวกับ _rule_errors)
_NONZERO_COUNTER_RE = re.compile(
    r'(\d+)\s+(?:input errors|output errors|CRC|runts|giants|frame|overrun|collisions|drops|resets|ignored)',
    re.IGNORECASE,
)


def _has_fault_signal(text, words=_EVIDENCE_FAULT_WORDS):
    """ข้อความนี้บ่งชี้ความผิดปกติไหม — จริงเมื่อเจอคำบ่งชี้ปัญหา หรือตัวนับที่มากกว่า 0"""
    lowered = (text or '').lower()

    for match in _NONZERO_COUNTER_RE.finditer(lowered):
        if int(match.group(1)) > 0:
            return True

    # ตัด "0 <คำ>" ออกก่อนหาคำบ่งชี้ กัน "0 collisions"/"0 CRC" ถูกนับเป็นสัญญาณผิดปกติ
    without_zeros = re.sub(r'\b0\s+\w+', ' ', lowered)
    return any(
        re.search(r'\b' + re.escape(word) + r'\b', without_zeros)
        for word in words
    )


def evidence_supports_cause(cause_summary, evidence_lines):
    """ตรวจว่าข้อสรุปกับหลักฐานไปด้วยกันได้ไหม — คืน (ok, เหตุผลที่ปฏิเสธ)

    ตรวจ 2 ข้อ:
      1. ข้อสรุปต้องพูดถึงความผิดปกติจริงๆ — ถ้าเขียนว่า "interface is up and operational"
         นั่นคือการเล่าสถานะซ้ำ ไม่ใช่สาเหตุ (เจอจริงในเคส 2 ของ live test)
      2. ต้องมีหลักฐานอย่างน้อย 1 บรรทัดที่บ่งชี้ความผิดปกติ — ถ้าทุกบรรทัดบอกว่าปกติดี
         แต่ข้อสรุปบอกว่าเสีย แสดงว่าโมเดลสรุปสวนหลักฐานตัวเอง (เจอจริงในเคส 3)

    ⚠️ นี่เป็นการตรวจเชิงคำ ไม่ใช่การเข้าใจความหมายจริง — จับได้เฉพาะความขัดแย้งแบบชัดๆ
    ไม่ได้การันตีว่าข้อสรุปถูกต้อง แค่กรองคำตอบที่ขัดแย้งกับหลักฐานตัวเองออกไปเท่านั้น
    การตัดสินสุดท้ายยังเป็นของเจ้าหน้าที่เสมอ (ต้องเขียนข้อจำกัดนี้ในเล่ม)
    """
    if not _has_fault_signal(cause_summary, _CAUSE_FAULT_WORDS):
        return False, NO_FAULT_NOTE

    lines = [l for l in (evidence_lines or '').splitlines() if l.strip()]
    if not any(_has_fault_signal(line) for line in lines):
        return False, UNSUPPORTED_NOTE

    return True, None


def filter_noise(text):
    """ตัดบรรทัด log ที่เกิดจากการที่ระบบ SSH เข้าไปตรวจเอง — ใช้ก่อนส่งให้โมเดลเท่านั้น"""
    if not text:
        return ''
    return '\n'.join(line for line in text.splitlines() if not _NOISE_RE.search(line))


# ข้อความคั่นที่ lib/diagnostics.diagnose_from_best_available_node() แทรกไว้ตอนที่ SSH เข้า
# Site ไม่ได้แล้วตกไปใช้ Core แทน — ต้องตรงกับที่นั่นเป๊ะๆ (แก้ที่ไหนต้องแก้อีกที่ด้วย)
FALLBACK_MARK = 'Falling back to Core for diagnosis.'


def recover_cause_output(raw_output):
    """กู้คืน 'ส่วนที่ SSH สำเร็จจริง' ออกจาก raw_output ที่เก็บไว้ใน DB

    ⚠️ ทำไมต้องมี: Phase 3 แยก cause_output (ผลที่สำเร็จ) ออกจาก raw_output (ผลเต็มรวม
    ข้อความ SSH ล้มเหลว) เพื่อไม่ให้คำว่า "unreachable" ปนไปทำให้สรุปสาเหตุผิด — แต่
    **เก็บลง DB แค่ raw_output อย่างเดียว** ตัวที่มาอ่านทีหลัง (analyze_pending.py) จึงต้อง
    แยกเองใหม่ ไม่งั้นบั๊ก #2 วันที่ 6 ส.ค. 2026 จะกลับมาอีกรอบ (ดู docs/Plan.md 3.8)

    ถ้าไม่มีข้อความคั่น = ไม่เคยมีการตกไปใช้ Core = ทั้งก้อนคือผลที่สำเร็จอยู่แล้ว คืนตามเดิม
    """
    text = raw_output or ''
    if FALLBACK_MARK not in text:
        return text
    return text.split(FALLBACK_MARK, 1)[1].strip()


def _normalize(text):
    """ยุบช่องว่างซ้ำและตัดหัวท้าย เพื่อเทียบข้อความแบบไม่ติดเรื่องการจัดคอลัมน์

    จำเป็นเพราะ parser ใน llm_prompts.py strip ช่องว่างหน้าบรรทัดไปแล้ว ขณะที่ผลจริงจาก
    Cisco เว้นวรรคจัดคอลัมน์ไว้เยอะ — ถ้าเทียบตรงๆ จะไม่ตรงทั้งที่เป็นบรรทัดเดียวกัน
    """
    return re.sub(r'\s+', ' ', (text or '')).strip().lower()


def verify_evidence(evidence_lines, raw_output):
    """ตรวจว่าทุกบรรทัดหลักฐานที่โมเดลอ้าง **มีอยู่จริง** ใน raw_output

    นี่คือกลไกกันตอบมั่วที่สำคัญที่สุดของ Phase 3.5 — prompt สั่งให้คัดลอกบรรทัดมาเป๊ะๆ
    ได้ก็จริง แต่ไม่มีอะไรบังคับโมเดลได้จริง การตรวจด้วยโค้ดตรงนี้คือสิ่งเดียวที่บังคับได้

    คืน (ok, missing_lines)
      ok = True ต่อเมื่อ **ทุก** บรรทัดผ่าน — ผ่านบางส่วนถือว่าไม่ผ่าน เพราะคำตอบที่ปนของ
      แต่งขึ้นมาแม้บรรทัดเดียวก็เชื่อถือไม่ได้ทั้งใบ
    """
    lines = [l for l in (evidence_lines or '').splitlines() if l.strip()]
    if not lines or not (raw_output or '').strip():
        return False, lines

    haystack = _normalize(raw_output)
    missing = []
    for line in lines:
        needle = _normalize(line)
        if len(needle) < MIN_EVIDENCE_CHARS or needle not in haystack:
            missing.append(line.strip())

    return (not missing), missing


def _explain(cause_summary, evidence_lines, node, interface, client):
    """เรียก prompt B เรียบเรียงให้คนอ่าน — คืน None ได้โดยไม่ทำให้ผลวินิจฉัยเสียหาย

    จงใจให้ล้มเหลวแบบเงียบ: ข้อความเรียบเรียงเป็นของ 'เสริม' ถ้าไม่มีก็แค่ UI แสดง
    cause_summary แบบดิบแทน ไม่ใช่เหตุให้ทั้งใบใช้ไม่ได้
    """
    if not cause_summary:
        return None
    system, user = llm_prompts.build_explain_prompt(
        cause_summary=cause_summary, evidence_lines=evidence_lines,
        node=node, interface=interface,
    )
    return llm_prompts.parse_explanation(client.chat(system, user))


def analyze(raw_output, cause_output=None, node=None, interface=None, summary=None,
            client=llm_client, use_llm=True):
    """วิเคราะห์ผลจาก Phase 3 หนึ่งใบ

    raw_output   : ผลเต็มสำหรับโชว์/ตรวจหลักฐาน (รวมข้อความ SSH ล้มเหลวถ้ามี)
    cause_output : เฉพาะผลที่สำเร็จจริง ใช้สรุปสาเหตุ — ถ้าไม่ส่งมาจะใช้ raw_output แทน
                   (แยกกันตามบทเรียนบั๊ก #2 วันที่ 6 ส.ค. 2026 — ดู Plan 3.8)
    client       : ฉีดเข้ามาได้เพื่อทดสอบโดยไม่ต้องเปิด Ollama
    use_llm      : False = โหมดกฎล้วน ใช้ตอนเปรียบเทียบ 3 แนวทางใน 3.5.3 ขั้นที่ 4

    คืน dict — **ไม่มี owner_group / priority โดยเจตนา** (หลักการข้อ 4):
        cause_summary, cause_source ('rule'|'llm'|'undetermined'), evidence_lines,
        llm_status ('done'|'pending'|'undetermined'), llm_explanation, llm_note
    """
    source_for_cause = cause_output if cause_output is not None else raw_output
    result = cause_rules.summarize(source_for_cause)

    out = {
        'cause_summary': result['cause_summary'],
        'cause_source': result['cause_source'],
        'evidence_lines': result['evidence_lines'],
        'llm_status': result['llm_status'],
        'llm_explanation': None,
        'llm_note': None,
    }

    # ปิด LLM หรือใช้ไม่ได้ -> คืนผลของกฎตามเดิม ระบบยังเดินต่อได้ (Checkpoint ข้อ 4)
    if not use_llm or client is None:
        return out

    # ---- เส้นทางที่ 1: กฎสรุปได้แล้ว -> LLM แตะได้แค่ถ้อยคำ ห้ามแตะสาเหตุ ----
    if result['cause_source'] == 'rule':
        out['llm_explanation'] = _explain(
            result['cause_summary'], result['evidence_lines'], node, interface, client,
        )
        return out

    # ---- เส้นทางที่ 2: กฎสรุปไม่ได้ -> ให้ LLM ตีความ แล้วตรวจหลักฐานทุกบรรทัด ----
    system, user = llm_prompts.build_interpret_prompt(
        cause_output=filter_noise(source_for_cause),
        node=node, interface=interface, summary=summary,
    )
    interpreted = llm_prompts.parse_interpretation(client.chat(system, user))

    if not interpreted:
        # โมเดลตอบ INSUFFICIENT_DATA / ตอบผิดรูปแบบ / เรียกไม่ติด — ทั้งหมดแปลว่ายังสรุปไม่ได้
        # คง llm_status='pending' ไว้ ให้ลองใหม่ได้ทีหลัง (เช่นตอน Ollama กลับมาเปิด)
        return out

    # ด่านที่ 1 — บรรทัดที่อ้างมี "อยู่จริง" ใน raw_output ไหม
    ok, missing = verify_evidence(interpreted['evidence_lines'], raw_output)
    if not ok:
        # ⚠️ จุดสำคัญที่สุดของทั้ง Phase: โมเดลอ้างบรรทัดที่ไม่มีอยู่จริง = ทิ้งคำตอบทั้งใบ
        # และเปลี่ยนเป็น 'undetermined' (ไม่ใช่ 'pending') เพราะลองแล้วและผลใช้ไม่ได้
        # ต้องให้คนเข้ามาตรวจ ไม่ใช่วนลองซ้ำไปเรื่อยๆ
        out['llm_status'] = 'undetermined'
        out['llm_note'] = f"{UNVERIFIED_NOTE}: {'; '.join(missing[:3])}"
        return out

    # ด่านที่ 2 — บรรทัดที่อ้าง "สนับสนุนข้อสรุป" ไหม (ผ่านด่าน 1 แล้วยังตกด่านนี้ได้)
    supported, reason = evidence_supports_cause(
        interpreted['cause_summary'], interpreted['evidence_lines'],
    )
    if not supported:
        out['llm_status'] = 'undetermined'
        out['llm_note'] = f"{reason}: {interpreted['cause_summary']}"
        return out

    out['cause_summary'] = interpreted['cause_summary']
    out['cause_source'] = 'llm'
    out['evidence_lines'] = interpreted['evidence_lines']
    out['llm_status'] = 'done'
    out['llm_explanation'] = _explain(
        interpreted['cause_summary'], interpreted['evidence_lines'], node, interface, client,
    )
    return out


# ---------------------------------------------------------------------------
# ทดสอบด้วย client จำลอง — ไม่ต้องเปิด Ollama ไม่ต้องเปิด lab
# รัน: py lib\analyzer.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = 0
    total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    class FakeClient:
        """client จำลอง — ตอบตามสคริปต์ที่กำหนด และจดไว้ว่าถูกเรียกกี่ครั้ง"""

        def __init__(self, *responses):
            self.responses = list(responses)
            self.calls = []

        def chat(self, system, user, **kwargs):
            self.calls.append({'system': system, 'user': user})
            return self.responses.pop(0) if self.responses else None

    ADMIN_DOWN = ("$ show interface Ethernet0/1\n"
                  "Ethernet0/1 is administratively down, line protocol is down\n"
                  "  Hardware is AmdP2, address is aabb.cc00.0100\n")

    # output ที่กฎทุกข้อ match ไม่ได้ (ตัวนับเป็น 0 ทั้งหมด ไม่มี keyword ใดเลย)
    UNKNOWN = ("$ show interface Ethernet0/2\n"
               "Ethernet0/2 is up, line protocol is up\n"
               "     0 input errors, 0 CRC, 0 frame, 0 overrun\n"
               "     Duplex mismatch detected by peer negotiation\n")

    print("=== เส้นทางที่ 1: กฎสรุปได้ ===\n")

    fake = FakeClient("The port was shut down on purpose. Check who changed it.")
    r = analyze(ADMIN_DOWN, node='Access-Site-B', interface='Ethernet0/1', client=fake)
    check(r['cause_source'] == 'rule', "กฎ match ต้องได้ cause_source='rule'")
    check(r['cause_summary'] == 'Port administratively shut down', "สาเหตุต้องมาจากกฎ ไม่ใช่จากโมเดล")
    check(r['llm_status'] == 'done', "llm_status='done' เพราะไม่ต้องรอ LLM ตีความแล้ว")
    check(r['llm_explanation'] == 'The port was shut down on purpose. Check who changed it.',
          "ต้องได้ข้อความเรียบเรียงจาก prompt B")
    check(len(fake.calls) == 1, "กฎสรุปได้แล้ว ต้องเรียกโมเดลแค่ครั้งเดียว (เรียบเรียงเท่านั้น)")
    check('Never add a cause' in fake.calls[0]['system'], "ครั้งเดียวที่เรียกต้องเป็น prompt B")

    print("\n=== หลักการข้อ 4: ห้ามแตะ Owner Group / Priority ===\n")

    check('owner_group' not in r and 'priority' not in r,
          "ผลลัพธ์ต้องไม่มี owner_group/priority เลย (บังคับด้วยโครงสร้าง)")

    # แม้โมเดลจะพยายามยัดคำสั่งเปลี่ยนกลุ่มมาในคำตอบ ก็ไปไม่ถึงไหนอยู่ดี
    fake2 = FakeClient("Reassign this to Network-Core and raise priority to P1.")
    r2 = analyze(ADMIN_DOWN, client=fake2)
    check(r2['cause_summary'] == 'Port administratively shut down',
          "โมเดลสั่งเปลี่ยนกลุ่ม/ความด่วน ต้องไม่กระทบสาเหตุที่กฎสรุปไว้")

    print("\n=== เส้นทางที่ 2: กฎสรุปไม่ได้ -> LLM ตีความ ===\n")

    good_answer = ("CAUSE: Duplex mismatch reported by the peer during negotiation\n"
                   "EVIDENCE: Duplex mismatch detected by peer negotiation")
    fake3 = FakeClient(good_answer, "The peer reports a duplex mismatch on this link.")
    r3 = analyze(UNKNOWN, node='Core', interface='Ethernet0/2', client=fake3)
    check(r3['cause_source'] == 'llm', "กฎสรุปไม่ได้ + LLM ตอบได้ ต้องได้ cause_source='llm'")
    check(r3['llm_status'] == 'done', "ตรวจหลักฐานผ่านแล้ว llm_status ต้องเป็น 'done'")
    check('Duplex mismatch' in r3['cause_summary'], "สาเหตุต้องมาจากคำตอบของโมเดล")
    check(len(fake3.calls) == 2, "ต้องเรียกโมเดล 2 ครั้ง (ตีความ + เรียบเรียง)")

    print("\n=== กลไกกันตอบมั่ว: หลักฐานที่ไม่มีอยู่จริง ===\n")

    hallucinated = ("CAUSE: Fiber cut between the core and the site\n"
                    "EVIDENCE: Ethernet0/2 is down, line protocol is down (fiber signal lost)")
    fake4 = FakeClient(hallucinated, "should never be used")
    r4 = analyze(UNKNOWN, client=fake4)
    check(r4['cause_source'] == 'undetermined', "หลักฐานไม่มีอยู่จริง ต้องทิ้งคำตอบทั้งใบ")
    check(r4['cause_summary'] is None, "ห้ามเก็บสาเหตุที่โมเดลแต่งขึ้นไว้เลยแม้แต่นิดเดียว")
    check(r4['llm_status'] == 'undetermined', "ลองแล้วใช้ไม่ได้ ต้องเป็น 'undetermined' ไม่ใช่ 'pending'")
    check(r4['llm_note'] and UNVERIFIED_NOTE in r4['llm_note'], "ต้องบันทึกเหตุผลที่ปฏิเสธไว้ให้ตรวจย้อนได้")
    check(len(fake4.calls) == 1, "คำตอบถูกปฏิเสธแล้ว ต้องไม่เรียก prompt B ต่อ")

    partial = ("CAUSE: Something happened\n"
               "EVIDENCE: Ethernet0/2 is up, line protocol is up\n"
               "     Fiber signal lost on the far end")
    fake5 = FakeClient(partial)
    r5 = analyze(UNKNOWN, client=fake5)
    check(r5['cause_source'] == 'undetermined',
          "จริง 1 บรรทัด ปลอม 1 บรรทัด ต้องถือว่าไม่ผ่านทั้งใบ")

    print("\n=== verify_evidence() โดยตรง ===\n")

    ok, missing = verify_evidence("Ethernet0/1 is administratively down, line protocol is down", ADMIN_DOWN)
    check(ok and not missing, "บรรทัดที่คัดลอกมาจริงต้องผ่าน")

    ok, _ = verify_evidence("   Ethernet0/1   is    administratively   down, line protocol is down  ", ADMIN_DOWN)
    check(ok, "ช่องว่างต่างกันต้องยังผ่าน (Cisco จัดคอลัมน์ด้วยช่องว่าง)")

    ok, _ = verify_evidence("ETHERNET0/1 IS ADMINISTRATIVELY DOWN, LINE PROTOCOL IS DOWN", ADMIN_DOWN)
    check(ok, "ตัวพิมพ์ใหญ่เล็กต่างกันต้องยังผ่าน")

    ok, missing = verify_evidence("Interface was disabled by the operator", ADMIN_DOWN)
    check(not ok and missing, "บรรทัดที่แต่งขึ้นต้องไม่ผ่าน")

    ok, _ = verify_evidence("down", ADMIN_DOWN)
    check(not ok, f"เศษข้อความสั้นกว่า {MIN_EVIDENCE_CHARS} ตัวอักษรต้องไม่ผ่าน (กันการอ้างลอยๆ)")

    ok, _ = verify_evidence("", ADMIN_DOWN)
    check(not ok, "ไม่มีหลักฐานเลยต้องไม่ผ่าน")

    ok, _ = verify_evidence("Ethernet0/1 is administratively down", "")
    check(not ok, "raw_output ว่างต้องไม่ผ่าน (ไม่มีอะไรให้ตรวจ)")

    print("\n=== ด่านที่ 2: หลักฐานมีจริงแต่ไม่สนับสนุนข้อสรุป (บั๊กจาก live test 10 ส.ค.) ===\n")

    # เคสจริงที่เจอ: อ้างบรรทัดที่มีอยู่จริง แต่บรรทัดนั้นบอกว่า interface ปกติดี
    BRIEF = ("$ show ip interface brief\n"
             "Interface              IP-Address      OK? Method Status    Protocol\n"
             "Ethernet0/1            10.0.2.1        YES manual up        up\n")
    contradicting = ("CAUSE: The interface Ethernet0/1 is administratively down\n"
                     "EVIDENCE: Ethernet0/1            10.0.2.1        YES manual up        up")
    fake7 = FakeClient(contradicting, "should never be used")
    r13 = analyze(BRIEF, client=fake7)
    check(r13['cause_source'] == 'undetermined',
          "อ้างบรรทัดจริงแต่บรรทัดนั้นบอกว่าปกติดี ต้องถูกปฏิเสธ")
    check(r13['llm_note'] and UNSUPPORTED_NOTE in r13['llm_note'],
          "ต้องบันทึกว่าปฏิเสธเพราะหลักฐานไม่สนับสนุนข้อสรุป (คนละเหตุผลกับด่านที่ 1)")
    check(len(fake7.calls) == 1, "ถูกปฏิเสธที่ด่าน 2 แล้ว ต้องไม่เรียก prompt B ต่อ")

    # เคสจริงที่เจอ: ข้อสรุปไม่ได้พูดถึงปัญหาเลย เป็นแค่การเล่าสถานะซ้ำ
    restating = ("CAUSE: The interface Ethernet0/2 is up and operational\n"
                 "EVIDENCE: Ethernet0/1            10.0.2.1        YES manual up        up")
    r14 = analyze(BRIEF, client=FakeClient(restating))
    check(r14['cause_source'] == 'undetermined', "ข้อสรุปที่ไม่ใช่ปัญหา ต้องถูกปฏิเสธ")
    check(r14['llm_note'] and NO_FAULT_NOTE in r14['llm_note'],
          "ต้องแยกเหตุผลว่าข้อสรุปไม่ได้พูดถึงความผิดปกติเลย")

    print("\n=== evidence_supports_cause() โดยตรง ===\n")

    ok, _ = evidence_supports_cause(
        'Physical layer signal loss on the link',
        'Ethernet0/0 is down, line protocol is down',
    )
    check(ok, "ข้อสรุปบอกว่าเสีย + หลักฐานบอกว่า down ต้องผ่าน")

    ok, _ = evidence_supports_cause(
        'Cable fault indicated by error counters',
        '     5 input errors, 3 CRC, 0 frame, 0 overrun',
    )
    check(ok, "ตัวนับมากกว่า 0 ต้องนับเป็นสัญญาณผิดปกติ")

    ok, reason = evidence_supports_cause(
        'Cable fault indicated by error counters',
        '     0 input errors, 0 CRC, 0 frame, 0 overrun',
    )
    check(not ok and reason == UNSUPPORTED_NOTE,
          "ตัวนับเป็น 0 ทั้งหมดต้องไม่นับเป็นหลักฐาน (บทเรียนเดียวกับกฎ _rule_errors)")

    # regression: live test 10 ส.ค. 2026 — โมเดลตอบแบบนี้แล้วถูกปฏิเสธผิดๆ เพราะฝั่งข้อสรุป
    # ใช้รายการคำเดียวกับฝั่งหลักฐานที่ตัดคำว่า error ออกไป
    ok, reason = evidence_supports_cause(
        'The interface Ethernet0/2 has a high number of output errors',
        '1247 output errors, 1247 collisions, 0 interface resets',
    )
    check(ok, "ข้อสรุปที่ใช้คำว่า error/high ต้องนับว่าบรรยายความผิดปกติ (ฝั่งข้อสรุปกว้างกว่าฝั่งหลักฐาน)")

    ok, reason = evidence_supports_cause(
        'Excessive collisions indicate a duplex problem',
        '1247 output errors, 1247 collisions, 0 interface resets',
    )
    check(ok, "คำว่า excessive/problem ต้องนับเป็นการบรรยายความผิดปกติ")

    # regression: compare_approaches.py รอบแรก เคส C07 — โมเดลตอบถูกแต่ถูกปฏิเสธ
    ok, _ = evidence_supports_cause(
        'The interface is experiencing intermittent connectivity',
        '%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down',
    )
    check(ok, "คำว่า intermittent ต้องนับเป็นการบรรยายความผิดปกติ (เคส C07)")

    ok, reason = evidence_supports_cause(
        'Everything looks normal on this interface',
        'Ethernet0/0 is down, line protocol is down',
    )
    check(not ok and reason == NO_FAULT_NOTE, "ข้อสรุปที่ไม่ได้พูดถึงปัญหา ต้องถูกปฏิเสธก่อนถึงชั้นหลักฐาน")

    ok, _ = evidence_supports_cause('Port administratively shut down', '')
    check(not ok, "ไม่มีหลักฐานเลยต้องไม่ผ่าน")

    print("\n=== LLM ใช้ไม่ได้ / ปิด Ollama (Checkpoint 3.5.5 ข้อ 4) ===\n")

    dead = FakeClient(None, None)
    r6 = analyze(ADMIN_DOWN, client=dead)
    check(r6['cause_source'] == 'rule' and r6['cause_summary'] == 'Port administratively shut down',
          "โมเดลไม่ตอบ ผลของกฎต้องยังอยู่ครบ")
    check(r6['llm_explanation'] is None, "ไม่มีข้อความเรียบเรียงก็ไม่เป็นไร ไม่พัง")

    r7 = analyze(ADMIN_DOWN, client=None)
    check(r7['cause_source'] == 'rule', "client=None (ไม่มี LLM เลย) ต้องยังทำงานได้")

    r8 = analyze(ADMIN_DOWN, use_llm=False, client=FakeClient("ไม่ควรถูกเรียก"))
    check(r8['cause_source'] == 'rule' and r8['llm_explanation'] is None,
          "use_llm=False ต้องไม่เรียกโมเดลเลย (โหมดกฎล้วนสำหรับตารางเปรียบเทียบ)")

    dead2 = FakeClient(None)
    r9 = analyze(UNKNOWN, client=dead2)
    check(r9['llm_status'] == 'pending',
          "กฎสรุปไม่ได้ + เรียกโมเดลไม่ติด ต้องคง 'pending' ไว้ให้ลองใหม่ทีหลังได้")

    print("\n=== filter_noise() — กรอง log ที่ระบบสร้างเอง ===\n")

    noisy = ("Ethernet0/1 is administratively down\n"
             "%SSH-5-SSH2_SESSION: SSH2 Session request from 192.168.150.1\n"
             "%SEC_LOGIN-5-LOGIN_SUCCESS: Login Success [user: admin]\n"
             "%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down\n"
             "%SYS-5-CONFIG_I: Configured from console\n")
    filtered = filter_noise(noisy)
    check('%LINK-3-UPDOWN' in filtered, "เหตุการณ์จริงบนเครือข่ายต้องถูกเก็บไว้")
    check('%SSH-5' not in filtered and '%SEC_LOGIN' not in filtered,
          "log ที่เกิดจากการที่ระบบ SSH เข้าไปตรวจเองต้องถูกกรองทิ้ง")
    check('Ethernet0/1 is administratively down' in filtered,
          "บรรทัดผล show command ปกติต้องไม่ถูกกรองทิ้งไปด้วย")
    check(filter_noise('') == '' and filter_noise(None) == '', "ค่าว่าง/None ต้องไม่พัง")

    # ⚠️ ต้องกรองเฉพาะตอนส่งให้โมเดล — ห้ามกระทบ raw_output ที่เก็บลง DB (เกณฑ์ข้อ 4)
    # ใช้ UNKNOWN (กฎ match ไม่ได้) เพื่อบังคับให้เดินเส้นทางที่ 2 ซึ่งเป็นทางเดียวที่ใช้ filter_noise
    noisy_unknown = (UNKNOWN +
                     "%SSH-5-SSH2_SESSION: SSH2 Session request from 192.168.150.1\n"
                     "%SEC_LOGIN-5-LOGIN_SUCCESS: Login Success [user: admin]\n")
    fake6 = FakeClient("CAUSE: x\nEVIDENCE: y")
    r12 = analyze(noisy_unknown, client=fake6)
    check(len(fake6.calls) == 1 and 'Never use outside knowledge' in fake6.calls[0]['system'],
          "กฎสรุปไม่ได้ ต้องเดินเส้นทางที่ 2 (prompt A) จริง")
    check('%SSH-5' not in fake6.calls[0]['user'] and '%SEC_LOGIN' not in fake6.calls[0]['user'],
          "prompt ที่ส่งให้โมเดลต้องไม่มี noise")
    check('0 input errors' in fake6.calls[0]['user'], "ผล show command จริงต้องยังอยู่ครบใน prompt")
    check(r12['cause_source'] == 'undetermined',
          "หลักฐาน 'y' สั้นเกินและไม่มีจริง ต้องถูกปฏิเสธ")

    print("\n=== cause_output แยกจาก raw_output (บทเรียนบั๊ก #2 วันที่ 6 ส.ค.) ===\n")

    unreachable_preamble = ("[12:04:38] Access-Site-B (192.168.150.50:2202) - no response\n"
                            "Device unreachable, consistent with link failure on Core Ethernet0/1.\n"
                            "Falling back to Core for diagnosis.\n\n")
    r10 = analyze(unreachable_preamble + ADMIN_DOWN, cause_output=ADMIN_DOWN, client=FakeClient(None))
    check(r10['cause_summary'] == 'Port administratively shut down',
          "ต้องสรุปจาก cause_output (Core ที่สำเร็จ) ไม่ใช่ไปติดคำว่า unreachable ใน raw_output")

    r11 = analyze(unreachable_preamble + ADMIN_DOWN, client=FakeClient(None))
    check('unreachable' in (r11['cause_summary'] or '').lower(),
          "ถ้าไม่ส่ง cause_output มา จะตกไปใช้ raw_output ทั้งก้อนตามเดิม (พฤติกรรมที่คาดหวัง)")

    print("\n=== recover_cause_output() — กู้ cause_output จาก raw_output ที่เก็บใน DB ===\n")

    # ของจริงที่ diagnostics.py ประกอบไว้: ข้อความล้มเหลว + ข้อความคั่น + ผลจาก Core
    stored = (unreachable_preamble.replace('Falling back to Core for diagnosis.\n\n', '')
              + FALLBACK_MARK + '\n\n' + ADMIN_DOWN)
    recovered = recover_cause_output(stored)
    check('unreachable' not in recovered.lower(),
          "ต้องตัดข้อความ SSH ล้มเหลวออกหมด ไม่เหลือคำว่า unreachable")
    check('administratively down' in recovered, "ต้องเหลือผลจริงจาก Core ไว้ครบ")

    r12 = analyze(stored, cause_output=recover_cause_output(stored), client=FakeClient(None))
    check(r12['cause_summary'] == 'Port administratively shut down',
          "อ่านจาก DB แล้วกู้ cause_output ก่อนวิเคราะห์ ต้องได้สาเหตุจริง ไม่ใช่ 'unreachable'")

    plain = "$ show interface Ethernet0/1\nEthernet0/1 is administratively down, line protocol is down"
    check(recover_cause_output(plain) == plain,
          "ไม่มีข้อความคั่น (SSH สำเร็จตั้งแต่แรก) ต้องคืนทั้งก้อนตามเดิม")
    check(recover_cause_output('') == '' and recover_cause_output(None) == '',
          "ค่าว่าง/None ต้องไม่พัง")

    print(f"\nผลรวม: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case")
