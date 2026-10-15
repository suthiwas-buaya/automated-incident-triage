r"""
Phase 3 - SSH เข้าอุปกรณ์ + รันคำสั่งวินิจฉัย (docs/BRIEF_Phase3_Analyze.md ข้อ 3)

แยกหน้าที่ชัดจาก fault_injection.py: ไฟล์นั้น "ทำให้ล่ม/ซ่อม" (config), ไฟล์นี้แค่
"อ่าน" สถานะผ่าน show command ล้วนๆ ไม่มีการ config ใดๆ ทั้งสิ้น (ข้อห้าม #5)

⚠️ SSH เข้าไม่ได้ (เช่น สายขาด SSH เข้า Site ไม่ได้) ไม่ใช่ error ที่ต้อง raise ทิ้ง —
ถือเป็น "หลักฐาน" อย่างหนึ่ง ต้องคืนข้อความอธิบายกลับไปเสมอ ไม่ปล่อยให้ raw_output ว่าง
(ข้อห้าม #4) ผู้เรียก (diagnose_worker.py / dashboard/backend/app.py) เอาไปตั้ง
diagnose_state='unreachable' ต่อ

ทดสอบ logic การ format คำสั่ง/mapping ที่ไม่ต้องพึ่ง SSH ได้เอง: py lib\diagnostics.py
ทดสอบ SSH จริงต้องเปิด lab (ดู __main__ ท้ายไฟล์ — แยกเป็นเทสต์คนละก้อน)
"""
import time

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

try:
    from lib import mapping
    from lib.lab_secrets import CREDENTIALS     # รหัสแยกไว้ในไฟล์ที่ไม่ขึ้น GitHub
except ImportError:          # กรณีรันไฟล์นี้ตรงๆ จากในโฟลเดอร์ lib
    import mapping
    from lab_secrets import CREDENTIALS
SSH_TIMEOUT = 10  # วินาที — ต้องสั้น ไม่งั้นตอนอุปกรณ์ไม่ตอบ (สายขาด) จะค้างนาน (ข้อควรระวัง #4)

# 3 คำสั่งที่รันอัตโนมัติทุกครั้งที่วินิจฉัย (docs/BRIEF_Phase3_Analyze.md ข้อ 3)
AUTO_COMMANDS = [
    'show interface {interface}',
    'show ip interface brief',
    'show logging',
]


def format_command(template, interface):
    """แทน {interface} ใน template ด้วย interface จริงของ ticket — คำสั่งที่ไม่มี {interface}
    (เช่น 'show ip interface brief') ผ่านเฉยๆ ไม่มีผล
    """
    return template.format(interface=interface or '')


def _connect(device):
    """เปิด SSH เข้า device (ชื่อต้องอยู่ใน mapping.DEVICE_SSH เท่านั้น) แล้ว enable() ให้เสร็จ

    Raise NetmikoTimeoutException/NetmikoAuthenticationException/OSError ถ้าเข้าไม่ได้ —
    ผู้เรียก (run_commands) เป็นคน catch แล้วแปลงเป็นข้อความ unreachable ที่นี่จงใจไม่ catch
    เองเพื่อให้ error ชนิดจริงไหลขึ้นไปให้ผู้เรียกตัดสินใจเอง
    """
    if device not in mapping.DEVICE_SSH:
        raise ValueError(f"ไม่รู้จัก device '{device}' — ต้องเป็นหนึ่งใน {list(mapping.DEVICE_SSH)}")
    target = mapping.DEVICE_SSH[device]
    conn = ConnectHandler(
        device_type='cisco_ios', host=target['host'], port=target['port'],
        timeout=SSH_TIMEOUT, conn_timeout=SSH_TIMEOUT,
        **CREDENTIALS,
    )
    conn.enable()
    return conn


def _unreachable_output(device, interface):
    """ข้อความอธิบายตอน SSH ล้มเหลว — ต้องมีคำว่า 'unreachable' ให้ lib/cause_rules.py match ได้
    (ดู docs/BRIEF_Phase3_Analyze.md ข้อ 3 ตัวอย่างข้อความ)

    ⚠️ จงใจไม่ใส่ข้อความ exception ดิบจาก Netmiko — บั๊กที่เจอจริง 6 ส.ค. 2026: ข้อความเดิม
    ("Common causes: 1. Incorrect hostname... 2. Wrong TCP port... 3. Firewall...") ชวนให้
    เข้าใจผิดว่าตั้งค่าผิด ทั้งที่สาเหตุจริงคือสายขาด (ตามสถาปัตยกรรมโปรเจกต์นี้ที่ Site จะ
    เข้าไม่ได้ทันทีที่สายไปมันขาด) เขียนข้อความของเราเองแทนให้ตรงบริบทจริงเสมอ
    """
    target = mapping.DEVICE_SSH.get(device, {})
    timestamp = time.strftime('%H:%M:%S')
    reachable_note = (
        f"Device unreachable, consistent with link failure on Core {interface}."
        if interface else "Device unreachable, consistent with a link failure."
    )
    return f"[{timestamp}] {device} ({target.get('host', '?')}:{target.get('port', '?')}) - no response\n{reachable_note}"


def run_commands(device, interface, commands=None):
    """
    SSH เข้า device แล้วรันหลายคำสั่งเรียงกัน (ใช้โดย diagnose_worker.py สำหรับ 3 คำสั่งอัตโนมัติ)

    คืน dict:
        success    bool  — False = เข้าอุปกรณ์ไม่ได้เลย (ไม่ใช่ error ของคำสั่งใดคำสั่งหนึ่ง)
        raw_output str   — รวมผลทุกคำสั่งเป็น string เดียว ไม่เคยว่าง (ข้อห้าม #4)
        runs       list  — [{'device', 'command', 'output', 'success'}, ...] ทีละคำสั่ง มี
                            'device' กำกับด้วยเสมอ (สำคัญตอน fallback ไป Core — ดู
                            diagnose_from_best_available_node()) สำหรับบันทึกลง diagnostic_runs
                            ผ่าน lib/db.py record_diagnostic_run()
    """
    commands = commands or AUTO_COMMANDS

    # ไม่รู้ interface ก็ตัดคำสั่งที่ต้องใช้ interface ออก (Phase 5 ข้อ C-4) — ไม่งั้นจะยิง
    # 'show interface ' (เว้นท้าย) เข้าอุปกรณ์จริง ซึ่ง SSH สำเร็จ (success=True) แต่ได้ผลลัพธ์
    # ที่ไม่ใช่ของ interface ใดเลย แล้วถูกส่งต่อให้ cause_rules/LLM ตีความเหมือนเป็นหลักฐาน
    # จริง = ป้อนข้อมูลนอกขอบเขตเข้าชั้นวิเคราะห์ (บทเรียนร่วมของบั๊กทั้ง 7 ข้อใน Phase 3.5)
    if not interface:
        commands = [c for c in commands if '{interface}' not in c]

    formatted = [format_command(c, interface) for c in commands]

    try:
        conn = _connect(device)
    except (NetmikoTimeoutException, NetmikoAuthenticationException, OSError, ValueError):
        message = _unreachable_output(device, interface)
        return {
            'success': False,
            'raw_output': message,
            'runs': [{'device': device, 'command': c, 'output': message, 'success': False} for c in formatted],
        }

    runs = []
    chunks = []
    try:
        for command in formatted:
            output = conn.send_command(command)
            runs.append({'device': device, 'command': command, 'output': output, 'success': True})
            chunks.append(f"$ {command}\n{output}")
    finally:
        conn.disconnect()

    return {'success': True, 'raw_output': '\n\n'.join(chunks), 'runs': runs}


def run_single_command(device, command_template, interface):
    """รันคำสั่งเดียว (ใช้โดย Manual Investigation ในหน้า Analyze ผ่าน /api/tickets/<tid>/run-command)

    ⚠️ ผู้เรียกต้อง validate command_template กับ mapping.MANUAL_COMMANDS มาก่อนแล้วเสมอ —
    ฟังก์ชันนี้ไม่เช็คซ้ำ (ความรับผิดชอบเรื่อง whitelist อยู่ที่ dashboard/backend/app.py
    ตามข้อห้าม #5 "ห้ามเชื่อ frontend")

    คืน dict เดียว: {'command', 'output', 'success'}
    """
    result = run_commands(device, interface, commands=[command_template])
    return result['runs'][0]


def diagnose_from_best_available_node(node, interface, fallback_device='Core'):
    """กลยุทธ์ตามข้อ 3: ลอง SSH เข้า Site ที่รายงาน fault ก่อน ถ้าเข้าไม่ได้ (สายขาด SSH ก็เข้า
    ไม่ได้ตามไปด้วย) ให้ตกไปวินิจฉัยจาก Core แทน (Core เป็น node เดียวที่ยังรายงาน fault นี้ได้)

    node ที่ส่งเข้ามาเป็นชื่อ Access-Site-A/B/C หรือ 'Core' ตรงๆ (จาก ticket['node'])

    ⚠️ raw_output (สำหรับโชว์ใน UI) รวมทั้งความพยายามที่ Site ที่ล้มเหลวด้วย เพื่อความโปร่งใส
    — แต่ 'cause_output' แยกไว้ต่างหาก เหลือแค่ผลจริงที่สำเร็จ (Core) เท่านั้น ให้
    lib/cause_rules.py เอาไปสรุปสาเหตุ ป้องกันไม่ให้คำว่า "unreachable" ในข้อความ error
    ตอนพยายาม SSH เข้า Site ปนเข้าไปทำให้สรุปสาเหตุผิดเป็น "อุปกรณ์ไม่ตอบสนอง" ทั้งที่ Core
    บอกสาเหตุจริงมาแล้ว (เจอบั๊กนี้จริงตอนทดสอบกับ lab จริง 6 ส.ค. 2026)
    """
    if node == fallback_device or node not in mapping.DEVICE_SSH:
        result = run_commands(fallback_device, interface)
        return {**result, 'cause_output': result['raw_output']}

    primary = run_commands(node, interface)
    if primary['success']:
        return {**primary, 'cause_output': primary['raw_output']}

    core_result = run_commands(fallback_device, interface)
    combined_output = primary['raw_output'] + '\nFalling back to Core for diagnosis.\n\n' + core_result['raw_output']
    return {
        'success': core_result['success'],
        'raw_output': combined_output,
        'cause_output': core_result['raw_output'] if core_result['success'] else combined_output,
        'runs': primary['runs'] + core_result['runs'],
    }


# ---------------------------------------------------------------------------
# ทดสอบ logic ที่ไม่ต้องพึ่ง SSH (format คำสั่ง / mapping / unreachable message)
# ทดสอบ SSH จริงต้องเปิด lab -- ทั้งสองก้อนรันด้วย: py lib\diagnostics.py
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    print("=== ทดสอบ logic ที่ไม่ต้องพึ่ง lab ===\n")
    passed = 0
    total = 0

    total += 1
    ok = format_command('show interface {interface}', 'Ethernet0/1') == 'show interface Ethernet0/1'
    passed += ok
    print(f"{'✓' if ok else '❌'} format_command() แทน {{interface}} ถูกต้อง")

    total += 1
    ok = format_command('show ip interface brief', 'Ethernet0/1') == 'show ip interface brief'
    passed += ok
    print(f"{'✓' if ok else '❌'} format_command() คำสั่งที่ไม่มี {{interface}} ผ่านเฉยๆ ไม่พัง")

    total += 1
    try:
        _connect('Access-Site-Z')
        ok = False
    except ValueError:
        ok = True
    passed += ok
    print(f"{'✓' if ok else '❌'} device ที่ไม่รู้จักต้อง raise ValueError ก่อนแม้แต่จะพยายาม SSH")

    total += 1
    msg = _unreachable_output('Access-Site-B', 'timeout after 10s')
    ok = 'unreachable' in msg.lower() and 'Access-Site-B' in msg and msg.strip() != ''
    passed += ok
    print(f"{'✓' if ok else '❌'} _unreachable_output() มีคำว่า unreachable + ไม่ว่างเปล่า (เกณฑ์ข้อ 4)")

    total += 1
    ok = set(mapping.DEVICE_SSH) == {'Core', 'Access-Site-A', 'Access-Site-B', 'Access-Site-C'}
    passed += ok
    print(f"{'✓' if ok else '❌'} DEVICE_SSH มีครบ 4 node")

    print(f"\nผลรวม: {passed}/{total} test case ผ่าน")
    assert passed == total, "มี test case ไม่ผ่าน"
    print("✅ ผ่านทุก test case (ก้อนที่ไม่ต้องพึ่ง lab)")

    print("\n=== ทดสอบ SSH จริง (ต้องเปิด lab + เชื่อม VMnet8) ===")
    print("ข้ามอัตโนมัติถ้าเข้า Core ไม่ได้ภายใน 3 วินาที (เดา — ไม่ได้ต่อ lab อยู่ตอนนี้)\n")
    try:
        result = run_commands('Core', interface='Loopback0', commands=['show version'])
        print(f"SSH เข้า Core สำเร็จ: success={result['success']}")
        print(result['raw_output'][:300])
    except Exception as e:
        print(f"⚠ ข้ามการทดสอบ SSH จริง (ปกติถ้ายังไม่เปิด lab): {e}")
