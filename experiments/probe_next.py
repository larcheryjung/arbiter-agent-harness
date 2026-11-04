"""Step-0 probe: does Gemini (Antigravity) read exit codes, follow NEXT:, and relay answers verbatim?

Usage (ask Gemini to run it and "follow what it says"):
    python probe_next.py ask
    python probe_next.py answer "<user's exact words>"
"""
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
LOG = HERE / "probe_answers.log"
ME = f'python "{Path(__file__).resolve()}"'


def ask():
    print("QUESTIONS (2):")
    print("  1. หน้าจอ Ops Tracking ควรแสดงสถานะล่าสุดกี่รายการ? [default: 20]")
    print("  2. ต้องมีปุ่ม export CSV ไหม? [default: ไม่มี]")
    print()
    print("STATUS: NEEDS_ANSWERS  (ไม่ใช่ error คำสั่งทำงานสำเร็จ)")
    print("NEXT: แสดงคำถามข้างบนให้ผู้ใช้ดูตามตัวอักษร ห้ามสรุปหรือแปล "
          "บอกผู้ใช้ว่ากำลังรอคำตอบ แล้วนำคำตอบของผู้ใช้มารันแบบไม่แก้ไขสักตัวอักษร:")
    print(f'      {ME} answer "<ข้อความของผู้ใช้>"')
    sys.exit(0)  # PowerShell -Command collapses non-zero codes to 1; status travels in STATUS:


def answer(text):
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')}\t{text}\n")
    print(f"RECEIVED (verbatim): {text}")
    print("STATUS: OK")
    print("NEXT: แจ้งผู้ใช้ว่าได้รับคำตอบแล้ว และแสดงบรรทัด RECEIVED ข้างบนตามตัวอักษร")
    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "ask":
        ask()
    elif len(sys.argv) >= 3 and sys.argv[1] == "answer":
        answer(" ".join(sys.argv[2:]))
    else:
        print(__doc__)
        sys.exit(64)
