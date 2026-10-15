r"""
Phase 5 งาน D — เตรียมอาการ "คิวขาเข้าเต็ม" (เคส C06) บนเส้นที่ต่อไป Site ใดก็ได้

ไฟล์นี้ทำหน้าที่อะไร
--------------------
เป็น "เครื่องมือฉีด fault" ตัวที่สอง คู่กับ fault_injection.py ที่มีอยู่เดิม

    fault_injection.py   ทำให้พอร์ตล่ม (shutdown)  -> มี syslog -> ระบบสร้าง TT เอง
    c06_congestion.py    ทำให้คิวขาเข้าล้น          -> ไม่มี syslog -> คนต้องเปิด TT เอง

แยกไฟล์กันเพราะทำงานคนละแบบสิ้นเชิง fault_injection.py มีแต่ shutdown/no shutdown
และผ่านการทดสอบมาตั้งแต่ Phase 1 แล้ว ส่วนตัวนี้ต้องแก้ค่าคิว + ยิง traffic +
อ่านตัวนับกลับมาแปลผล ถ้ายัดรวมกันจะทำให้ของที่ใช้ได้อยู่แล้วเสี่ยงพัง

🚫 ไฟล์นี้ **ไม่สร้าง TT ให้** โดยเจตนา — หัวใจของ C06 คือ fault ที่ระบบเฝ้าระวัง
   มองไม่เห็น จึงต้องพึ่งคนแจ้ง ถ้าสคริปต์สร้าง TT ให้เองก็ทำลายเรื่องเล่าทั้งหมด
   และขัดหลักการ "ห้ามฟันธงแทน agent" · หน้าที่ของมันคือเตรียมอาการบนอุปกรณ์
   ให้พร้อม แล้วบอกคนว่าต้องไปกรอกอะไรที่ Dashboard

วิธีสร้างอาการ
--------------
ย่อคิวขาเข้าจาก 75 ที่นั่งเหลือ 1 แล้วยิง traffic ปกติเข้าไป
  -> ไม่ได้รอให้เกิดความหนาแน่นจริง แต่ย่อคิวลงมาหา traffic แทน
  -> ตัวเลข drops เป็นค่าจริงจากอุปกรณ์ ไม่ได้ปลอม แต่เงื่อนไขถูกสร้างขึ้น
  -> หลักการเดียวกับที่ใช้ shutdown แทนการเดินไปดึงสายจริง (Constraint 6 ในเล่ม)

⚠️ ต้องคืนค่า hold-queue 75 in เสมอเมื่อเลิกใช้ ไม่งั้นการทดสอบรอบอื่นจะเพี้ยนหมด
   โดยไม่มีอะไรฟ้อง (สายเส้นนั้นจะสูญเสีย packet ~35% ค้างไว้ตลอด และ SSH/syslog
   ของ Site ปลายทางก็วิ่งผ่านเส้นเดียวกัน)
   -> ปุ่ม r ใน inject_menu.py เรียก restore_all() ให้อัตโนมัติแล้ว

วิธีรันเดี่ยวๆ:
    py c06_congestion.py status          ดูค่าคิวของทั้ง 3 เส้น
    py c06_congestion.py prepare 2       เตรียมอาการบนเส้นที่ 2 (ไป Site B)
    py c06_congestion.py traffic 2 3     ยิง traffic ซ้ำ 3 รอบ **โดยไม่ล้างตัวนับ**
    py c06_congestion.py restore 2       คืนค่าเส้นที่ 2 เป็น 75
    py c06_congestion.py restore all     คืนค่าทั้ง 3 เส้น
"""
import re
import sys

from netmiko import ConnectHandler

import fault_injection as fi

# เส้นที่ทำ C06 ได้ — ทั้ง 3 Site ใช้กลไกเดียวกันหมด เพราะการจัดคิวเป็นงานของ
# ซอฟต์แวร์ล้วน ไม่ขึ้นกับพอร์ตไหน (ต่างจาก fault ระดับกายภาพที่ IOL ทำไม่ได้เลย)
#
# 🚫 ไม่มี Ethernet0/3 ในรายการโดยเจตนา — เป็นทางเข้า lab (SSH + syslog) ห้ามแตะ
#    ถ้าย่อคิวเส้นนั้นจะตัดขาตัวเองทันที
SITES = {
    'a': {'interface': 'Ethernet0/0', 'site': 'Access-Site-A', 'core_ip': '10.0.1.1', 'ssh_port': 2201},
    'b': {'interface': 'Ethernet0/1', 'site': 'Access-Site-B', 'core_ip': '10.0.2.1', 'ssh_port': 2202},
    'c': {'interface': 'Ethernet0/2', 'site': 'Access-Site-C', 'core_ip': '10.0.3.1', 'ssh_port': 2203},
}

DEFAULT_SITE = 'b'          # เส้นที่ test_holdqueue.py ยืนยันไว้ตั้งแต่ 12 ส.ค.
INTERFACES = [s['interface'] for s in SITES.values()]

SMALL_QUEUE = 1
DEFAULT_QUEUE = 75

# ยิงเบาๆ ก่อน — มากเกินไปจะทำให้ตัวนับ ignored/input errors ขึ้นด้วย
# แล้วกฎ errors จะจับได้ก่อนแล้วตอบว่า "สายเสีย" ซึ่งผิด (= เคส A ในเล่ม)
PING_COUNT = 200
PING_SIZE = 1500

QUEUE_RE = re.compile(r'Input queue:\s*(\d+)/(\d+)/(\d+)/(\d+)')
ERROR_RE = re.compile(r'(\d+)\s+input errors.*?(\d+)\s+ignored')


# ลำดับที่ใช้แสดงในเมนู — เลข 1/2/3 อ้างถึงตัวไหน (เรียงตามหมายเลขพอร์ต)
ORDER = ['a', 'b', 'c']


def resolve(key):
    """รับได้ทั้ง 1/2/3, a/b/c, Ethernet0/1, E0/1, Access-Site-B"""
    if not key:
        return SITES[DEFAULT_SITE]
    k = str(key).strip().lower()
    if k in SITES:
        return SITES[k]
    if k.isdigit() and 1 <= int(k) <= len(ORDER):
        return SITES[ORDER[int(k) - 1]]
    for cfg in SITES.values():
        if k in (cfg['interface'].lower(), cfg['site'].lower(), cfg['interface'].lower().replace('ethernet', 'e')):
            return cfg
    raise ValueError(f"ไม่รู้จัก '{key}' — เลือกได้: {', '.join(SITES)} หรือ {', '.join(INTERFACES)}")


def _site_conn(cfg):
    """SSH เข้า Site ปลายทาง — วิ่งผ่าน NAT ของ Core เหมือนที่ lib/diagnostics.py ทำ"""
    return ConnectHandler(
        device_type='cisco_ios',
        host=fi.CORE['host'],
        port=cfg['ssh_port'],
        username=fi.CORE['username'],
        password=fi.CORE['password'],
        secret=fi.CORE['secret'],
    )


def _send_long(conn, command, seconds=180):
    """ping จำนวนมากใช้เวลานานกว่าค่า timeout ปกติของ netmiko

    รองรับ netmiko ทั้งรุ่นใหม่ (read_timeout) และรุ่นเก่า (delay_factor)

    ⚠️ ถ้า send_command() คืนค่าว่างหรือไม่มีบรรทัด 'Success rate' ให้ลองซ้ำด้วย
    send_command_timing() ซึ่งไม่รอ prompt pattern — ping ที่ยิงยาวๆ บน IOL บางครั้ง
    ทำให้ netmiko จับ prompt ไม่ตรงแล้วคืนค่าว่างออกมาโดยไม่ error (เจอจริง 21 ส.ค. 2026)
    """
    try:
        out = conn.send_command(command, read_timeout=seconds)
    except TypeError:
        out = conn.send_command(command, delay_factor=8, max_loops=2000)
    except Exception as e:
        out = f'(send_command ล้มเหลว: {type(e).__name__}: {e})'

    if 'Success rate' in (out or ''):
        return out

    try:
        out2 = conn.send_command_timing(command, delay_factor=8, last_read=10)
        if out2 and out2.strip():
            return out2
    except Exception:
        pass
    return out or ''


def _report_ping(out):
    """พิมพ์อัตราสำเร็จ ถ้าอ่านไม่ได้ให้โชว์ผลดิบเพื่อให้ debug ได้ ไม่ใช่เงียบไปเฉยๆ"""
    rate = [l.strip() for l in out.splitlines() if 'Success rate' in l]
    if rate:
        print(f"      {rate[0]}")
        return True
    tail = [l.strip() for l in out.splitlines() if l.strip()][-5:]
    print("      ⚠️ อ่านอัตราสำเร็จไม่ได้ — ผลดิบที่ได้กลับมา:")
    if not tail:
        print("         | (ว่างเปล่า — คำสั่งอาจไม่ถูกส่งเข้าไปเลย)")
    for l in tail:
        print(f"         | {l[:88]}")
    return False


def read_queue(conn, interface):
    """คืน (size, max, drops, flushes) จากบรรทัด Input queue"""
    out = conn.send_command(f'show interfaces {interface}')
    m = QUEUE_RE.search(out)
    return tuple(int(g) for g in m.groups()) if m else None


def read_errors(conn, interface):
    """คืน (input_errors, ignored) — ใช้ดูว่าจะไปโดนกฎ errors ดักหรือเปล่า"""
    out = conn.send_command(f'show interfaces {interface}')
    m = ERROR_RE.search(out)
    return tuple(int(g) for g in m.groups()) if m else None


def _set_queue(conn, interface, size):
    conn.send_config_set([f'interface {interface}', f'hold-queue {size} in'])


def status_all(conn=None):
    """คืน dict {interface: max ของคิว} — 1 = อาการค้างอยู่ · 75 = ปกติ"""
    own = conn is None
    if own:
        conn = fi.connect()
    try:
        result = {}
        for itf in INTERFACES:
            q = read_queue(conn, itf)
            result[itf] = q[1] if q else None
        return result
    finally:
        if own:
            conn.disconnect()


def restore(key=None, conn=None, quiet=False):
    """คืนค่าคิวของเส้นเดียวเป็น 75"""
    cfg = resolve(key)
    own = conn is None
    if own:
        conn = fi.connect()
    try:
        q = read_queue(conn, cfg['interface'])
        if q and q[1] == DEFAULT_QUEUE:
            if not quiet:
                print(f"   {cfg['interface']} เป็น hold-queue {DEFAULT_QUEUE} อยู่แล้ว")
            return False
        _set_queue(conn, cfg['interface'], DEFAULT_QUEUE)
        after = read_queue(conn, cfg['interface'])
        print(f"🔄 คืนค่า {cfg['interface']} เป็น hold-queue {DEFAULT_QUEUE} in แล้ว (อ่านซ้ำได้ {after[1] if after else '?'})")
        return True
    finally:
        if own:
            conn.disconnect()


def restore_all(conn=None, quiet=False):
    """คืนค่าคิวทั้ง 3 เส้น — เรียกจากปุ่ม r ของ inject_menu.py"""
    own = conn is None
    if own:
        conn = fi.connect()
    try:
        changed = 0
        for key in SITES:
            if restore(key, conn=conn, quiet=quiet):
                changed += 1
        return changed
    finally:
        if own:
            conn.disconnect()


def prepare(key=None):
    """เตรียมอาการ C06 บนเส้นที่เลือก แล้วบอกคนว่าต้องไปทำอะไรต่อ — ไม่สร้าง TT ให้"""
    cfg = resolve(key)
    interface, site = cfg['interface'], cfg['site']
    assert interface != fi.UPLINK, "ห้ามแตะ Ethernet0/3 (ทางเข้า lab)"

    print()
    print("=" * 68)
    print(f"  เตรียมอาการ C06 — คิวขาเข้าเต็ม บน Core {interface}")
    print(f"  (เส้นที่ต่อไป {site} · ปลายทาง ping = {cfg['core_ip']})")
    print("=" * 68)

    core = fi.connect()
    try:
        # ---------- 1. ล้างตัวนับให้ค่าตั้งต้นสะอาด ----------
        # ⚠️ ทำให้บรรทัด Last clearing เปลี่ยนจาก never เป็นเวลาที่ล้าง ซึ่งเป็น
        #    ร่องรอยจากขั้นตอนทดลอง ไม่ใช่อาการของเครือข่าย — ต้องประกาศในเล่ม
        #    (14 ส.ค. 2026 โมเดลเคยไปคว้าบรรทัดนี้มาตอบว่าเป็นสาเหตุ แล้วถูกด่านตรวจปฏิเสธ)
        print("\n[1/4] ล้างตัวนับของพอร์ต ...")
        core.send_command_timing(f'clear counters {interface}')
        core.send_command_timing('\n')          # ตอบ [confirm]
        before = read_queue(core, interface)
        print(f"      ค่าตั้งต้น Input queue = {before}   (size/max/drops/flushes)")

        # ---------- 2. ย่อคิว ----------
        print(f"\n[2/4] ย่อคิวขาเข้าเหลือ {SMALL_QUEUE} ที่นั่ง ...")
        _set_queue(core, interface, SMALL_QUEUE)

        cfg_text = core.send_command(f'show running-config interface {interface}')
        kept = 'hold-queue' in cfg_text.lower()
        after_cfg = read_queue(core, interface)
        changed = bool(after_cfg and after_cfg[1] == SMALL_QUEUE)

        # ด่านตรวจ 2 ข้อ — บทเรียนจาก C05 ที่ IOS รับคำสั่งเงียบๆ แต่ไม่เก็บลง config
        print(f"      running-config เก็บคำสั่งไว้ : {'✅ เก็บ' if kept else '❌ ไม่เก็บ'}")
        print(f"      ค่า max ของคิวเปลี่ยนจริง   : {'✅ เปลี่ยนเป็น ' + str(SMALL_QUEUE) if changed else '❌ ไม่เปลี่ยน'}")
        if not (kept and changed):
            print("\n🔴 ไม่ผ่านด่านตรวจ — อาการเดียวกับ duplex (C05) ที่สั่งได้แต่ไม่มีผลจริง")
            print("   หยุดก่อน อย่าเพิ่งยิง traffic แล้วบันทึกผลนี้ไว้")
            return
    finally:
        core.disconnect()

    # ---------- 3. ยิง traffic จาก Site ปลายทาง ----------
    print(f"\n[3/4] ยิง traffic จาก {site} เข้าหา Core ({PING_COUNT} packet ขนาด {PING_SIZE}) ...")
    print("      ใช้เวลาประมาณ 1-2 นาที รอสักครู่")
    try:
        conn_site = _site_conn(cfg)
    except Exception as e:
        print(f"\n⚠️ SSH เข้า {site} ไม่ได้: {type(e).__name__}")
        print(f"   ยิง traffic เองจาก console ของ {site} ได้:")
        print(f"      ping {cfg['core_ip']} repeat {PING_COUNT} size {PING_SIZE} timeout 0")
        return
    try:
        out = _send_long(conn_site, f"ping {cfg['core_ip']} repeat {PING_COUNT} size {PING_SIZE} timeout 0")
        rate = [l.strip() for l in out.splitlines() if 'Success rate' in l]
        print(f"      {rate[0] if rate else '(อ่านอัตราสำเร็จไม่ได้ — ดูผลดิบเอง)'}")
    finally:
        conn_site.disconnect()

    # ---------- 4. อ่านผลกลับจาก Core ----------
    print("\n[4/4] อ่านตัวนับกลับจาก Core ...")
    core = fi.connect()
    try:
        q = read_queue(core, interface)
        err = read_errors(core, interface)
    finally:
        core.disconnect()

    print(f"      Input queue = {q}   (size/max/drops/flushes)")
    if err:
        print(f"      input errors = {err[0]} · ignored = {err[1]}")

    drops = q[2] if q else 0
    dirty = bool(err and err[0] > 0)

    print()
    print("=" * 68)
    if drops == 0:
        print("  ⚠️ drops ยังเป็น 0 — เลือกเมนูนี้ใหม่เพื่อยิง traffic ซ้ำอีกรอบ")
    elif dirty:
        print(f"  ⚠️ drops = {drops} แต่ input errors = {err[0]} ขึ้นมาด้วย")
        print("     -> กฎ errors จะจับได้ก่อนแล้วตอบว่า 'สายเสีย' ซึ่งผิด (= เคส A)")
        print("     -> ถ้าอยากได้เคส B (ให้เข้าคิวชั้นวิเคราะห์) ให้เลือกเมนูนี้ใหม่")
        print("        เพื่อล้างตัวนับแล้วยิงเบากว่าเดิม")
    else:
        print(f"  ✅ อาการพร้อมแล้ว — drops = {drops} · input errors = 0")
        print("     กฎทั้ง 4 ข้อจะจับไม่ได้ -> TT จะเข้าคิวชั้นวิเคราะห์ (llm_status='pending')")
    print("=" * 68)

    print()
    print("  👉 ขั้นต่อไปต้องทำเอง (สคริปต์ไม่สร้าง TT ให้โดยเจตนา):")
    print("     1. เปิด Dashboard -> ปุ่ม 'แจ้งเหตุด้วยตนเอง'")
    print(f"     2. กรอก  อุปกรณ์ = Core   ·   Interface = {interface}")
    print(f"        ⚠️ ห้ามเลือก {site} เพราะระบบจะ SSH เข้าเครื่องนั้นแล้วสั่ง")
    print(f"           show interface {interface} ซึ่ง {site} ไม่ได้ใช้พอร์ตนี้")
    print("     3. คำอธิบาย: ลูกค้าแจ้งว่าใช้งานช้าผิดปกติ ไฟล์โหลดไม่จบ")
    print("     4. รอ diagnose_worker.py วินิจฉัย แล้วรัน 6_analyze_pending.bat")
    print()
    print(f"  ⚠️ เสร็จแล้วอย่าลืมกด r (ซ่อมทุกอย่าง) เพื่อคืนค่า hold-queue {DEFAULT_QUEUE} in")
    print()


def traffic(key=None, rounds=1, count=500):
    """ยิง traffic ซ้ำ **โดยไม่ล้างตัวนับและไม่แตะค่าคิว** — ใช้ตอน prepare แล้ว drops ยังเป็น 0

    ⚠️ ทำไมต้องแยกจาก prepare(): prepare() ล้างตัวนับที่ขั้นที่ 1 ทุกครั้ง
    การ "รันซ้ำ" prepare จึงลบ drops ที่สะสมไว้จากรอบก่อนทิ้งทุกรอบ แล้วเริ่มนับใหม่จาก 0
    ซึ่งเป็นสาเหตุที่ยิงเท่าไหร่ drops ก็ไม่ขึ้น (เจอจริง 17 และ 21 ส.ค. 2026)

    C06_runbook.md (14 ส.ค.) พิสูจน์แล้วว่า drops สะสมขึ้นตามปริมาณที่ยิง
    0 -> 12 -> 82 จาก packet รวม ~3,500 ชิ้น (~2%) โดย **ไม่ล้างตัวนับระหว่างรอบ**

    วิธีรัน:
        py c06_congestion.py traffic c          ยิง 1 รอบ (500 packet)
        py c06_congestion.py traffic c 3        ยิง 3 รอบติดกัน
    """
    cfg = resolve(key)
    interface, site = cfg['interface'], cfg['site']

    core = fi.connect()
    try:
        q0 = read_queue(core, interface)
    finally:
        core.disconnect()

    if not q0:
        print(f"\n❌ อ่านค่าคิวของ {interface} ไม่ได้\n")
        return
    if q0[1] != SMALL_QUEUE:
        print(f"\n❌ {interface} ยัง hold-queue = {q0[1]} ไม่ใช่ {SMALL_QUEUE}")
        print(f"   ต้องสั่ง  py c06_congestion.py prepare {key or DEFAULT_SITE}  ก่อน\n")
        return

    print()
    print("=" * 68)
    print(f"  ยิง traffic ซ้ำบน Core {interface}  ({rounds} รอบ × {count} packet)")
    print(f"  🚫 ไม่ล้างตัวนับ · 🚫 ไม่แตะค่าคิว — drops จะสะสมต่อจากเดิม")
    print("=" * 68)
    print(f"\n  ค่าเริ่มต้น Input queue = {q0}   (size/max/drops/flushes)")

    try:
        conn_site = _site_conn(cfg)
        try:
            conn_site.enable()          # ping ใช้ได้ใน user exec แต่ enable ทำให้ prompt คงที่
        except Exception:
            pass
    except Exception as e:
        print(f"\n⚠️ SSH เข้า {site} ไม่ได้: {type(e).__name__}")
        print(f"   ยิงเองจาก console ของ {site} ได้:")
        print(f"      ping {cfg['core_ip']} repeat {count} size {PING_SIZE} timeout 0")
        return

    try:
        print(f"\n  prompt ที่ได้จาก {site}: {conn_site.find_prompt()!r}")
        for i in range(1, rounds + 1):
            print(f"\n  [รอบ {i}/{rounds}] ping {cfg['core_ip']} repeat {count} size {PING_SIZE} timeout 0 ...")
            out = _send_long(conn_site, f"ping {cfg['core_ip']} repeat {count} size {PING_SIZE} timeout 0")
            ok_rate = _report_ping(out)
            if not ok_rate and i == 1:
                print("      💡 ถ้าว่างเปล่าทุกรอบ แปลว่า traffic ไม่ได้ถูกยิงจริง — drops จะไม่มีทางขึ้น")
                print(f"         ลองยิงเองจาก console ของ {site} ใน CML Web UI:")
                print(f"            ping {cfg['core_ip']} repeat {count} size {PING_SIZE} timeout 0")

            core = fi.connect()
            try:
                q = read_queue(core, interface)
                err = read_errors(core, interface)
            finally:
                core.disconnect()
            print(f"      Input queue = {q}  ·  input errors = {err[0] if err else '?'}")
            if q and q[2] > 0:
                print(f"      ✅ drops ขึ้นแล้ว = {q[2]}")
    finally:
        conn_site.disconnect()

    core = fi.connect()
    try:
        q = read_queue(core, interface)
        err = read_errors(core, interface)
    finally:
        core.disconnect()

    drops = q[2] if q else 0
    dirty = bool(err and err[0] > 0)

    print()
    print("=" * 68)
    if drops == 0:
        print(f"  ⚠️ drops ยังเป็น 0 หลังยิงไป {rounds * count} packet")
        print("     ลองสั่งซ้ำอีกรอบ (ตัวนับไม่ถูกล้าง drops จะสะสมต่อ)")
        print("     หรือเปลี่ยนไปใช้เส้น b (Ethernet0/1) ซึ่ง C06_runbook พิสูจน์แล้วว่าได้ผล")
    elif dirty:
        print(f"  ⚠️ drops = {drops} แต่ input errors = {err[0]} ขึ้นมาด้วย")
        print("     -> กฎ errors จะจับได้ก่อนแล้วตอบว่า 'สายเสีย' ซึ่งผิด (= เคส A ในเล่ม)")
        print("     -> ถ้าอยากได้เคส B ต้อง prepare ใหม่แล้วยิงเบากว่านี้")
    else:
        print(f"  ✅ อาการพร้อมแล้ว — drops = {drops} · input errors = 0")
        print("     กฎทั้ง 4 ข้อจะจับไม่ได้ -> TT จะเข้าคิวชั้นวิเคราะห์")
        print()
        print("  👉 รีบเปิด TT ทันที (Dashboard -> แจ้งเหตุด้วยตนเอง)")
        print(f"     อุปกรณ์ = Core   ·   Interface = {interface}")
    print("=" * 68)
    print()


def main():
    action = sys.argv[1].lower() if len(sys.argv) > 1 else 'status'
    target = sys.argv[2] if len(sys.argv) > 2 else None

    if action == 'prepare':
        prepare(target)
    elif action == 'traffic':
        rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        traffic(target, rounds=rounds)
    elif action == 'restore':
        if target and target.lower() == 'all':
            restore_all()
        else:
            restore(target)
    elif action == 'status':
        table = status_all()
        print()
        print("ค่าคิวขาเข้าของแต่ละเส้นบน Core")
        print("-" * 56)
        for i, key in enumerate(ORDER, start=1):
            cfg = SITES[key]
            q = table.get(cfg['interface'])
            icon = '🔴 อาการ C06 ค้างอยู่' if q == SMALL_QUEUE else ('🟢 ปกติ' if q == DEFAULT_QUEUE else '⚠️ ค่าแปลก')
            print(f"  {i}. {cfg['interface']:<14} hold-queue = {str(q):<4} {icon}   ({cfg['site']})")
        print("-" * 56)
        print()
    else:
        print(f"❌ ไม่รู้จักคำสั่ง '{action}' — ใช้ได้: status / prepare <1|2|3> / traffic <1|2|3> [รอบ] / restore <1|2|3|all>")
        sys.exit(1)


if __name__ == '__main__':
    main()
