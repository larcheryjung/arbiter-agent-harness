# Arbiter

Agent harness ของคุณก้อง (ตัวที่ 5) ใช้แทน AeroHarness: Gemini (Antigravity) เป็นคนลงมือและเรียก Claude อัตโนมัติ, Claude (`claude -p` อ่านอย่างเดียว) ร่าง spec และ review, script `arbiter` ตัดสินผ่าน/ไม่ผ่านจากหลักฐาน, คนกดแค่ Gate 1 และ Gate 2

- คู่มือฉบับเต็มสำหรับคน: [docs/index.html](docs/index.html) (สถานะ, วิธีใช้, ทำงานอย่างไร, Roadmap, บทเรียน, อ้างอิง)
- ทิศทางและการตัดสินใจ: [DIRECTION.md](DIRECTION.md) · บทเรียนจากงานจริง: [LESSONS.md](LESSONS.md)

## สถานะ (2026-11-06)

- **v0.2.0** `af02f35` · เทสต์ 32 ข้อ: `python -m unittest discover -s tests`
- Pilot: `C:\local-code-repo\hbs3\epolicy` (branch `dev`) ติดตั้ง v0.2.0 แล้ว · การ์ดจริง 1/10 (KS-118 → `e3a4e5a`, ค่า Claude $6.24)
- มีแล้ว: `/feature KS-xx`, `/feature <โจทย์>` (การ์ด `LOCAL-n` ไม่มี Jira), git pre-commit (scope, secret, Gate 1/2, `docs_allow`), config ใช้ฉบับที่ commit เท่านั้น
- ยังไม่มี: `/spike`, `/quick`, Ops, pre-push, hook กัน HBS DB/prod, pack `jira-ks`

## Roadmap ย่อ

1. **ตอนนี้:** ใช้ `/feature` กับการ์ดจริงให้ครบ 10 ใบ retro ทุกใบลง LESSONS.md
2. **ทำเมื่อมีตัวกระตุ้น:** `/spike` (มีงาน POC จริง; ตอนนี้ hook บล็อก commit ทุก branch) · `/quick` (ถ้า /feature หนักเกินกับงานเล็ก) · Ops (มีการ์ด deploy/DB)
3. **ภายหลัง:** แยก `arbiter.py` (531 บรรทัด เกินงบ 300) · `pip install -e` + shim hook · ย้ายหลักฐานไป `.git/arbiter/` (finding 1) · CI ตรวจ trailer · pack `jira-ks`
4. **เกณฑ์ยุติ:** ครบ 10 การ์ดแล้วไม่ดีกว่า AeroHarness → เอาแค่ `arbiter review` + hook กลับไปเสริม AeroHarness

## โครง repo

```
bin/arbiter.py, arbiter_core.py   CLI: spec [--local] | answer | approve | review [--continue] | commit | status
floor/git/pre_commit.py           hook (โปรเจกต์ตั้ง core.hooksPath มาที่ floor/git)
prompts/                          template ที่ arbiter สร้าง prompt ให้ Claude (Gemini เขียน prompt เองไม่ได้)
adapters/antigravity/workflows/   feature.md = /feature ของ Gemini (install คัดลอกและฝัง path)
install.py                        ติดตั้ง/อัปเดต idempotent: --verify "name=cmd" --docs-allow "glob"
tests/                            e2e บน git repo ชั่วคราว + fake Claude (stream-json)
```

## Retro การ์ด

อ่าน transcript ของ Gemini ที่ `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript_full.jsonl` (ห้ามอ่านไฟล์ credential ใน `~/.gemini`) คู่กับ `<project>/.arbiter/tasks/<KEY>/` (spec, answers, approvals, evidence, review, sessions.jsonl) แล้วจด 1 บรรทัดต่อเรื่องใน LESSONS.md

## กติกาเวลาแก้ arbiter

- งานแก้ arbiter ต้องผ่าน Gate 1 (อนุมัติแผน) และ Gate 2 (อนุมัติผลเทสต์จริง) ของคุณก้องเหมือนงานอื่น
- epolicy เรียก `bin/arbiter.py` จากโฟลเดอร์นี้โดยตรง แก้ไฟล์แล้วมีผลทันที อย่าแก้ระหว่างที่การ์ดกำลังรัน
- เปลี่ยน workflow/config ต้องรัน `install.py` ใหม่ แล้วให้คน commit `config.json` ด้วย `--no-verify`
