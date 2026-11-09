# Arbiter: ภาพรวม (ร่าง v0.2)

> สถานะ: **ร่างที่รวมข้อตกลงจากการคุย ยังไม่ได้ทดสอบกับงานจริง** · 2026-11-04
> อ้างอิงงานวิจัย: [ai-agent-harness-research-notes.md](ai-agent-harness-research-notes.md)
> เปลี่ยนจาก v0: ให้ Gemini เรียก Claude อัตโนมัติเหมือนเดิม · ส่งแค่ตัวชี้ ไม่ส่งการตีความ · ตัด Full/migration ออกจาก v0 · กำหนดจังหวะที่ agent ถาม

---

## 1. สรุปในหนึ่งประโยค

**ใช้ AeroHarness ที่ทำงานเร็วเหมือนเดิม แต่ย้ายการตัดสินว่างานผ่านหรือไม่ จาก "AI เล่าเอง" ไปเป็น "script ตัดสินจาก exit code"** และคนยังกดอนุมัติแค่ 2 จุดเท่าเดิม

---

## 2. หลักแกน 7 ข้อ

1. **เครื่องเป็นคนตัดสิน ไม่ใช่ AI เล่า** ผลผ่านหรือไม่ผ่านมาจาก `evidence.json` และ exit code ไม่ได้มาจากข้อความที่ AI รายงาน
2. **คนกดแค่ Gate 1 และ Gate 2** ไม่มีการสลับ panel หรือส่งงานต่อด้วยมือ ช่วงระหว่างสอง gate ให้ทำงานอัตโนมัติทั้งหมด
3. **คนถูกตรวจห้ามเป็นคนเขียนโจทย์ให้คนตรวจ** Gemini ส่งให้ Claude แค่ตัวชี้ (`KS-xx` + โหมด) แล้ว Claude อ่านต้นทางเอง
4. **พื้นกันพังอยู่ใน git hook และ gate script** เพราะเป็นชั้นเดียวที่บังคับได้ทั้ง Gemini และ Claude
5. **harness ตัวเดียว แต่แยกท่าตามประเภทงาน** และโหลดเข้า context เฉพาะท่าที่ใช้อยู่
6. **ส่งต่องานผ่านไฟล์ และ reset context ทุกการ์ด** ไฟล์ส่งต่อต้องสั้นและชี้ไปที่ต้นทาง ไม่คัดลอกเนื้อหามา
7. **บทเรียนต้องย้อนกลับมาแก้ harness** สะดุดเมื่อไรให้จด `LESSONS.md` 1 บรรทัด ถ้าเจอซ้ำค่อยเพิ่ม sensor ส่วนกฎที่ไม่เคยจับอะไรได้ใน 10 งานให้ลบทิ้ง

---

## 3. บทบาท

| ใคร | ทำอะไร | ห้ามทำอะไร |
|---|---|---|
| **คุณก้อง** | Gate 1, Gate 2, ตัดสินเรื่องที่ถูกส่งขึ้นมา (escalation) | — |
| **Gemini** (Antigravity) | คุมงาน + เขียนโค้ด · เรียกคำสั่ง `arbiter` | ห้ามเขียน prompt ให้ Claude · ห้ามตัดสินเองว่างานผ่าน |
| **Claude** (`claude -p`) | ร่าง spec · review | review อ่านได้อย่างเดียว ไม่มีสิทธิ์แก้โค้ด |
| **`arbiter` script** | สร้าง prompt · รัน gate · อ่าน verdict · นับรอบ · สร้างหน้า Gate 2 | — |

Claude Code ติดตั้งเป็น extension ใน Antigravity IDE อยู่แล้ว ถ้าอยากรู้ว่า Claude คิดอะไรในการเรียกครั้งไหน ให้ `--resume <session_id>` จาก log ของการ์ดได้ ปกติไม่ต้องเปิดดู

---

## 4. โครง 3 ชั้น

```
ชั้น 1  Safety floor ── ใช้กับทุกงาน รวม spike ── เป็นโค้ด ไม่กิน context
        git pre-commit: แก้ไฟล์นอก scope, push, ความลับหลุด
        Claude hooks: Jira ต้องเป็น KS เท่านั้น · ห้ามเขียนลง HBS DB หรือ prod
ชั้น 2  Track ── โหลดเฉพาะท่าที่ใช้ (Antigravity workflow + Claude skill)
        /spike  /quick  /feature        (/greenfield ทำภายหลัง)
ชั้น 3  Pack ── โหลดเมื่อเกี่ยวข้อง
        jira-ks · spring-boot · docker-deploy · windows-ps · pch-network
```

---

## 5. Track

| Track | ใช้กับงาน | ขั้นตอน | คนกด |
|---|---|---|---|
| `/spike` | requirement ใหญ่ → แผน → การ์ด (2026-11-09 เปลี่ยนความหมาย ดู docs/spike-design.md) | คุยหลายรอบ → อนุมัติแผน → ซอยการ์ด (เครื่องตรวจกฎ) → อนุมัติการ์ด → Gemini สร้างใน KS · ไม่มีโค้ด | อนุมัติแผน + อนุมัติการ์ด |
| `/poc` | ความหมายเดิมของ `/spike`: ลองของเพื่อตอบคำถามเทคนิค (spike เสนอได้ ไม่บังคับ) | บน branch `spike/*` · **ห้าม merge** · ผลลัพธ์คือ note · ยังไม่ทำ | ไม่มี |
| `/quick` | ฟีเจอร์เล็ก, bug fix, config (≤2 ไฟล์) | spec สั้น → เทสต์ที่ fail ก่อน → แก้ → review | Gate 2 (ดู spec + diff พร้อมกัน) |
| `/feature` | ฟีเจอร์กลางถึงใหญ่ใน brownfield | ดูข้อ 6 · ถ้างานใหญ่ให้แบ่งเป็นหลาย phase | Gate 1 + Gate 2 |
| **Ops** | ไม่ใช่ track ให้เลือก แต่ทำงานอัตโนมัติเมื่อแตะระบบจริง (ssh, deploy, DB) | runbook → dry-run → **คนอนุมัติให้ลงมือจริง** → post-check → rollback | เพิ่มอีก 1 จุด |

- **ใครเลือก track:** ถ้าคุณพิมพ์คำสั่งเองก็ใช้ track นั้นเลย ถ้าเล่าโจทย์มาเฉยๆ AI จะเสนอ track พร้อมเหตุผล 1 บรรทัดแล้วให้คุณยืนยัน
- **ยกระดับกลางทาง:** ถ้าแก้ไฟล์นอก scope จน scope guard ทำงาน ให้หยุดแล้วเสนอยกระดับ AI ขยาย scope เองไม่ได้ ส่วนการลดระดับต้องให้คนสั่งเท่านั้น
- **ยังไม่ทำ:** `/greenfield` (รอจนมีโปรเจกต์ใหม่จริง) และ migration (ทำเมื่อมีงานจริง)

---

## 6. ลำดับงานของ `/feature`

```
คุณ: "ทำ KS-xx"
Gemini ─► arbiter spec KS-xx
            └ Claude: อ่านการ์ด + intent.md + โค้ด (recon) → spec.md
                      + คำถาม 0–5 ข้อ (ทุกข้อมีค่าที่จะใช้ถ้าไม่ตอบ) + สิ่งที่สันนิษฐานไว้
       ─► แสดง spec ─► 🚪 Gate 1 (คุณตอบคำถามและอนุมัติในครั้งเดียว)
Gemini ─► เขียนโค้ด ─► arbiter review KS-xx
            ├ gate: verify + lint + scope + เทสต์ AC ไม่ถูกแก้ → evidence.json
            ├ Claude (อ่านอย่างเดียว): spec + diff + evidence → review.md + verdict
            ├ exit 1 → Gemini แก้ → review อีกรอบ  (อัตโนมัติ)
            ├ exit 2 → ส่งให้คุณ (ครบ 2 รอบแล้วยังไม่ผ่าน หรือเจอเงื่อนไขหยุด)
            └ exit 0 → หน้า Gate 2 ที่ script สร้างจาก evidence + review
       ─► 🚪 Gate 2 ─► commit + ขยับ Jira + LESSONS 1 บรรทัด
```

**จังหวะที่ agent ถาม:** recon ก่อนถาม · ถามรวมรอบเดียวที่ Gate 1 · ระหว่างลงมือจะถามเฉพาะเมื่อเจอ**เงื่อนไขหยุด** ได้แก่ สิ่งที่เจอขัดกับ spec, ต้องแก้นอก scope, จะทำสิ่งที่ย้อนกลับไม่ได้, หรือเทสต์ AC ดูเหมือนผิด

**คำถามที่ช่วยได้มาก:** "ถ้าตอน Gate 2 คุณจะตีงานนี้กลับ น่าจะเป็นเพราะอะไร" คำตอบจะกลายเป็น AC ได้เลย

---

## 7. Claude อ่านอะไรเอง

| โหมด | อ่าน | ไม่อ่าน |
|---|---|---|
| spec | การ์ด Jira + epic หรือการ์ดที่ลิงก์ · `intent.md` (คำพูดของคุณตามจริง) · โฟลเดอร์การ์ดเดิม · `CURRENT_STATE` · โค้ด | แผนหรือวิธีทำที่ Gemini คิดไว้ |
| review | `spec.md` ที่ผ่าน Gate 1 · `git diff` · `evidence.json` · `review.md` ของรอบก่อน | รายงานของ Builder (ถ้าจะอ่าน ให้อ่านหลังตัดสินเองเสร็จแล้ว) |

ตั้งแต่รอบ review ที่ 2 ให้ตรวจเฉพาะ**ข้อเดิมว่าแก้แล้วหรือยัง และมีอะไรพังเพิ่มหรือไม่** ห้ามเปิดประเด็นใหม่ ยกเว้นเป็นเรื่องร้ายแรง

---

## 8. ไฟล์ในโปรเจกต์เป้าหมาย

```
<project>/.arbiter/
  CURRENT_STATE.md         # ≤30 บรรทัด เขียนทับ ไม่ต่อท้าย ชี้ไปที่ต้นทาง ห้ามใส่สถานะที่ตรวจซ้ำได้
  tasks/KS-xx/
    intent.md              # สิ่งที่คุณตัดสินใจหรือบอกไว้ ใช้คำพูดเดิม
    spec.md                # ≤1 หน้าจอ: goal, non-goals, AC, scope_allow, verify_cmd
    evidence.json          # สร้างโดย script เท่านั้น
    review.md              # Claude เขียน
    sessions.log           # session_id ของ claude -p แต่ละครั้ง
LESSONS.md
```

## 9. Repo ของ harness

```
arbiter-agent-harness/
  bin/arbiter              # spec | review | gate (สร้าง prompt, นับรอบ, ตัดสินด้วย exit code)
  tracks/                  # quick, feature, spike: Antigravity workflow + Claude skill
  floor/                   # git hooks + Claude hooks + settings.json (allowlist)
  packs/
  install/                 # ติดตั้งและอัปเดตโดยมีเลขเวอร์ชัน
```

---

## 10. ของเดิมจาก AeroHarness

| ทำอะไร | ของ |
|---|---|
| **เก็บไว้** | Gemini เรียก Claude อัตโนมัติ · `scope_allow` / `verify_cmd` / ห้ามแตะ · คนอนุมัติ 2 จุด · คนทำไม่ใช่คนตรวจ · reset ทุกการ์ด · ส่งให้คนตัดสินเมื่อแก้แล้วกระทบ business |
| **เปลี่ยนจากกฎเป็นโค้ด** | "รันจริงเท่านั้น" → `evidence.json` · scope → pre-commit · Jira KS → hook · `--dangerously-skip-permissions` → allowlist |
| **เปลี่ยนวิธี** | `architect-prompt.txt` ที่ Gemini เขียนเอง → script สร้างจากตัวชี้ · `TASK.md` + `implementation_plan.md` → `spec.md` ใบเดียว · `CURRENT_STATE` 124 บรรทัด → ≤30 |
| **ย้ายไปเป็น pack** | Jira governance, Fortinet cert, การจัดการ `.env`, Windows gotchas |
| **ตัดทิ้ง** | 10 ขั้นเท่ากันทุกการ์ด · Visual Snapshot ทุกครั้ง · การ sync upstream ด้วยมือ · `builder-to-architect.md` ในฐานะข้อมูลให้ reviewer |

---

## 11. ขอบเขต v0 และเกณฑ์ยุติ

**ทำใน v0:** floor + `/quick` + `/feature` + `/spike` + Ops + pack `jira-ks`
**ยังไม่ทำ:** `/greenfield`, migration, ให้ Claude เป็นคนคุมงาน, ชุดทดสอบพฤติกรรมโมเดล, dashboard

**ลำดับที่จะทำ**
1. **ขั้น 0:** ทดสอบสิ่งที่ยังไม่รู้ (ข้อ 12) บนเครื่องจริง แล้วเดินการ์ดจริง 1 ใบด้วยมือตามข้อ 6
2. เขียน `arbiter review` + pre-commit + hook Jira KS (ทุกตัวต้องเคยจับปัญหาจริงได้อย่างน้อย 1 ครั้ง)
3. ใช้กับการ์ดจริง 10 ใบ จด `LESSONS.md` แต่ยังไม่เพิ่มกฎ
4. เพิ่มเฉพาะเรื่องที่เจอซ้ำ

**งบขนาด:** กฎที่โหลดทุก turn ≤80 บรรทัด · template ≤1 หน้าจอ (ไม่จำกัดขนาดโค้ด: คุณก้องตัดสิน 2026-11-09)
**ตัวชี้วัด:** เวลาที่คุณใช้ต่อ gate · จำนวนรอบที่ต้องแก้ · ปัญหาที่คุณเจอตอน Gate 2 แต่ AI ไม่เจอ · bug ที่หลุดไปหลัง Done
**เกณฑ์ยุติ:** ถ้าครบ 10 การ์ดแล้วไม่ดีกว่า AeroHarness ให้นำแค่ `arbiter review` กับ hook กลับไปเสริม AeroHarness แทน

---

## 11.1 สถานะ v0 (2026-11-04)

สร้างเสร็จและทดสอบแล้ว: `bin/arbiter.py` (spec / answer / approve / review / commit / status), `floor/git/pre-commit`, prompt 4 ไฟล์, workflow ของ Gemini 14 บรรทัด, `install.py` · ชุดทดสอบ 19 ข้อ · e2e กับ Claude จริง 2 รอบ

**ข้อจำกัดที่รู้แล้ว ยังไม่ได้แก้** (จาก independent review ข้อ 1)
- ไฟล์ใน `.arbiter/` (approvals, evidence, state, config) ถูก agent แก้ได้ ส่วน `commit` ตรวจ spec, scope และ tree ซ้ำเอง แต่ยังเชื่อผล verify/review ที่อยู่ใน `evidence.json`
- agent รัน `approve` หรือ `--continue` แทนคนได้ ตอนนี้ตรวจย้อนหลังได้จาก transcript ของ Antigravity เท่านั้น
- `git commit --no-verify` และ `-c core.hooksPath=` ข้าม hook ได้ ต้องใช้ CI ตรวจ trailer `Arbiter-Tree` จึงจะปิดได้
- แนวทางแก้รอบถัดไป: เก็บไฟล์หลักฐานตัวจริงไว้ใน `.git/arbiter/` · ให้ approve ต้องยืนยันด้วยรหัสที่แสดงให้คนเห็นเท่านั้น · ตรวจ transcript อัตโนมัติ

## 11.2 v0.2.0 (2026-11-06, หลังการ์ดจริงใบแรก KS-118)

- **การ์ด `LOCAL-n`**: `/feature <โจทย์>` เป็นงานที่ไม่มีการ์ด Jira arbiter ตั้งรหัสเอง เก็บข้อความผู้ใช้ตามตัวอักษรใน `intent.md` Claude ไม่มีสิทธิ์เรียก Jira เลย และไม่ต้องขยับการ์ดหลัง commit
- **บรรทัด `▶ สิ่งที่คุณต้องทำ`** อยู่ในทุกหน้าที่ต้องรอคน (คำถาม, Gate 1, Gate 2, STALE, ESCALATE, ERROR, BLOCKED, COMMITTED)
- **STALE แสดงชื่อไฟล์** และ workflow ห้าม Gemini ใช้ checkout/restore/reset/stash/clean หรือย้อนไฟล์ที่การ์ดไม่ได้แก้
- **`docs_allow`** ใน config: commit ที่มีแต่เอกสารผ่าน hook ได้โดยไม่ต้องมีการ์ด (ยังสแกน secret) · ระหว่างทำการ์ด เอกสารที่เปลี่ยนไม่นับเป็น scope ไม่ทำให้ STALE และไม่ถูกรวมใน commit ของการ์ด · `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` ไม่นับเป็นเอกสารเสมอ
- **config ใช้ฉบับที่ commit แล้วเท่านั้น**: arbiter หยุดทำงานถ้า `config.json` ในเครื่องไม่ตรงกับ HEAD และ hook อ่าน `docs_allow` จาก HEAD (กัน agent ขยาย `docs_allow` หรือแก้ verify เองในเครื่อง)
- **hook ต้องเห็นการอนุมัติ Gate 2** ของ tree นั้นใน `approvals.json` ไม่เชื่อ env `ARBITER_COMMIT_TREE` อย่างเดียว (ยังเป็นไฟล์ที่ agent เขียนได้ จึงแค่บังคับให้ต้องปลอมการอนุมัติของคน ซึ่งเห็นได้ใน transcript)
- **ภาษา**: Claude เขียน spec/คำถาม/review ตามภาษาของข้อความล่าสุดที่ผู้ใช้พิมพ์ในการ์ด ไม่มีให้ใช้ `language` ใน config (ค่าตั้งต้น `th`)

## 12. สิ่งที่ต้องทดสอบบนเครื่องจริงก่อน (ขั้น 0)

- [x] `claude -p --allowedTools "Read"` แล้วสั่งให้ Write **ไม่ค้าง** ถูกปฏิเสธใน 12 วินาที ได้ exit 0 และมีรายการใน `permission_denials` ของ JSON ให้ script อ่านได้ (2026-11-04, claude 2.1.281)
- [x] `claude -p --resume <id>` **จำบทสนทนาเดิมได้** (ทดสอบด้วย code word แล้วตอบถูก, session_id เดิม)
- [x] `claude -p` เรียก Jira MCP อ่าน KS-58 ได้ และ `~/.claude/hooks/jira-guard.py` บันทึก log ว่า allow
- [x] Gemini ใน Antigravity ทำตาม `STATUS:`/`NEXT:` ได้ แสดงคำถามตามตัวอักษรเดิม และ**ส่งคำตอบต่อตรงทุกตัวอักษร** (เทียบข้อความใน transcript กับ argv แล้ว, 2026-11-04) · แต่ Gemini ชอบเปิดอ่าน source ของ script ก่อนรัน
- ข้อมูลที่ได้เพิ่ม: Antigravity เก็บ transcript ไว้ที่ `~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` จึงใช้ตรวจย้อนหลังแบบอัตโนมัติได้ว่า Gemini ส่งข้อความของผู้ใช้ต่อตรงหรือไม่ · มี `~/.gemini/antigravity/bin/agentapi.bat` ซึ่งยังไม่ได้ตรวจ
- บทเรียน (ข้อ 11 รอบแรก): Antigravity รันคำสั่งผ่าน `powershell -Command` ซึ่ง**ทำให้ exit code ที่ไม่ใช่ 0 กลายเป็น 1 ทั้งหมด** (ทดสอบแล้ว: 3 กลายเป็น 1) Gemini จึงเข้าใจว่าพังและไปเปิด source → **สื่อสารกับ Gemini ด้วยบรรทัด `STATUS:` และให้สถานะที่ไม่ใช่ error เป็น exit 0** ส่วน exit 0/1 ใช้กับ hook และ script
- บทเรียน: Python บน Windows พิมพ์ภาษาไทยไม่ได้ถ้าไม่ตั้ง `PYTHONIOENCODING=utf-8` หรือ `sys.stdout.reconfigure(encoding="utf-8")`

- [x] (2026-11-09) `claude -p --add-dir <repo อื่น>` อ่านไฟล์นอก repo ได้ ไม่ค้าง · แต่ไม่ใส่ `--add-dir` ก็อ่านได้เหมือนกัน (Read ที่อยู่ใน `--allowedTools` อ่านได้ทั้งเครื่อง) ดู LESSONS

## 13. คำถามที่ยังค้าง

**ตัดสินแล้ว (2026-11-04)**
- ภาษา: **Python 3.12** (มีบนเครื่องแล้ว และ jira-guard ก็เขียนด้วย Python)
- Pilot: **`hbs3/epolicy`** และจะลบ harness ตัวอื่นออกจาก repo นี้
- Jira: **Gemini ขยับการ์ดเหมือนเดิม** กฎ lifecycle อยู่ที่ kong-command-center · ส่วนความปลอดภัยของ Jira มีครบแล้ว 3 ชั้น (credential เห็นแค่ KS · `mcp-atlassian --jira-projects-filter KS` · `~/.claude/hooks/jira-guard.py`)
- Deploy/prod: คุณรัน script เอง ไม่ต้องเพิ่มชั้นกัน
- โฟลเดอร์การ์ด: **`.arbiter/tasks/KS-xx/` แยกจากของเดิม** ส่วนประวัติเก่าใน `kong-workspace/tasks/` เก็บไว้ตามเดิม
- การสลับ: ลบ Triumvirate และ workflow ที่ค้างได้ทันที ส่วน `temp-context/` กับ `aeroharness/` ให้ลบใน commit เดียวกับที่ติดตั้ง v0
- ชื่อ: **Arbiter** (ผู้ชี้ขาด) คำสั่ง `arbiter`, โฟลเดอร์ `.arbiter/`, env `ARBITER_*`, trailer `Arbiter-*`
- `.agents/rules/jira-safety.md`: เก็บไว้จนกว่าจะมี pack `jira-ks` ระดับผู้ใช้ · ฝั่ง Gemini ควรเพิ่ม `--jira-projects-filter KS` ใน `~/.gemini/antigravity/mcp_config.json`

**ยังค้าง**
1. ใครเขียน AC: คุณเขียนเอง หรือ Claude ร่างแล้วคุณอนุมัติที่ Gate 1
2. จะให้คนอื่นในทีมใช้ด้วยหรือไม่
