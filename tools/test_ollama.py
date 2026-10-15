"""
Phase 3.5.3 ขั้นที่ 1 - ทดสอบว่าเครื่องรัน Ollama ไหวไหม

วิธีรัน:
    py -m pip install requests
    py test_ollama.py

⚠️ ก่อนรัน: ปิด CML ใน VMware ก่อนเสมอ
   (RAM ไม่พอรันพร้อมกัน - ดู Plan.md 3.5.2)
"""

import time
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.1:8b"  # ถ้าช้าเกิน 30 วิ ให้เปลี่ยนเป็น "llama3.2:3b" หรือ "phi3:mini"

TEST_PROMPT = (
    "A Cisco router interface went down with syslog message: "
    "'%LINK-3-UPDOWN: Interface Ethernet0/1, changed state to down'. "
    "In one short sentence, what could be a possible cause?"
)


def main():
    print(f"Testing model: {MODEL}")
    print("Sending test prompt...\n")

    start = time.time()
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": TEST_PROMPT, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        elapsed = time.time() - start
        data = resp.json()

        print("Response:")
        print(data.get("response", "(no response field)"))
        print(f"\nTime taken: {elapsed:.2f} seconds")

        if elapsed > 30:
            print("\n⚠️  เกิน 30 วินาที - ลองโมเดลเล็กลง (ดู Plan.md 3.5.3 ขั้นที่ 1)")
        else:
            print("\n✅ ไม่เกิน 30 วินาที - ใช้โมเดลนี้ต่อได้")

    except requests.exceptions.ConnectionError:
        print("Connection failed - Ollama รันอยู่ไหม? ลอง: ollama serve")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
