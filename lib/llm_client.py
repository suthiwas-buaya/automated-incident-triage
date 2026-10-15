r"""
Phase 3.5 - ตัวเชื่อมกับ Ollama (docs/Plan.md 3.5.3 ขั้นที่ 3)

หน้าที่เดียว: ส่ง (system, user) ไปให้โมเดลแล้วคืนข้อความกลับมา — ไม่รู้จักเรื่อง ticket,
ไม่รู้จักเรื่องกฎ, ไม่ตัดสินใจอะไรทั้งสิ้น (การตัดสินใจอยู่ที่ analyzer.py)

หลักการที่ยึด: **LLM ใช้ไม่ได้ ต้องไม่ทำให้ระบบพัง** ทุกฟังก์ชันในไฟล์นี้คืน None เมื่อ
ล้มเหลว ไม่ raise ขึ้นไป เพราะ Phase 3 (ชุดกฎ) ต้องเดินต่อได้เองเสมอแม้ปิด Ollama ทิ้ง
(Checkpoint 3.5.5 ข้อ 4)

ใช้ urllib จาก standard library ไม่ใช่ requests — ลดของที่ต้องติดตั้งเพิ่ม และให้ไฟล์นี้
ทดสอบได้แม้เครื่องที่ยังไม่ได้ pip install อะไรเลย (แนวเดียวกับ check_dashboard.py)

ทดสอบ: py lib\llm_client.py
  - ก้อนที่ 1 ทดสอบ logic ล้วน ไม่ต้องเปิด Ollama
  - ก้อนที่ 2 ทดสอบยิงจริง จะข้ามอัตโนมัติถ้าไม่ได้เปิด Ollama ไว้
"""
import json
import time
import urllib.error
import urllib.request

OLLAMA_URL = 'http://localhost:11434'
MODEL = 'llama3.1:8b'

# ---------------------------------------------------------------------------
# ค่าที่ตั้งเอง ห้ามใช้ค่าเริ่มต้นของ Ollama — เหตุผลของแต่ละตัวอยู่ในคอมเมนต์
# ---------------------------------------------------------------------------

# ⚠️ ค่าเริ่มต้นของ Ollama คือ 4096 ไม่ใช่ 128k ตามสเปกโมเดล และเมื่อ prompt ยาวเกิน
# มันจะ "ตัดทิ้งเงียบๆ" ไม่แจ้ง error เลย → โมเดลเห็นข้อมูลไม่ครบแล้วสรุปผิดโดยเราไม่รู้ตัว
# ผล 3 show command (โดยเฉพาะ show logging ที่มี 58 บรรทัด/ครั้ง ตาม Plan 3.9) ทะลุ 4096 ได้ง่าย
NUM_CTX = 8192

# 0 = ตอบเหมือนเดิมทุกครั้งเท่าที่เป็นไปได้ — จำเป็นเพราะตัวชี้วัด "ความสม่ำเสมอเมื่อรันซ้ำ"
# ในตารางเปรียบเทียบ 3 แนวทาง (3.5.3 ขั้นที่ 4) และเพราะงาน NOC ต้องตรวจสอบย้อนกลับได้
TEMPERATURE = 0

# ให้ Ollama อุ้มโมเดลไว้ใน RAM นานขึ้น (ค่าเริ่มต้น 5 นาที) — ไม่งั้น TT ที่เข้ามาห่างกัน
# จะเจอ cold start 45 วินาทีทุกใบ (วัดจริงไว้ใน Plan 3.5.3 ขั้นที่ 1)
KEEP_ALIVE = '30m'

# ตรึงเลขสุ่มเริ่มต้น — temperature=0 อย่างเดียว **ไม่การันตีว่าตอบเหมือนเดิมทุกครั้ง**
# (ทดสอบจริง 10 ส.ค. 2026: ข้อความเรียบเรียงจาก prompt B เปลี่ยนถ้อยคำระหว่างสองรอบ
# ทั้งที่ตั้ง temperature=0 แล้ว) ใส่ seed คู่กันเพื่อลดความต่างให้เหลือน้อยที่สุด
SEED = 0

# timeout ต้องเผื่อ cold start (วัดได้ 45.49 วิ) ไม่ใช่ตั้งตามเวลา warm (4.64 วิ)
# ถ้าตั้งสั้นเกินจะพังทุกครั้งที่โมเดลเพิ่งถูกปล่อยออกจาก RAM
TIMEOUT_SECONDS = 180

# เพดานความยาว prompt กันชนเพดาน context — ประมาณหยาบๆ ว่า 1 token ≈ 3.5 ตัวอักษร
# สำหรับ log อุปกรณ์ (ตัวอักษรล้วน ไม่ใช่ภาษาคน) เผื่อที่ให้คำตอบด้วยจึงหารครึ่ง
MAX_PROMPT_CHARS = int(NUM_CTX * 3.5 / 2)

TRUNCATION_MARK = '\n...[truncated to fit model context]...\n'


def _post(path, payload, timeout=TIMEOUT_SECONDS):
    """ยิง POST ไป Ollama — คืน dict ที่ decode แล้ว หรือ None ถ้าล้มเหลวทุกกรณี

    จงใจ catch กว้าง: ปิด Ollama, ยังโหลดโมเดลไม่เสร็จ, JSON เพี้ยน, timeout —
    ทุกกรณีมีความหมายเดียวกันสำหรับผู้เรียกคือ "ใช้ LLM ไม่ได้ ให้ถอยไปใช้กฎ"
    """
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        f"{OLLAMA_URL}{path}", data=data,
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            TimeoutError, OSError, ValueError):
        return None


def is_available(timeout=3):
    """เช็คเร็วๆ ว่า Ollama เปิดอยู่ไหม — ใช้ timeout สั้นเพราะแค่ถามว่ามีใครรับสายไหม

    ⚠️ ตอบ True ไม่ได้แปลว่าโมเดลพร้อมใช้ แปลว่า service เปิดอยู่เท่านั้น (โมเดลอาจยัง
    ไม่ถูกดึงมา) — ผู้เรียกต้องเผื่อกรณี chat() คืน None อยู่ดี
    """
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def shrink_to_fit(text, max_chars=MAX_PROMPT_CHARS):
    """ตัดข้อความให้พอดีเพดาน context โดย **เก็บทั้งหัวและท้าย** ทิ้งตรงกลาง

    เหตุผลที่ไม่ตัดท้ายทิ้งอย่างเดียว: ผลของเราเรียงเป็น show interface (หัว) →
    show ip interface brief → show logging (ท้าย) ถ้าตัดท้ายทิ้งจะเสีย log ที่บอก
    ลำดับเหตุการณ์ ถ้าตัดหัวทิ้งจะเสียสถานะ interface ซึ่งเป็นหลักฐานหลัก — ทั้งสองส่วน
    สำคัญพอกัน จึงเก็บหัวท้ายแล้วคาดหมายไว้ตรงกลางให้เห็นชัดว่าถูกตัด

    ใส่เครื่องหมายไว้ให้เห็นเสมอ ไม่ตัดแบบเงียบๆ (บทเรียนจากพฤติกรรม num_ctx ของ Ollama เอง)
    """
    if not text:
        return ''
    if len(text) <= max_chars:
        return text

    budget = max_chars - len(TRUNCATION_MARK)
    head = int(budget * 0.6)      # ให้หัวมากกว่าเพราะสถานะ interface คือหลักฐานหลัก
    tail = budget - head
    return text[:head] + TRUNCATION_MARK + text[-tail:]


def chat(system, user, model=MODEL, timeout=TIMEOUT_SECONDS):
    """ส่ง prompt ไปให้โมเดล คืนข้อความคำตอบ (string) หรือ None ถ้าใช้ไม่ได้

    ใช้ /api/chat เพราะแยก system กับ user ออกจากกันได้ชัดกว่า /api/generate ที่ต้อง
    ยัดรวมเป็นก้อนเดียว — ข้อบังคับใน system prompt จะถูกโมเดลให้น้ำหนักมากกว่า
    """
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': shrink_to_fit(user)},
        ],
        'stream': False,
        'keep_alive': KEEP_ALIVE,
        'options': {'temperature': TEMPERATURE, 'num_ctx': NUM_CTX, 'seed': SEED},
    }

    result = _post('/api/chat', payload, timeout=timeout)
    if not result:
        return None

    content = (result.get('message') or {}).get('content')
    return content if content and content.strip() else None


def chat_timed(system, user, **kwargs):
    """เหมือน chat() แต่คืน (คำตอบ, เวลาที่ใช้เป็นวินาที) — ใช้เก็บตัวเลขลงตาราง
    เปรียบเทียบ 3 แนวทางใน 3.5.3 ขั้นที่ 4 โดยไม่ต้องจับเวลาเองทุกที่
    """
    start = time.time()
    response = chat(system, user, **kwargs)
    return response, round(time.time() - start, 2)


# ---------------------------------------------------------------------------
# ทดสอบ -- รัน: py lib\llm_client.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    passed = 0
    total = 0

    def check(ok, note):
        global passed, total
        total += 1
        passed += bool(ok)
        print(f"{'✓' if ok else '❌'} {note}")

    print("=== ก้อนที่ 1: logic ล้วน (ไม่ต้องเปิด Ollama) ===\n")

    check(NUM_CTX > 4096, "num_ctx ต้องมากกว่าค่าเริ่มต้น 4096 ของ Ollama (กันตัด prompt เงียบๆ)")
    check(TEMPERATURE == 0, "temperature ต้องเป็น 0 เพื่อให้รันซ้ำได้ผลเหมือนเดิม")
    check(TIMEOUT_SECONDS > 45, "timeout ต้องเผื่อ cold start ที่วัดได้จริง 45.49 วินาที")

    short = "Ethernet0/1 is administratively down"
    check(shrink_to_fit(short) == short, "ข้อความสั้นกว่าเพดาน ต้องไม่ถูกแตะเลย")

    long_text = ('HEAD' + 'x' * 50000 + 'TAIL')
    shrunk = shrink_to_fit(long_text, max_chars=1000)
    check(len(shrunk) <= 1000, "ข้อความยาวต้องถูกตัดให้ไม่เกินเพดาน")
    check(shrunk.startswith('HEAD'), "ต้องเก็บส่วนหัวไว้ (สถานะ interface = หลักฐานหลัก)")
    check(shrunk.endswith('TAIL'), "ต้องเก็บส่วนท้ายไว้ (show logging = ลำดับเหตุการณ์)")
    check(TRUNCATION_MARK.strip() in shrunk, "ต้องมีเครื่องหมายบอกว่าถูกตัด ไม่ตัดเงียบๆ")

    check(shrink_to_fit('') == '', "ข้อความว่างต้องไม่พัง")
    check(shrink_to_fit(None) == '', "None ต้องไม่พัง (ของจริงมี field ว่างได้)")

    # ยิงไปพอร์ตที่ไม่มีใครฟัง ต้องได้ None ไม่ใช่ระเบิด
    _saved_url = OLLAMA_URL
    OLLAMA_URL = 'http://localhost:1'
    check(_post('/api/chat', {}, timeout=2) is None, "Ollama ไม่ตอบ ต้องคืน None ไม่ raise")
    check(chat('sys', 'user', timeout=2) is None, "chat() ตอน Ollama ล่ม ต้องคืน None (ระบบเดินต่อด้วยกฎได้)")
    check(is_available(timeout=2) is False, "is_available() ต้องเป็น False เมื่อไม่มีใครรับสาย")
    OLLAMA_URL = _saved_url

    print(f"\nผลรวมก้อนที่ 1: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case (ก้อนที่ไม่ต้องเปิด Ollama)")

    print("\n=== ก้อนที่ 2: ยิงจริงไปที่ Ollama ===")
    if not is_available():
        print("⚠ ข้าม — ยังไม่ได้เปิด Ollama (ปกติถ้ากำลังเปิด CML อยู่)")
    else:
        print("Ollama เปิดอยู่ — ทดสอบยิงจริง (รอบแรกอาจนานถ้าโมเดลยังไม่อยู่ใน RAM)\n")
        answer, seconds = chat_timed(
            'You answer with exactly one word.',
            'Reply with the single word: OK',
        )
        print(f"คำตอบ : {answer!r}")
        print(f"เวลา   : {seconds} วินาที")
        if answer is None:
            print("❌ ยิงไม่สำเร็จ — เช็คว่าดึงโมเดลมาแล้วหรือยัง: ollama pull llama3.1:8b")
        else:
            print("✅ เชื่อมต่อและได้คำตอบจริง")
