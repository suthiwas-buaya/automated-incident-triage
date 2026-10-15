r"""
Phase 3.5.3 ขั้นที่ 4 - เก็บข้อมูลเปรียบเทียบ 3 แนวทางด้วยชุดเคสเดียวกัน

ป้อนชุดเคสเดียวกัน (testcases/cause_cases.py) เข้าทั้ง 3 แนวทาง แล้ววัด 5 ตัวชี้วัด
ตามตารางใน docs/Plan.md 3.5.3 ขั้นที่ 4 — ผลที่ได้คือหลักฐานหลักของบทที่ 5.2

3 แนวทางที่เทียบ:
  1. Rule only  — ชุดกฎล้วน ไม่เรียกโมเดลเลย (= สิ่งที่มีอยู่หลัง Phase 3)
  2. LLM only   — เรียกโมเดลตีความตรงๆ **ไม่มีกฎ และไม่มีด่านตรวจของเรา**
                  จงใจถอดด่านตรวจออกเพื่อวัดว่า "ถ้าเชื่อโมเดลตรงๆ จะเกิดอะไรขึ้น"
                  ซึ่งเป็นตัวเลขที่ใช้อธิบายว่าทำไมต้องมี Hybrid
  3. Hybrid     — ระบบจริงของเรา (กฎก่อน → LLM → ด่านตรวจ 2 ชั้น)

วิธีรัน (ต้องเปิด Ollama · ปิด CML ก่อน):
    py compare_approaches.py

ผลจะถูกบันทึกเป็น docs/comparison_results.csv ไว้ใส่ภาคผนวกของเล่ม
"""
import csv
import os
import time

# --- ย้ายมาอยู่ใต้โฟลเดอร์ย่อย: บอก Python ให้มองหา lib/ ที่โฟลเดอร์แม่ ---
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# ---------------------------------------------------------------------

from lib import analyzer, llm_client, llm_prompts
from testcases import cause_cases

OUTPUT_CSV = os.path.join('docs', 'comparison_results.csv')


def run_rule_only(case):
    """แนวทางที่ 1 — กฎล้วน ไม่แตะโมเดลเลย"""
    start = time.time()
    result = analyzer.analyze(case['raw_output'], node=case['node'],
                              interface=case['interface'], summary=case['summary'],
                              use_llm=False)
    return result, round(time.time() - start, 2)


def run_llm_only(case):
    """แนวทางที่ 2 — โมเดลล้วน ไม่มีกฎ ไม่มีด่านตรวจ

    ⚠️ จงใจ **ไม่เรียก** verify_evidence() และ evidence_supports_cause() —
    เพื่อวัดว่าถ้าเอาคำตอบของโมเดลไปใช้ตรงๆ จะได้อะไร นี่คือตัวเลขที่พิสูจน์ว่าด่านตรวจ
    ของ Hybrid มีค่าจริงหรือไม่ (ถ้าไม่มีความต่าง แปลว่าเราสร้างด่านตรวจมาเปล่าๆ)
    """
    start = time.time()
    system, user = llm_prompts.build_interpret_prompt(
        cause_output=analyzer.filter_noise(case['raw_output']),
        node=case['node'], interface=case['interface'], summary=case['summary'],
    )
    parsed = llm_prompts.parse_interpretation(llm_client.chat(system, user))
    elapsed = round(time.time() - start, 2)

    if not parsed:
        return {'cause_summary': None, 'evidence_lines': None, 'cause_source': 'undetermined'}, elapsed

    return {
        'cause_summary': parsed['cause_summary'],
        'evidence_lines': parsed['evidence_lines'],
        'cause_source': 'llm',
    }, elapsed


def run_hybrid(case):
    """แนวทางที่ 3 — ระบบจริงของเรา"""
    start = time.time()
    result = analyzer.analyze(case['raw_output'], node=case['node'],
                              interface=case['interface'], summary=case['summary'])
    return result, round(time.time() - start, 2)


APPROACHES = [
    ('Rule only', run_rule_only),
    ('LLM only', run_llm_only),
    ('Hybrid', run_hybrid),
]


def evaluate(case, result):
    """ตัดสินผลของเคสเดียว — คืน (สรุปได้ไหม, ถูกไหม, ตอบมั่วไหม, หลักฐานเชื่อได้ไหม)"""
    answered = bool(result.get('cause_summary'))

    if case['determinable']:
        # เคสที่มีคำตอบ: ถูก = ตอบและตรง keyword · ตอบผิดไม่นับเป็นการมั่ว แต่ไม่ได้คะแนน
        correct = answered and cause_cases.matches_expected(result['cause_summary'], case)
        hallucinated = answered and not correct
    else:
        # เคสที่ไม่ควรสรุปได้: ถูก = ไม่ตอบ · ตอบอะไรมาก็ตาม = ตอบมั่ว
        correct = not answered
        hallucinated = answered

    # หลักฐานที่อ้างมา ตรวจสอบย้อนกลับได้จริงไหม (ใช้เกณฑ์เดียวกันทุกแนวทาง)
    evidence_ok = None
    if answered:
        found, _ = analyzer.verify_evidence(result.get('evidence_lines'), case['raw_output'])
        supported, _ = analyzer.evidence_supports_cause(
            result.get('cause_summary'), result.get('evidence_lines'))
        evidence_ok = bool(found and supported)

    return answered, correct, hallucinated, evidence_ok


def main():
    print("=" * 78)
    print("  เปรียบเทียบ 3 แนวทางด้วยชุดเคสเดียวกัน (Plan.md 3.5.3 ขั้นที่ 4)")
    print("=" * 78)

    if not llm_client.is_available():
        print("\n❌ Ollama ไม่ได้เปิดอยู่ — เปิดก่อนแล้วรันใหม่ (ถ้าเปิด CML อยู่ให้ปิดก่อน)")
        return

    n = len(cause_cases.CASES)
    determinable = sum(1 for c in cause_cases.CASES if c['determinable'])
    print(f"\nชุดเคส: {n} เคส (สรุปได้ {determinable} · สรุปไม่ได้ {n - determinable})")
    print(f"โมเดล : {llm_client.MODEL} | temperature={llm_client.TEMPERATURE} | seed={llm_client.SEED}\n")

    rows = []
    stats = {name: {'answered': 0, 'correct': 0, 'hallucinated': 0,
                    'evidence_ok': 0, 'time': 0.0} for name, _ in APPROACHES}

    for case in cause_cases.CASES:
        expect = 'สรุปได้' if case['determinable'] else 'ต้องตอบว่าสรุปไม่ได้'
        print(f"[{case['id']}] {case['note']}")
        print(f"      คาดหวัง: {expect}")

        for name, runner in APPROACHES:
            result, elapsed = runner(case)
            answered, correct, hallucinated, evidence_ok = evaluate(case, result)

            s = stats[name]
            s['answered'] += answered
            s['correct'] += correct
            s['hallucinated'] += hallucinated
            s['evidence_ok'] += (evidence_ok is True)
            s['time'] += elapsed

            mark = '✓' if correct else ('✗ มั่ว' if hallucinated else '– ไม่สรุป')
            summary_text = (result.get('cause_summary') or '(สรุปไม่ได้)')[:58]
            print(f"      {name:<10} {mark:<9} {elapsed:>5.1f}s  {summary_text}")

            rows.append({
                'case_id': case['id'],
                'approach': name,
                'determinable': case['determinable'],
                'answered': answered,
                'correct': correct,
                'hallucinated': hallucinated,
                'evidence_verifiable': evidence_ok,
                'seconds': elapsed,
                'cause_summary': result.get('cause_summary') or '',
            })
        print()

    # ---------------- ตารางสรุป ----------------
    print("=" * 78)
    print("  ตารางเปรียบเทียบ (ใส่บทที่ 5.2 ได้เลย)")
    print("=" * 78)
    print(f"{'ตัวชี้วัด':<34}{'Rule only':>14}{'LLM only':>14}{'Hybrid':>14}")
    print("-" * 78)

    def row(label, fn, suffix=''):
        cells = ''.join(f"{fn(stats[name]):>14}" for name, _ in APPROACHES)
        print(f"{label:<34}{cells}{suffix}")

    row('เวลาเฉลี่ยต่อเคส (วินาที)', lambda s: f"{s['time'] / n:.2f}")
    row('จำนวนเคสที่สรุปสาเหตุได้', lambda s: f"{s['answered']}/{n}")
    row('ตอบถูกตามที่คาดหวัง', lambda s: f"{s['correct']}/{n}")
    row('ตอบมั่ว (ไม่ควรตอบแต่ตอบ)', lambda s: f"{s['hallucinated']}")
    row('หลักฐานตรวจสอบย้อนกลับได้', lambda s: f"{s['evidence_ok']}/{s['answered'] or 0}")
    print("-" * 78)
    print(f"{'อธิบายที่มาของผลได้ไหม':<34}{'ได้ 100%':>14}{'อ้างได้ แต่':>14}{'ได้ 100%':>14}")
    print(f"{'':<34}{'':>14}{'ตรวจไม่ผ่านบ้าง':>14}{'':>14}")
    print(f"{'ผลเหมือนเดิมเมื่อรันซ้ำ':<34}{'ทุกครั้ง':>14}{'ไม่การันตี':>14}{'เฉพาะทางกฎ':>14}")

    accuracy = {name: stats[name]['correct'] / n * 100 for name, _ in APPROACHES}
    print("\n" + "=" * 78)
    print("  สรุปเป็นประโยค")
    print("=" * 78)
    for name, _ in APPROACHES:
        s = stats[name]
        print(f"  {name:<10} ถูก {accuracy[name]:.0f}% · ตอบมั่ว {s['hallucinated']} เคส · "
              f"เฉลี่ย {s['time'] / n:.2f} วิ/เคส")

    os.makedirs('docs', exist_ok=True)
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n📄 บันทึกผลดิบทุกเคสไว้ที่ {OUTPUT_CSV} (เปิดด้วย Excel ได้ ใส่ภาคผนวก)")


if __name__ == '__main__':
    main()
