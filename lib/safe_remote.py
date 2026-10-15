"""
safe_remote — สั่ง shutdown บน Access router โดยไม่ตัดขาตัวเอง

ปัญหาที่แก้:
    ตาม topology จริง (ดู Phase0.3_Topology_Setup.md) Access ทั้ง 3 ตัวต่อกับ
    โลกภายนอกผ่าน Ethernet0/0 -> Core เท่านั้น ไม่มีช่องทาง out-of-band
    เพราะฉะนั้น SSH ที่เข้าทาง port 2201/2202/2203 ก็วิ่งผ่าน Ethernet0/0 เส้นนั้นเอง

    พอสั่ง "shutdown Ethernet0/0" บน Access เพื่อจำลอง fault แบบ Physical
    -> session ขาดทันที และ **ต่อกลับเข้าไปสั่ง no shutdown ไม่ได้อีกเลย**
    ต้องไปเปิด Console ใน CML Web UI แก้มือ ซึ่งช้าและทำตอนสาธิตไม่ได้

วิธีแก้ — dead-man switch ด้วย reload:
    1. ก่อนทำให้ล่ม สั่ง write memory ให้ startup-config เป็นสภาพที่ "ดี" แน่ๆ
    2. ตั้ง reload in N   (N นาที)  <- ตัวนี้คือประกันชีวิต
    3. สั่ง shutdown โดย **ไม่ save**
    4. ตอนซ่อม: no shutdown แล้ว reload cancel

    ถ้าขั้นที่ 4 ทำไม่ได้ (เข้าไม่ถึงจริงๆ) router จะ reboot เองใน N นาที
    แล้วกลับมาด้วย startup-config ที่ดีจากขั้นที่ 1 = กู้คืนเองได้เสมอ

ข้อแลกเปลี่ยนที่ต้องรู้:
    fault แบบนี้ "ไม่ค้างถาวร" เหมือน fault ที่ทำบน Core — มันจะหายเองเมื่อครบ N นาที
    เป็นข้อจำกัดของแล็บที่ไม่มี out-of-band management ต้องเขียนไว้ในเล่มตรงๆ
    ตั้ง N ให้ยาวพอสำหรับรอบสาธิต (ค่าตั้งต้น 15 นาที) แต่ไม่ยาวจนลืม
"""
import time
from datetime import datetime, timedelta

from netmiko import ConnectHandler

import fault_injection as fi

# port forwarding ของแต่ละ Access — ตรงกับ lib/mapping.py DEVICE_SSH
ACCESS_SSH_PORT = {
    'Access-Site-A': 2201,
    'Access-Site-B': 2202,
    'Access-Site-C': 2203,
}

DEFAULT_RELOAD_MINUTES = 15


def _connect(site):
    port = ACCESS_SSH_PORT.get(site)
    if port is None:
        raise ValueError(f"ไม่รู้จัก site '{site}' — เลือกได้: {', '.join(ACCESS_SSH_PORT)}")
    device = dict(fi.CORE)
    device['port'] = port
    device['conn_timeout'] = 15
    conn = ConnectHandler(**device)
    conn.enable()
    return conn


def _arm_deadman(conn, minutes):
    """
    ตั้ง reload in N แล้ว **ตรวจสอบซ้ำว่าตั้งสำเร็จจริง**

    ต้อง save ก่อนเสมอ ไม่งั้น IOS จะถามว่าจะ save ไหม และถ้าเผลอตอบ yes
    ตอนที่ interface ล่มอยู่ startup-config จะกลายเป็นสภาพที่พัง

    บทเรียนจากการทดลอง 17 ก.ย. 2026:
        เดิมสั่ง reload แล้วเชื่อว่าสำเร็จเลย แต่ IOS ถาม 'Proceed with reload?
        [confirm]' ช้ากว่าที่ send_command_timing รอ คำสั่งจึงตกไปเงียบๆ
        ผลคือ Access-Site-A และ B ถูกปิดพอร์ตโดยไม่มีประกัน กู้ไม่ได้เลย
        ต้องไปแก้ผ่าน Console ใน CML

    ตอนนี้จึงยืนยันด้วย 'show reload' ทุกครั้ง ถ้าไม่ขึ้นว่ามี reload รออยู่
    จะ raise ออกไป เพื่อให้ผู้เรียกยกเลิกการทำให้ล่ม ดีกว่าปล่อยให้ล็อกตัวเอง
    """
    conn.save_config()                       # startup-config = สภาพดี (ยืนยันก่อนพัง)

    out = conn.send_command_timing(f'reload in {minutes}', read_timeout=20)
    # ตอบทุก prompt ที่โผล่มา — จำนวนและลำดับต่างกันไปตามรุ่น IOS
    for _ in range(3):
        low = out.lower()
        if 'confirm' in low or 'yes/no' in low or 'y/n' in low:
            out += conn.send_command_timing('\n', read_timeout=20)
        else:
            break

    time.sleep(1)
    check = conn.send_command('show reload')
    if 'no reload' in check.lower() or 'shutdown' not in check.lower():
        # ยังไม่มี reload รออยู่ -> ถือว่าตั้งไม่สำเร็จ
        raise RuntimeError(
            f"ตั้งประกัน reload ไม่สำเร็จ (show reload ตอบว่า: {check.strip()[:80]}) "
            f"— ยกเลิกการทำให้ล่ม เพราะถ้าทำต่อจะเข้าไปกู้ไม่ได้"
        )
    return check.strip()


def disarm(conn):
    """ยกเลิกประกัน reload — เรียกจากภายนอกได้"""
    out = conn.send_command_timing('reload cancel')
    if 'onfirm' in out:
        out += conn.send_command_timing('\n')
    return out


def break_remote(site, config_lines, minutes=DEFAULT_RELOAD_MINUTES):
    """
    ทำให้ Access ล่มด้วย config_lines โดยมีประกัน reload รออยู่

    คืน dict: {'site', 'armed_at', 'auto_recover_at', 'minutes'}
    """
    conn = _connect(site)
    armed_at = datetime.now()
    recover_at = armed_at + timedelta(minutes=minutes)
    try:
        print(f"  [{site}] บันทึก config ที่ดีไว้ก่อน แล้วตั้งประกัน reload in {minutes} นาที")
        _arm_deadman(conn, minutes)
        # นับเวลาจากตอนที่สั่ง reload จริง ไม่ใช่ตอนจบฟังก์ชัน
        armed_at = datetime.now()
        recover_at = armed_at + timedelta(minutes=minutes)
        print(f"  [{site}] 🛡️  ถ้ากู้ไม่ได้ router จะ reboot กลับเองตอน "
              f"{recover_at.strftime('%H:%M:%S')}")

        print(f"  [{site}] สั่งทำให้ล่ม (ไม่ save — ตั้งใจให้ reboot แล้วหาย)")
        try:
            conn.send_config_set(config_lines, read_timeout=10)
        except Exception:
            # session ขาดตรงนี้เป็นเรื่องปกติ ถ้าคำสั่งคือการปิด interface ที่เราใช้อยู่
            print(f"  [{site}] session ขาดระหว่างสั่ง — เป็นพฤติกรรมที่คาดไว้แล้ว")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    return {
        'site': site,
        'armed_at': armed_at,
        'auto_recover_at': recover_at,
        'minutes': minutes,
    }


def repair_remote(site, config_lines, retries=3):
    """
    ซ่อม Access แล้วยกเลิกประกัน reload

    ถ้าเข้าไม่ถึงจริงๆ จะไม่ error — แจ้งว่าให้รอ router กู้ตัวเองแทน
    คืน True ถ้าซ่อมสำเร็จ, False ถ้าต้องรอ reload
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            conn = _connect(site)
        except Exception as exc:
            last_error = exc
            print(f"  [{site}] ต่อไม่ติด (ครั้งที่ {attempt}/{retries}) — รอ 10 วินาที")
            time.sleep(10)
            continue

        try:
            print(f"  [{site}] ต่อติดแล้ว กำลังซ่อม")
            conn.send_config_set(config_lines)
            disarm(conn)
            print(f"  [{site}] ✅ ซ่อมเสร็จ และยกเลิกประกัน reload แล้ว")
            return True
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass

    print(f"  [{site}] ⚠️  เข้าไม่ถึง ({last_error})")
    print(f"  [{site}]     ไม่ต้องทำอะไร — router จะ reboot กลับสภาพดีเองตามเวลาที่ตั้งไว้")
    print(f"  [{site}]     ถ้าจะเร่ง ให้เปิด Console ของ {site} ใน CML แล้วสั่ง 'reload' ได้เลย")
    return False


def status(site):
    """ดูว่ามีประกัน reload ค้างอยู่ไหม — ใช้ตรวจก่อนเลิกใช้งานแล็บทุกครั้ง"""
    conn = _connect(site)
    try:
        out = conn.send_command('show reload')
    finally:
        conn.disconnect()
    return out.strip()
