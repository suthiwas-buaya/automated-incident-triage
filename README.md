# Automated Incident Triage

ระบบตรวจจับ fault บนเครือข่ายอัตโนมัติ → สร้าง Trouble Ticket → จำแนกว่าทีมไหนควรรับผิดชอบ → วินิจฉัยสาเหตุ
โปรเจกต์วิชา ENE372/473 · มจธ. (KMUTT)

## ระบบทำอะไร

```
Router (Cisco IOL ใน CML) ──syslog UDP 514──▶ syslog_server.py ──▶ tickets.db (SQLite)
                                                 │ จำแนกทีม + ความด่วน + รวมเหตุที่เกี่ยวกัน
                                                 ▼
                              diagnose_worker.py ── SSH เข้า Router รัน show command → สรุปสาเหตุด้วยกฎ
                              analyze_pending.py ── เคสที่กฎสรุปไม่ได้ ส่งให้ LLM (Ollama) ช่วยวิเคราะห์
                                                 ▼
                              dashboard/ (Flask + vanilla JS) ── เจ้าหน้าที่ยืนยัน/แก้ทีมที่ระบบจำแนก
```

- **Ticket เปิด/ปิดเอง** จาก syslog `%LINK-*` (`down` → เปิด, `up` → ปิด)
- **จำแนก Owner Group + Priority** ด้วยกฎ keyword (word boundary) และยกระดับตามผลกระทบ (Site หลุดทั้ง Site → ≥ P2)
- **วินิจฉัยสาเหตุ** แบบ Hybrid: กฎก่อน แล้วค่อยใช้ LLM เฉพาะเคสที่กฎไม่ครอบคลุม
- **Human-in-the-loop**: ระบบเสนอทีม แต่คนต้องกดยืนยันบน Dashboard

## โครงสร้าง

| ส่วน | ไฟล์ |
| :-- | :-- |
| ตัวหลัก | `syslog_server.py`, `diagnose_worker.py`, `analyze_pending.py` |
| จำลอง fault | `fault_injection.py`, `inject_menu.py`, `c06_congestion.py` |
| ไลบรารี | `lib/` — parser, classifier, correlation, diagnostics, cause rules, LLM client ฯลฯ |
| Dashboard | `dashboard/backend/app.py` (Flask API), `dashboard/frontend/` |
| ทดสอบ/วัดผล | `measure_latency.py`, `run_*_batch.py`, `test_dispositions.py`, `replay_syslog.py`, `testcases/`, `tools/` |
| ปุ่มลัด Windows | `0_` – `8_*.bat` |

## ติดตั้ง

ต้องมี Python 3 (บน Windows ใช้คำสั่ง `py`)

```bat
py -m pip install -r requirements.txt
copy lib\lab_secrets.example.py lib\lab_secrets.py
```

แล้วแก้ `lib\lab_secrets.py` ใส่ username/password ของ Router ใน lab
ที่อยู่ IP / port ของอุปกรณ์ตั้งใน `lib/mapping.py` (`DEVICE_SSH`) และ `fault_injection.py` (`CORE`)

ถ้าจะใช้ส่วนวิเคราะห์ด้วย LLM ต้องติดตั้ง [Ollama](https://ollama.com) และโหลดโมเดล `llama3.1:8b`

## วิธีรัน

เปิดหลายหน้าต่างพร้อมกัน (หรือดับเบิลคลิก `.bat` ตามเลข):

| หน้าต่าง | คำสั่ง | |
| :-- | :-- | :-- |
| 1 | `py syslog_server.py` | ฟัง syslog — ค้างไว้ |
| 2 | `py diagnose_worker.py` | วินิจฉัยผ่าน SSH — ค้างไว้ |
| 3 | `py dashboard\backend\app.py` | API ของ Dashboard แล้วเปิด `dashboard\frontend\index.html` |
| 4 | `py inject_menu.py` | ยิง fault ทดสอบ |

ทดสอบส่วน logic โดยไม่ต้องเปิด lab: `py lib\<ชื่อไฟล์>.py` (ทุกไฟล์ใน `lib/` มี unit test ด้วยข้อมูลจำลอง)

## Lab

Topology แบบ Star บน Cisco Modeling Labs (CML): Core 1 ตัว + Access Site A/B/C ต่อเข้า Core คนละเส้น
fault ต้องยิงที่ Core เท่านั้น เพราะ IOL ไม่ส่งต่อการขาดของสัญญาณไปอีกฝั่ง
