# Arbiter

Agent harness ของคุณก้อง (ตัวที่ 5) ใช้แทน AeroHarness: Gemini (Antigravity) เป็นคนลงมือและเรียก Claude อัตโนมัติ, Claude (`claude -p` อ่านอย่างเดียว) ร่าง spec และ review, script `arbiter` ตัดสินผ่าน/ไม่ผ่านจากหลักฐาน, คนกดแค่ Gate 1 และ Gate 2

- คู่มือสอนใช้งานฉบับเต็ม (หลายหน้า): [docs/index.html](docs/index.html) → แนวคิด, /feature, /spike, เมื่องานติด, อะไรบังคับจริง, กรณีศึกษา, อ้างอิง, [สถานะและ Roadmap](docs/roadmap.html)
- ทิศทางและการตัดสินใจ: [DIRECTION.md](DIRECTION.md) · บทเรียนจากงานจริง: [LESSONS.md](LESSONS.md) · แบบ `/spike` และ `/poc`: [docs/spike-design.md](docs/spike-design.md)

## สถานะ (2026-11-10)

- **v0.3.5** · เทสต์ 59 ข้อ: `python -m unittest discover -s tests`
- Pilot `/feature`: `C:\local-code-repo\hbs3\epolicy` (branch `dev`) ติดตั้ง 0.3.4 · KS-118 → `e3a4e5a` (ค่า Claude $6.24)
- Pilot `/spike`: `C:\local-code-repo\hbs3\hbs-strangler-service` (ติดตั้ง 0.3.4) · SPIKE-1 จบแล้ว → KS-160, KS-166 (Policy Status API spec ให้เว็บ TA) · KS-160 → `4cb0dc3` (การ์ดจริง 2/10)
- Pilot ที่สาม: `C:\local-code-repo\hbs-legacy\hbs-web-core` (branch `develop`, ติดตั้ง 0.3.5 เมื่อ 2026-11-10) · verify = Ant build · repo แรกที่มีหลาย branch (รายละเอียดใน docs/roadmap.html)
- มีแล้ว: `/feature KS-xx`, `/feature <โจทย์>` (การ์ด `LOCAL-n`), `/spike KS-xx` หรือ `/spike <requirement + ไฟล์>` (แผน → การ์ด → Jira), git pre-commit (scope, secret, Gate 1/2, `docs_allow`), config ใช้ฉบับที่ commit เท่านั้น, ดึงการ์ด KS ขึ้นบอร์ดเอง (`spike link`, `spec KS-xx`), ปลด deadlock (`decisions.md`, `SPEC_ISSUE`, `dispute`, `park`), สั่ง model เองได้ (`haiku`/`sonnet`/`opus`/`fable` หรือชื่อรุ่นเต็มเช่น `claude-fable-5-1`; `fable` = alias ของ CLI ตอนนี้คือ `claude-fable-5-2` ไม่เป็นค่าตั้งต้น; รุ่นจริงบันทึกเป็น `model_used` ใน `sessions.jsonl`)
- ยังไม่มี: `/poc`, `/quick`, Ops, pre-push, hook กัน HBS DB/prod, pack `jira-ks`

## Roadmap ย่อ

1. **ตอนนี้:** ใช้ `/feature` กับการ์ดจริงให้ครบ 10 ใบ (การ์ดจาก spike ใบแรกนับรวมได้) · เดิน `/spike` ใบแรก · retro ทุกใบลง LESSONS.md
2. **ทำเมื่อมีตัวกระตุ้น:** `/poc` (spike เสนอการ์ด POC จริง) · `/quick` (ถ้า /feature หนักเกินกับงานเล็ก) · Ops (มีการ์ด deploy/DB) · ใช้ `model/effort` ของการ์ดจาก spike เป็นค่าตั้งต้นของ /feature
3. **ภายหลัง:** `pip install -e` + shim hook · ย้ายหลักฐานไป `.git/arbiter/` (finding 1) · CI ตรวจ trailer · pack `jira-ks`
4. **เกณฑ์ยุติ:** ครบ 10 การ์ดแล้วไม่ดีกว่า AeroHarness → เอาแค่ `arbiter review` + hook กลับไปเสริม AeroHarness

## ติดตั้งในโปรเจกต์ใหม่

ระดับเครื่องมีอยู่แล้ว (ไม่ต้องทำต่อโปรเจกต์): Python 3.12, `claude` CLI, Jira MCP ที่เห็นแค่ KS, `~/.claude/hooks/jira-guard.py`

1. (AI) `python C:/local-code-repo/arbiter-agent-harness/install.py --project <repo> --verify "unit=<คำสั่งเทสต์>" --docs-allow "kong-workspace/**" --docs-allow "*.md" [--read-dir <repo อื่นที่ /spike อ่าน>]`
   สร้าง `.arbiter/config.json`, `.arbiter/.gitignore`, `.arbiter/tasks/` · ตั้ง `core.hooksPath` → `floor/git` · เขียน `.agent/workflows/feature.md` และ `spike.md`
   ยังไม่มีคำสั่งเทสต์ก็ข้าม `--verify` ได้ (/spike ไม่ใช้ · การ์ด /feature ใบแรกใส่ verify ใน spec ได้)
2. (AI) ลบ harness เก่า (`.agents/skills/aeroharness`, `kong-workspace/temp-context`, workflow เก่าใน `.agent/workflows/`) และเขียน `AGENTS.md` / `CLAUDE.md` ใหม่ให้เหลือความรู้ของโปรเจกต์ (ทำใน commit เดียวกัน เพราะหลังติดตั้ง hook ถือว่าไฟล์เหล่านี้เป็นโค้ด)
3. (คน) ตรวจ `.arbiter/config.json` แล้ว `git add -A && git commit --no-verify -m "chore: install arbiter <version>"`

## โครง repo

```
bin/arbiter.py           CLI /feature: spec [--local] | answer | approve | review [--continue] | dispute | park | unpark | commit | status
bin/arbiter_spike.py     CLI /spike (arbiter.py spike ...): new | answer | approve | cards | link | status
bin/arbiter_claude.py    เรียก claude -p (อ่านอย่างเดียว), template, stream-json, ภาษา
bin/arbiter_core.py      git, scope, config จาก HEAD, say() STATUS/NEXT
bin/arbiter_extract.py   แปลง .docx/.xlsx ที่แนบมากับ requirement เป็นข้อความ
bin/arbiter_board.py     ดึงการ์ด KS ออกจาก Backlog ขึ้นบอร์ด (Jira Agile API, ไม่บล็อกงาน)
floor/git/pre_commit.py  hook (โปรเจกต์ตั้ง core.hooksPath มาที่ floor/git)
prompts/                 template ที่ arbiter สร้าง prompt ให้ Claude (Gemini เขียน prompt เองไม่ได้)
adapters/antigravity/workflows/   feature.md, spike.md ของ Gemini (install คัดลอกและฝัง path)
install.py               ติดตั้ง/อัปเดต idempotent: --verify --docs-allow --read-dir
tests/                   e2e บน git repo ชั่วคราว + fake Claude (stream-json)
```

## Retro การ์ด / spike

อ่าน transcript ของ Gemini ที่ `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript_full.jsonl` (ห้ามอ่านไฟล์ credential ใน `~/.gemini`) คู่กับ `<project>/.arbiter/tasks/<KEY>/` (spec, answers, approvals, evidence, review, sessions.jsonl) หรือ `.arbiter/spikes/<KEY>/` (intent, inputs, answers, replies, solution, cards, cards-errors, approvals, jira-map, sessions.jsonl) แล้วจด 1 บรรทัดต่อเรื่องใน LESSONS.md

## กติกาเวลาแก้ arbiter

- งานแก้ arbiter ต้องผ่าน Gate 1 (อนุมัติแผน) และ Gate 2 (อนุมัติผลเทสต์จริง) ของคุณก้องเหมือนงานอื่น
- epolicy เรียก `bin/arbiter.py` จากโฟลเดอร์นี้โดยตรง แก้ไฟล์แล้วมีผลทันที อย่าแก้ระหว่างที่การ์ดกำลังรัน
- เปลี่ยน workflow/config ต้องรัน `install.py` ใหม่ แล้วให้คน commit `config.json` ด้วย `--no-verify`
- ส่งโค้ด Python ผ่าน Bash heredoc: `\\n` ถูกแปลงเป็นขึ้นบรรทัดจริงก่อนถึง Python ให้ใช้ Edit แทนเมื่อมี escape
