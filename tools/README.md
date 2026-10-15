# tools/ — เครื่องมือตรวจสอบที่รันเป็นครั้งคราว

ไฟล์ในนี้ **ไม่ได้อยู่ในเส้นทางการทำงานหลัก** ของระบบ — ไม่มี `.bat` ตัวไหนเรียก
และไม่มีไฟล์อื่น `import` เข้าไป จึงย้ายลงมาได้โดยระบบหลักไม่กระทบ

## วิธีรัน

รันจาก **โฟลเดอร์ราก** ของโปรเจกต์เสมอ:

```
py tools\check_dashboard.py
```

ไฟล์ที่ต้องใช้ `lib/` มีโค้ด 3 บรรทัดหัวไฟล์ (`_sys.path.insert`) ที่ชี้กลับไปโฟลเดอร์แม่ให้แล้ว
ส่วน `check_dashboard.py` กับ `review_worksheet.py` แก้ `PROJECT_ROOT` ให้ขึ้นไป 1 ชั้น
เพื่อให้ยังหา `tickets.db` ที่รากเจอเหมือนเดิม

## รายการไฟล์

| ไฟล์ | Phase | หน้าที่ |
| :-- | :-- | :-- |
| `check_dashboard.py` | 4 | วินิจฉัย "ทำไม Ticket ไม่โผล่บน Dashboard" |
| `review_worksheet.py` | 6 | ใบงานไล่ยืนยันการจำแนกกลุ่ม (เกณฑ์ข้อ 3) |
| `test_ollama.py` | 3.5 | เช็คว่าเครื่องรัน Ollama ไหวไหม |
| `test_analyzer_live.py` | 3.5 | ทดสอบกลไกกันตอบมั่วกับโมเดลจริง |
| `compare_approaches.py` | 3.5 | เทียบ Rule only / LLM only / Hybrid |
| `probe_feasibility.py` | 5 | สำรวจว่า C06 / C08 ทำบน lab นี้ได้ไหม |
| `probe_counters.py` | 5 | IOL มีตัวนับ duplex/collisions ไหม |
| `test_holdqueue.py` | 5 | IOL ยอมเก็บ `hold-queue` ลง running-config ไหม |

ดูภาพรวมทุกไฟล์แยกตาม Phase ที่ [`docs/plan/FILES_BY_PHASE.md`](../docs/plan/FILES_BY_PHASE.md)
