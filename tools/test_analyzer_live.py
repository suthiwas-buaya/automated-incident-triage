"""
Phase 3.5.3 ขั้นที่ 3 - ทดสอบกลไกกันตอบมั่วกับโมเดลจริง (ไม่ใช่ client จำลอง)

ต่างจาก self-test ใน lib\analyzer.py ตรงที่ไฟล์นั้นทดสอบว่า "โค้ดของเราถูก" ด้วยคำตอบ
ที่เราแต่งเอง — ส่วนไฟล์นี้ทดสอบว่า "โมเดลจริงทำตามที่สั่งไหม" ซึ่งเป็นคนละคำถามกัน
และเป็นความเสี่ยงที่เหลืออยู่จริงๆ (โมเดล 8B อาจไม่ทำตามรูปแบบที่สั่งก็ได้)

ตอบ Checkpoint 3.5.5 ข้อ 3 โดยตรง: ป้อนข้อมูลที่ไม่พอสรุป ต้องไม่ได้คำตอบมั่ว

วิธีรัน (ปิด CML ก่อน):
    py test_analyzer_live.py
"""
import time

# --- ย้ายมาอยู่ใต้โฟลเดอร์ย่อย: บอก Python ให้มองหา lib/ ที่โฟลเดอร์แม่ ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# ---------------------------------------------------------------------

from lib import analyzer, llm_client, llm_prompts

# เคสที่ 1 — กฎสรุปได้อยู่แล้ว (admin down) LLM ควรแตะแค่ถ้อยคำ ห้ามเปลี่ยนสาเหตุ
CASE_RULE = """$ show interface Ethernet0/1
Ethernet0/1 is administratively down, line protocol is down
  Hardware is AmdP2, address is aabb.cc00.0100 (bia aabb.cc00.0100)
  MTU 1500 bytes, BW 10000 Kbit/sec, DLY 1000 usec
     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored
"""

# เคสที่ 2 — กฎสรุปไม่ได้ แต่มีเบาะแสจริงใน output ให้ LLM ตีความ
CASE_LLM = """$ show interface Ethernet0/2
Ethernet0/2 is up, line protocol is up
  Hardware is AmdP2, address is aabb.cc00.0200
  Full-duplex, 100Mb/s, media type is unknown
     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored
     1247 output errors, 1247 collisions, 0 interface resets
"""

# เคสที่ 3 — ข้อมูลไม่พอสรุปอะไรได้เลย ต้องได้ "สรุปไม่ได้" ไม่ใช่คำตอบมั่ว
# (นี่คือเคสที่ Checkpoint 3.5.5 ข้อ 3 บังคับให้ทดสอบ)
CASE_INSUFFICIENT = """$ show ip interface brief
Interface              IP-Address      OK? Method Status    Protocol
Ethernet0/0            10.0.1.1        YES manual up        up
Ethernet0/1            10.0.2.1        YES manual up        up
"""


def _build_realistic_case():
    """เคสที่ 4 — ผลเต็มขนาดจริงจาก 3 คำสั่ง รวม show logging ที่ยาวและมี noise ปน

    ทำไมต้องมี: เคส 1-3 ใช้ข้อมูลสั้นๆ ไม่กี่บรรทัด ซึ่งไม่ได้ทดสอบสิ่งที่เสี่ยงที่สุดเลย
    คือ **พฤติกรรมตอนข้อมูลยาว** — Plan 3.9 บันทึกว่า show logging จริงมี 58 บรรทัดต่อครั้ง
    และค่าเริ่มต้น num_ctx ของ Ollama ตัด prompt ทิ้งเงียบๆ ถ้าเราตั้ง num_ctx ไม่พอ
    หรือ shrink_to_fit ทำงานผิด จะเห็นอาการที่เคสนี้เท่านั้น
    """
    header = ("$ show interface Ethernet0/1\n"
              "Ethernet0/1 is up, line protocol is up\n"
              "  Hardware is AmdP2, address is aabb.cc00.0100 (bia aabb.cc00.0100)\n"
              "  MTU 1500 bytes, BW 10000 Kbit/sec, DLY 1000 usec,\n"
              "     reliability 255/255, txload 1/255, rxload 1/255\n"
              "  Encapsulation ARPA, loopback not set\n"
              "  Keepalive set (10 sec)\n"
              "  ARP type: ARPA, ARP Timeout 04:00:00\n"
              "     892 input errors, 892 CRC, 0 frame, 0 overrun, 0 ignored\n"
              "     0 output errors, 0 collisions, 2 interface resets\n"
              "\n$ show ip interface brief\n"
              "Interface              IP-Address      OK? Method Status    Protocol\n"
              "Ethernet0/0            10.0.1.1        YES manual up        up\n"
              "Ethernet0/1            10.0.2.1        YES manual up        up\n"
              "Ethernet0/2            10.0.3.1        YES manual up        up\n"
              "\n$ show logging\n")

    # จำลอง log ยาวๆ แบบของจริง: ส่วนใหญ่เป็น noise จากการที่ระบบ SSH เข้าไปตรวจเอง
    noise = []
    for i in range(30):
        noise.append(f"*Aug 10 14:{i:02d}:11.220: %SSH-5-SSH2_SESSION: SSH2 Session request from 192.168.150.1 (tty = 0) - succeeded")
        noise.append(f"*Aug 10 14:{i:02d}:12.100: %SEC_LOGIN-5-LOGIN_SUCCESS: Login Success [user: admin] [Source: 192.168.150.1]")
        noise.append(f"*Aug 10 14:{i:02d}:18.443: %SYS-6-LOGOUT: User admin has exited tty session 0(192.168.150.1)")

    real_events = [
        "*Aug 10 14:31:02.117: %LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down",
        "*Aug 10 14:31:03.117: %LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet0/1, changed state to down",
    ]
    return header + "\n".join(noise + real_events) + "\n"


CASE_REALISTIC = _build_realistic_case()


def show(title, raw_output, expect, interface='Ethernet0/1'):
    """⚠️ interface ต้องตรงกับ interface ที่ปรากฏใน raw_output ของเคสนั้นเสมอ

    บทเรียนจริง 10 ส.ค. 2026: รอบแรกผมตั้ง interface='Ethernet0/1' ให้ทุกเคส ทั้งที่
    เคส 2 เป็นผลของ Ethernet0/2 — โมเดลถูกถามถึงพอร์ตหนึ่งแต่ได้ข้อมูลของอีกพอร์ต จึงตอบ
    ว่าหาหลักฐานไม่ได้ ซึ่งถูกต้องแล้ว ความผิดอยู่ที่ข้อมูลทดสอบ ไม่ใช่ที่โมเดล
    """
    print("=" * 70)
    print(f"  {title}")
    print(f"  คาดหวัง: {expect}")
    print("=" * 70)

    summary = f'%LINK-3-UPDOWN: Interface {interface}, changed state to down'
    start = time.time()
    result = analyzer.analyze(raw_output, node='Core', interface=interface, summary=summary)
    elapsed = round(time.time() - start, 2)

    print(f"cause_source    : {result['cause_source']}")
    print(f"llm_status      : {result['llm_status']}")
    print(f"cause_summary   : {result['cause_summary']}")
    print(f"evidence_lines  : {(result['evidence_lines'] or '').strip()[:200]}")
    print(f"llm_explanation : {result['llm_explanation']}")
    if result['llm_note']:
        print(f"llm_note        : {result['llm_note']}")
    print(f"เวลาที่ใช้        : {elapsed} วินาที")

    # ตรวจข้อบังคับที่ต้องจริงเสมอไม่ว่าโมเดลจะตอบอะไรมา
    problems = []
    if result['cause_source'] != 'undetermined':
        ok, missing = analyzer.verify_evidence(result['evidence_lines'], raw_output)
        if not ok:
            problems.append(f"หลักฐานตรวจไม่ผ่านแต่ยังถูกยอมรับ: {missing}")
    if 'owner_group' in result or 'priority' in result:
        problems.append("มี owner_group/priority หลุดออกมา")

    print("⚠ " + " | ".join(problems) if problems else "✓ ผ่านข้อบังคับทุกข้อ")

    # ถ้าสรุปไม่ได้และไม่มี llm_note แปลว่าโมเดลตอบมาแล้วแต่แกะไม่ได้ (หรือตอบ INSUFFICIENT)
    # ต้องเห็นคำตอบดิบถึงจะรู้ว่าเป็นอย่างไหน — ไม่งั้นได้แต่เดา
    if result['cause_source'] == 'undetermined' and not result['llm_note']:
        print("\n  ↓ ไม่มี llm_note = โมเดลตอบแล้วแต่ระบบใช้ไม่ได้ ขอดูคำตอบดิบ:")
        system, user = llm_prompts.build_interpret_prompt(
            cause_output=analyzer.filter_noise(raw_output),
            node='Core', interface=interface, summary=summary,
        )
        raw_reply = llm_client.chat(system, user)
        print(f"  ---- คำตอบดิบจากโมเดล ----\n{raw_reply}\n  --------------------------")

    print()
    return result


def main():
    print()
    if not llm_client.is_available():
        print("❌ Ollama ไม่ได้เปิดอยู่ — เปิดก่อนแล้วรันใหม่")
        print("   (ถ้ากำลังเปิด CML อยู่ ให้ปิด CML ก่อน — RAM ไม่พอรันพร้อมกัน)")
        return

    print(f"โมเดล: {llm_client.MODEL} | num_ctx={llm_client.NUM_CTX} | temperature={llm_client.TEMPERATURE}")
    print("รอบแรกอาจนานถ้าโมเดลยังไม่อยู่ใน RAM (cold start ~45 วินาที)\n")

    r1 = show("เคส 1: กฎสรุปได้ (admin down)",
              CASE_RULE, "cause_source='rule' และสาเหตุต้องไม่ถูกโมเดลเปลี่ยน")

    r2 = show("เคส 2: กฎสรุปไม่ได้ มีเบาะแสให้ตีความ (collisions)",
              CASE_LLM, "cause_source='llm' พร้อมหลักฐานที่มีอยู่จริง หรือ 'undetermined' ถ้าโมเดลไม่มั่นใจ",
              interface='Ethernet0/2')

    r3 = show("เคส 3: ข้อมูลไม่พอสรุป (Checkpoint 3.5.5 ข้อ 3)",
              CASE_INSUFFICIENT, "ต้องเป็น 'undetermined' — ห้ามเดาสาเหตุขึ้นมาเอง")

    print("=" * 70)
    print(f"  เคส 4: ผลเต็มขนาดจริง 3 คำสั่ง ({len(CASE_REALISTIC)} ตัวอักษร, "
          f"{len(CASE_REALISTIC.splitlines())} บรรทัด)")
    print("  คาดหวัง: ต้องเห็น 892 CRC errors ไม่ใช่หลงไปกับ log SSH 90 บรรทัด")
    print("=" * 70)
    filtered = analyzer.filter_noise(CASE_REALISTIC)
    print(f"หลังกรอง noise    : {len(filtered)} ตัวอักษร "
          f"({len(filtered.splitlines())} บรรทัด) — ลดลง "
          f"{round((1 - len(filtered) / len(CASE_REALISTIC)) * 100)}%")
    print(f"เพดาน prompt      : {llm_client.MAX_PROMPT_CHARS} ตัวอักษร")
    print(f"ถูกตัดกลางไหม     : "
          f"{'ใช่ — ทดสอบ shrink_to_fit ด้วย' if len(filtered) > llm_client.MAX_PROMPT_CHARS else 'ไม่ (ยังไม่ชนเพดาน)'}")
    print()
    r4 = show("เคส 4 (ต่อ): วิเคราะห์ผลเต็มขนาด", CASE_REALISTIC,
              "cause_source='llm' อ้าง 892 CRC หรือ 'undetermined' — ห้ามอ้าง log SSH",
              interface='Ethernet0/1')

    print("=" * 70)
    print("  เคส 5: รันซ้ำข้อมูลเดิม (seed=0 + temperature=0)")
    print("=" * 70)
    print("  วัด 2 ระดับ เพราะ 'ตรงกันทุกตัวอักษร' กับ 'ให้ข้อสรุปเดียวกัน' ไม่เท่ากัน")
    print("  — LLM ไม่การันตีผลเท่ากันทุกครั้งแม้ตรึง seed แล้ว (ธรรมชาติของการคำนวณ)")
    print()
    again = analyzer.analyze(CASE_LLM, node='Core', interface='Ethernet0/2',
                             summary='%LINK-3-UPDOWN: Interface Ethernet0/2, changed state to down')

    def norm(text):
        """ยุบช่องว่างและตัดเครื่องหมายท้ายบรรทัด — เทียบว่า 'สาระเดียวกันไหม'"""
        import re as _re
        return _re.sub(r'[\s,.;:]+', ' ', (text or '')).strip().lower()

    rows = [
        ('cause_source  ', r2['cause_source'], again['cause_source']),
        ('llm_status    ', r2['llm_status'], again['llm_status']),
        ('cause_summary ', r2['cause_summary'], again['cause_summary']),
        ('evidence_lines', r2['evidence_lines'], again['evidence_lines']),
        ('llm_explanation', r2['llm_explanation'], again['llm_explanation']),
    ]
    exact_count = 0
    same_count = 0
    for label, before, after in rows:
        exact = (before or '') == (after or '')
        same = norm(before) == norm(after)
        exact_count += exact
        same_count += same
        mark = '✓' if exact else ('≈' if same else '❌')
        print(f"{mark} {label} : {'ตรงทุกตัวอักษร' if exact else ('สาระเดียวกัน แต่ตัวอักษรต่าง' if same else 'ต่างกันจริง')}")
        if not exact:
            print(f"    รอบก่อน : {(before or '')[:120]!r}")
            print(f"    รอบนี้  : {(after or '')[:120]!r}")

    print(f"\nสรุป: ตรงทุกตัวอักษร {exact_count}/{len(rows)} ฟิลด์ · "
          f"ให้สาระเดียวกัน {same_count}/{len(rows)} ฟิลด์")
    print("→ ตัวเลขคู่นี้คือคำตอบของช่อง 'ความสม่ำเสมอเมื่อรันซ้ำ' ในตารางเปรียบเทียบ 3.5.3 ขั้นที่ 4")
    print()

    print("=" * 70)
    print("  สรุปผล")
    print("=" * 70)

    verdicts = [
        ("เคส 1 กฎยังเป็นเจ้าของสาเหตุ",
         r1['cause_source'] == 'rule' and r1['cause_summary'] == 'Port administratively shut down'),
        ("เคส 1 ได้ข้อความเรียบเรียงจากโมเดล", bool(r1['llm_explanation'])),
        ("เคส 3 ไม่มีสาเหตุมั่วหลุดออกมา", r3['cause_source'] == 'undetermined'),
    ]
    for note, ok in verdicts:
        print(f"{'✓' if ok else '❌'} {note}")

    print(f"\nเคส 2 ได้ผลเป็น: {r2['cause_source']} "
          f"({'โมเดลตีความได้และหลักฐานจริง' if r2['cause_source'] == 'llm' else 'โมเดลไม่ผ่านการตรวจ — ปลอดภัยไว้ก่อน'})")
    print(f"เคส 4 ได้ผลเป็น: {r4['cause_source']} "
          f"({'กฎจับได้เอง ไม่ต้องเรียก LLM ตีความ' if r4['cause_source'] == 'rule' else 'ต้องพึ่ง LLM'})")

    print("\n📌 ที่ยังทดสอบไม่ได้จากสคริปต์นี้ — ต้องทำมือ:")
    print("   ปิด Ollama จริง (คลิกขวาไอคอนมุมขวาล่าง > Quit) แล้วรันไฟล์นี้ซ้ำ")
    print("   ต้องขึ้น 'Ollama ไม่ได้เปิดอยู่' แล้วจบสวยๆ + py lib\\analyzer.py ต้องยังผ่านครบ")
    print("   → ข้อนี้คือ Checkpoint 3.5.5 ข้อ 4")


if __name__ == '__main__':
    main()
