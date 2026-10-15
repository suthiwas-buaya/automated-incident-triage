r"""
Phase 3 - ชุดกฎสรุปสาเหตุจากผล show command (แยกจาก logic เหมือน mapping.py กับ classifier.py)

หน้าที่: รับ raw_output (string ดิบจาก SSH หรือข้อความ "เข้าอุปกรณ์ไม่ได้") -> สรุป
cause_summary + evidence_lines + cause_source โดยไม่แตะเครือข่ายจริงเลย
ทดสอบได้เองไม่ต้องมี lab: py lib\cause_rules.py
"""
import re

# ---------------------------------------------------------------------------
# ชุดกฎ — เรียงจาก "เจาะจงที่สุด" ไปกว้างที่สุด ตัวแรกที่ match ชนะ (เหมือน mapping.py)
# แต่ละกฎรับ raw_output (string) -> คืน cause_summary (string) ถ้า match หรือ None ถ้าไม่
# ---------------------------------------------------------------------------

# ตัวนับ error ที่ถือว่า "มีปัญหา" ถ้ามากกว่า 0 เช่น "5 input errors, 3 CRC"
_ERROR_COUNTER_RE = re.compile(r'(\d+)\s+(?:input errors|CRC|runts|giants|frame)', re.IGNORECASE)


def _has_word(text, word):
    """match แบบ 'ทั้งคำ' (word boundary) ห้ามใช้ `word in text` เด็ดขาด

    ⚠️ เหตุผล (สืบเนื่องจากบั๊กจริงใน Phase 2): keyword สั้นๆ อย่าง `los` ไปตรงกับ
    คำว่า "lost"/"close" ได้ถ้าใช้ substring match เฉยๆ — ต้องกันด้วย \b เสมอ
    """
    return re.search(r'\b' + re.escape(word) + r'\b', text, re.IGNORECASE) is not None


def _matching_lines(raw_output, predicate):
    """คืนบรรทัดจริงจาก raw_output ที่ predicate(line) เป็น True — ใช้ทำ evidence_lines
    ห้ามแต่งข้อความเอง ต้องอ้างของจริงเท่านั้น
    """
    return [line for line in raw_output.splitlines() if predicate(line.strip())]


def _rule_admin_down(raw_output):
    if not _has_word(raw_output, 'administratively'):
        return None
    evidence = _matching_lines(raw_output, lambda l: 'administratively' in l.lower())
    return {
        'cause_summary': 'Port administratively shut down',
        'evidence_lines': '\n'.join(evidence),
    }


def _rule_physical(raw_output):
    lines = _matching_lines(
        raw_output,
        lambda l: 'line protocol is down' in l.lower() and 'administratively' not in l.lower(),
    )
    if not lines:
        return None
    return {
        'cause_summary': 'Physical-layer signal loss (line protocol down, not administratively shut)',
        'evidence_lines': '\n'.join(lines),
    }


def _rule_errors(raw_output):
    lines = []
    for line in raw_output.splitlines():
        m = _ERROR_COUNTER_RE.search(line)
        if m and int(m.group(1)) > 0:
            lines.append(line.strip())
    if not lines:
        return None
    return {
        'cause_summary': 'Cable or transceiver fault (CRC/input error counters above 0)',
        'evidence_lines': '\n'.join(lines),
    }


def _rule_unreachable(raw_output):
    if not _has_word(raw_output, 'unreachable'):
        return None
    evidence = _matching_lines(raw_output, lambda l: 'unreachable' in l.lower() or 'failed' in l.lower())
    return {
        'cause_summary': 'Device unreachable (SSH failed), consistent with a link failure',
        'evidence_lines': '\n'.join(evidence) if evidence else raw_output.strip(),
    }


# เรียงลำดับสำคัญ: unreachable มาก่อนเพราะถ้า SSH เข้าไม่ได้เลย output จะเป็นข้อความ
# อธิบาย ไม่ใช่ output จริงจาก router จึงต้องกันไม่ให้กฎอื่นเผลอ match คำในข้อความ error นั้น
RULES = [
    ('unreachable', _rule_unreachable),
    ('admin-down', _rule_admin_down),
    ('physical', _rule_physical),
    ('errors', _rule_errors),
]


def summarize(raw_output):
    """
    สรุปสาเหตุจาก raw_output

    คืน dict: {'cause_summary', 'cause_source', 'evidence_lines', 'llm_status'}
    - เข้ากฎ -> cause_source='rule', llm_status='done'
    - ไม่เข้ากฎใดเลย -> cause_source='undetermined', llm_status='pending' (รอ Phase 3.5 วิเคราะห์ต่อ)

    raw_output ต้องไม่ว่าง (ผู้เรียกเป็นคนรับประกัน — ข้อห้าม #4 ของ Phase 3) ฟังก์ชันนี้
    แค่ประมวลผล ไม่ตัดสินว่า raw_output ว่างได้หรือไม่
    """
    for name, rule in RULES:
        result = rule(raw_output)
        if result:
            return {
                'cause_summary': result['cause_summary'],
                'cause_source': 'rule',
                'evidence_lines': result['evidence_lines'],
                'llm_status': 'done',
            }

    return {
        'cause_summary': None,
        'cause_source': 'undetermined',
        'evidence_lines': None,
        'llm_status': 'pending',
    }


# ---------------------------------------------------------------------------
# ทดสอบด้วยผล show command จำลอง (ไม่ต้องมี lab) -- รัน: py lib\cause_rules.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    CASES = [
        (
            "admin-down: interface ที่ถูกสั่งปิดจากการตั้งค่า",
            "Ethernet0/1 is administratively down, line protocol is down\n"
            "  Hardware is AmdP2, address is aabb.cc00.0100\n",
            'rule', 'admin-down มาก่อน physical แม้จะมี line protocol is down ด้วย',
        ),
        (
            "physical: line protocol down ธรรมดา ไม่มี administratively",
            "Ethernet0/0 is down, line protocol is down (notconnect)\n"
            "  Hardware is AmdP2, address is aabb.cc00.0000\n",
            'rule', 'ไม่มีคำว่า administratively ต้องตกไปกฎ physical',
        ),
        (
            "errors: CRC error มากกว่า 0",
            "Ethernet0/2 is up, line protocol is up\n"
            "     5 input errors, 3 CRC, 0 frame, 0 overrun\n",
            'rule', 'ตัวนับ error > 0 ต้องเข้ากฎ errors',
        ),
        (
            "errors: ตัวนับเป็น 0 ทั้งหมด -> ไม่ควร match กฎ errors",
            "Ethernet0/2 is up, line protocol is up\n"
            "     0 input errors, 0 CRC, 0 frame, 0 overrun\n",
            'undetermined', 'ตัวนับเป็น 0 ต้องไม่เข้ากฎ errors (และไม่เข้ากฎอื่นด้วย)',
        ),
        (
            "unreachable: SSH ล้มเหลว",
            "[12:04:38] Access-Site-B (192.168.150.50:2202) - no response\n"
            "Device unreachable, consistent with link failure on Core Ethernet0/1.\n",
            'rule', 'ข้อความ unreachable ต้องสรุปเป็นกฎ unreachable',
        ),
        (
            "ไม่เข้ากฎใดเลย -> undetermined",
            "Ethernet0/0 is up, line protocol is up\n"
            "     0 input errors, 0 CRC\n"
            "IP routing table entries look normal\n",
            'undetermined', 'output ปกติไม่มี keyword ใดเข้ากฎ ต้องเป็น undetermined',
        ),
        (
            "word boundary: 'lost' ต้องไม่ทำให้เข้าใจผิดว่า down",
            "BGP neighbor 10.0.1.2 adjacency lost, reason: hold timer expired\n",
            'undetermined', 'คำว่า lost ไม่ควรทำให้ match กฎใดใน cause_rules (กันบั๊กแบบ los/lost ของ Phase 2)',
        ),
    ]

    print("=== ทดสอบ summarize() ===\n")
    passed = 0
    for note, raw, expect_source, why in CASES:
        result = summarize(raw)
        ok = result['cause_source'] == expect_source
        passed += ok
        mark = '✓' if ok else '❌'
        print(f"{mark} {note}")
        print(f"   คาด cause_source={expect_source}  ได้={result['cause_source']}  ({why})")
        if result['cause_source'] == 'rule':
            print(f"   cause_summary : {result['cause_summary']}")
            print(f"   evidence_lines: {result['evidence_lines']!r}")
        print()

    # ทุกกฎที่ match ต้องมี evidence_lines ไม่ว่าง (ห้ามแต่งข้อความเองโดยไม่มีบรรทัดอ้างอิง)
    for note, raw, expect_source, why in CASES:
        if expect_source == 'rule':
            result = summarize(raw)
            assert result['evidence_lines'], f"กฎที่ match ต้องมี evidence_lines เสมอ: {note}"
    print("✓ ทุกกฎที่ match มี evidence_lines ไม่ว่าง (ไม่มีการแต่งเหตุผลลอยๆ)")
    passed += 1

    total = len(CASES) + 1
    print(f"\nผลรวม: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case")
