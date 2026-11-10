# `/spike` — แบบร่างสำหรับ Gate 1 (ร่าง 2026-11-06)

> สถานะ: ผ่าน Gate 1 (2026-11-09) · **ระยะ A ทำแล้วใน v0.3.0** · ระยะ B (`/poc`) ยังไม่ทำ
> ต่างจากแบบ: ตรวจ path จริงเฉพาะ `scope_hint` ของการ์ด (ไม่ตรวจ path ใน solution.md เพราะเป็นข้อความอิสระ) · เพิ่ม `replies.md`, `cards-errors.md` ไว้ทำ retro · คำสั่ง `cards` สั่งซอยการ์ดซ้ำ
> ที่มา: flow ที่คุณก้องร่างไว้ (requirement → ถก → solution 🚪 → POC → การ์ด 🚪 → Jira → /feature)

## 0. เปลี่ยนความหมายของ `/spike`

DIRECTION.md §5 เคยกำหนดให้ `/spike` คือ "POC ไม่มี gate ผลลัพธ์เป็น note" แบบนี้แยกเป็น 2 track
- **`/spike`** = กลั่น requirement ใหญ่จนได้ solution และรายการการ์ด (ไม่มีโค้ด)
- **`/poc`** = ความหมายเดิมของ `/spike` ใน DIRECTION: ลองของบน `spike/*` ห้าม merge ผลลัพธ์เป็น note เปิดเป็นการ์ด POC ได้ทั้งจาก spike และเปิดเองโดยตรง
งาน "หาสาเหตุ bug" ไม่อยู่ในแบบนี้ (ถ้าต้องการค่อยทำเป็น track แยก)

## 1. Flow

```
คุณ: /spike KS-200   หรือ   /spike <requirement ตามตัวอักษร / ไฟล์แนบ>
Gemini ─► arbiter spike new ...                          (ไม่แตะโค้ด รันพร้อมการ์ด /feature ได้)
  ┌─► Claude (อ่านอย่างเดียว): requirement + โค้ดจริง + Jira KS ที่เกี่ยวข้อง
  │     → solution.md ส่วน "ความเข้าใจ" + REPLY + คำถาม (มี default) + ข้อแย้ง
  └── คุณตอบ / ถาม / แย้ง ─► arbiter spike answer   (วนกี่รอบก็ได้ ทุกหน้าแสดงรอบและค่า Claude สะสม)
      เมื่อไม่มีคำถามที่ต้องตอบแล้ว Claude เติมทางเลือก 2–3 แบบ + ที่แนะนำ
      ถ้ายังเลือกไม่ได้เพราะติดคำถามเชิงเทคนิค Claude เสนอการ์ด POC (คำถาม + เกณฑ์ผ่าน) แทน
        → คุณอนุมัติ → Gemini สร้างการ์ด POC ใน KS → spike หยุดรอ (WAITING_POC)
        → การ์ด POC เดินใน /poc จนได้ note → arbiter spike resume <ID> → Claude อ่าน note เองแล้วถกต่อ
  🚪 อนุมัติแผน: อนุมัติ solution
  Claude แตกการ์ดอัตโนมัติ → cards.json → arbiter ตรวจด้วยกฎ (ข้อ 4) → ไม่ผ่านให้ Claude แก้ 1 รอบอัตโนมัติ
  🚪 อนุมัติการ์ด: อนุมัติรายการการ์ด
Gemini ─► สร้างการ์ดใน KS ตาม cards.json ทีละฟิลด์ ─► arbiter spike link <ID> "C1=KS-201 ..."
arbiter ─► commit โฟลเดอร์ spike (ไม่มีโค้ด hook ผ่านเอง) ─► การ์ดแต่ละใบเข้า /feature KS-xx
```

คนกดแค่ 2 จุด: **อนุมัติแผน** และ **อนุมัติการ์ด** ส่วนการตอบคำถามระหว่างถกเป็นการคุยกัน ไม่นับเป็น gate

## 2. หลักเดิมที่ยังใช้ทั้งหมด

- Gemini ส่งแค่ตัวชี้ (`KS-200`) หรือข้อความของคุณตามตัวอักษร ส่วน prompt ของ Claude arbiter สร้างจาก template
- `STATUS:`/`NEXT:` + บรรทัด `▶ สิ่งที่คุณต้องทำ` ทุกหน้า · Gemini แสดงตามตัวอักษรก่อนเสมอ (LESSONS KS-118) แล้วเขียน `สรุปจาก Builder` ต่อท้ายเองในหน้าที่รอคุณตัดสิน (v0.3.6)
- การอนุมัติเก็บ sha256 ของไฟล์ที่อนุมัติ ถ้าไฟล์เปลี่ยนหลังอนุมัติ การอนุมัตินั้นใช้ไม่ได้ทันที
- ถ้าเป็น `KS-xx` ใช้ `require_jira` เหมือน /feature · ภาษาใช้ตามข้อความล่าสุดของคุณ

## 3. สิ่งที่ Claude ทำต่างจาก /feature

| | /feature spec | /spike |
|---|---|---|
| ถาม | รวมรอบเดียวก่อน Gate 1 | **วนหลายรอบ** และตอบคำถามของคุณได้ (`===REPLY===`) |
| ท่าที | ร่างตามการ์ด | **ต้องแย้ง** ถ้าเห็นว่า requirement เสี่ยงหรือมีทางที่ดีกว่า (`===PUSHBACK===`) |
| อ่าน | การ์ด + repo นี้ | + `jira_search` ใน KS (หา epic/การ์ดซ้ำ) + repo อื่นใน `spike.read_dirs` (`--add-dir`) |
| ผลลัพธ์ | spec.md | solution.md → cards.json |

ถ้าคุยกันเกิน 6 รอบ arbiter จะเริ่ม session ใหม่จากไฟล์ (intent + answers + solution) แทน `--resume` เพื่อไม่ให้ context บวมจนแพง

## 4. กฎที่เครื่องตรวจ `cards.json` (ไม่เชื่อ AI)

1. ฟิลด์ครบ: `id, type(Epic/Story/Task), title, parent, points(1,2,3,5,8), model(haiku/sonnet/opus), effort, repo, depends, scope_hint, ac`
2. ทุกการ์ดมี AC · Story / Epic / Release **ไม่บังคับ** (v0.3.1) Claude เสนอตามเนื้องาน หน้าการ์ดแจ้งเมื่อไม่มี Story
3. การ์ดที่ไม่ใช่ Epic มีแต้มไม่เกิน `max_points` (ค่าตั้งต้น 8) ถ้าเกินต้องแตกเพิ่ม
4. Story/Task มี `repo` เดียวจาก `spike.repos` เพราะ /feature ทำงานใน repo เดียว
5. `depends` ชี้ไปที่ id ที่มีอยู่จริงและไม่วนเป็นวง
6. **path ใน `scope_hint` และใน "ผลกระทบ" ของ solution ต้องมีอยู่จริง** หรือขึ้นต้นด้วย `new:` (กันการเดาโค้ดที่ไม่มีอยู่)
7. ชื่อ release ที่ดูเหมือนเลขเวอร์ชันของ service (`xxx-v1.2.3`) จะได้คำเตือน (AGENTS กฎ 12) แต่ไม่บล็อก

## 5. ส่งต่อให้ /feature โดยไม่มีใครเล่าซ้ำ

- `jira-map.json` จับคู่ `C1→KS-201` · ตอนรัน `arbiter spec KS-201` ถ้า arbiter เจอ key นี้ใน spike ใด จะเพิ่มบรรทัดใน prompt ให้ Claude อ่าน `solution.md` และการ์ดนั้นใน `cards.json` เอง (ตัดสินด้วยโปรแกรม ไม่ได้พึ่งข้อความใน Jira)
- ถ้าเนื้อการ์ดใน Jira ไม่ตรงกับ `cards.json` ที่อนุมัติ (เช่น Gemini เขียนใหม่) Claude จะถามใน Gate 1 ของการ์ดนั้น ซึ่งเป็นการตรวจที่ไม่ต้องเพิ่มค่าใช้จ่าย
- ระยะถัดไป (ยังไม่ทำ): ใช้ `model/effort` ของการ์ดเป็นค่าตั้งต้นของ /feature

## 6. `/poc` (track แยก ทำเมื่อมีการ์ด POC จริง)

- **POC ไม่บังคับ** Claude เสนอเองได้เมื่อเลือกทางไม่ได้จริง (ต้องบอกว่าไม่แน่ใจเรื่องอะไร และถ้าไม่ลองจะเลือกทางไหน) คุณเป็นคนตัดสิน: สั่งข้ามแล้วให้ Claude เลือกเลย หรือสั่งให้ทำ POC เองก็ได้แม้ Claude ไม่ได้เสนอ
- ที่อยู่ของ POC: การ์ดใน KS (Task ติด label `poc`) บนบอร์ด · โค้ดอยู่ในโฟลเดอร์แยกข้าง repo บน branch `spike/<KEY>` ไม่ merge · ผลสรุปอยู่ใน `.arbiter/poc/<KEY>/note.md` และ comment บนการ์ด
- spike กับ POC เชื่อมกันแค่ 2 จุด: spike เสนอการ์ด POC ก่อนถึง อนุมัติแผน และ `spike resume` ให้ Claude อ่าน note ของ POC เอง
- POC ต้องจบก่อน อนุมัติแผน เพราะถ้าแตกการ์ดจากสมมติฐานที่ยังไม่ได้พิสูจน์ แล้ว POC ไม่ผ่าน ต้องรื้อการ์ดทั้งชุด
- `arbiter poc start <KS-xx>` สร้าง `git worktree` ที่ `../<repo>-poc-<KEY>` บน branch `spike/<KEY>` จึงไม่กระทบ working tree หลักหรือการ์ดที่กำลังทำ
- การ์ด POC มีคำสั่งตรวจของแต่ละคำถาม · `poc check` รันคำสั่งเหล่านั้นแล้วเขียน evidence เอง · Claude อ่าน evidence + diff แล้วเขียน `note.md` (ตอบคำถาม: ได้/ไม่ได้/ได้แบบมีเงื่อนไข) · จำกัด 4 รอบ
- hook: บน `spike/*` commit ได้ (ยังสแกน secret และไฟล์ต้องห้าม) · `commit-msg` เติม trailer `Arbiter-Spike: <ID>` อัตโนมัติ
- **กัน merge**: hook `pre-push` ใหม่บล็อกทุก commit ที่มี trailer `Arbiter-Spike` ไม่ว่าจะ merge มาทางไหน (merge แบบ fast-forward ไม่มี hook ให้ดักตอน merge จึงดักตอน push แทน)
- โค้ดใน POC ไม่ถูกนำไปใช้ต่อ การ์ดอ้างถึงได้ แต่ /feature ต้องเขียนใหม่

## 7. ไฟล์และคำสั่ง

```
.arbiter/spikes/<KS-xx | SPIKE-n>/
  intent.md  inputs/  answers.md  questions.md  solution.md
  cards.json  cards.md (arbiter สร้าง)  poc-cards.json (ถ้ามี)
  approvals.json {solution, cards}  jira-map.json  sessions.jsonl
```

`arbiter spike new <KS-xx> | --text "..." | --file <path>...` · `answer` · `approve` (รู้เองว่าเป็น อนุมัติแผน, C หรือการ์ด POC) · `resume` · `link` · `status`
ไม่ใช้ `.arbiter/ACTIVE` (hook ใช้ไฟล์นี้หา scope ของการ์ด /feature) จึงรัน spike ซ้อนกับการ์ด /feature ได้

config ใหม่ (commit แล้วเท่านั้นตามกฎเดิม): `spike: {model, effort, repos{name: path}, read_dirs, max_points, max_rounds_resume}`

## 8. งานที่ต้องทำ (ขนาดโดยประมาณ)

| ระยะ | งาน | ขนาด |
|---|---|---|
| A0 | ย้าย `render/sections/parse_stream/Card.claude` ไปไว้ `bin/arbiter_claude.py` (arbiter.py 531 บรรทัด เกินงบ 300 อยู่แล้ว) | 2 |
| A1 | `bin/arbiter_spike.py`: new/answer/approve/link/status + validator ข้อ 4 + prompt `spike_*.md` 3 ไฟล์ | 5 |
| A2 | workflow `spike.md` ของ Gemini + install.py + hook ข้อ 5 ใน `spec` | 2 |
| A3 | เทสต์ e2e ด้วย fake Claude: วนหลายรอบ, การอนุมัติถูกยกเลิกเมื่อไฟล์เปลี่ยน, validator, link | 3 |
| B | `/poc`: worktree, `poc check`, note, hook `commit-msg` + `pre-push` + spike `resume` | 5 |

**ข้อเสนอ:** ทำระยะ A ก่อน (ไม่มีโค้ด POC จึงไม่ต้องแก้ hook เดิมเลย) แล้วทำระยะ B เมื่อมีการ์ด POC จริง ตามหลัก "ทำเมื่อมีตัวกระตุ้น" ระหว่างนั้นถ้า spike เสนอการ์ด POC คุณทำ POC เองนอก arbiter แล้วส่งผลกลับด้วย `spike answer`
**ตัดสินแล้ว (2026-11-06):** Gemini สร้างการ์ดบน Jira เป็นค่าตั้งต้น

**ตัดสินแล้ว (2026-11-09, ใช้ค่าตั้งต้นที่เสนอทั้งหมด)**
- เริ่มได้ 2 แบบ: `/spike KS-xx` (จบแล้วการ์ด spike ไป Done) หรือพิมพ์โจทย์ → `SPIKE-n`
- ~~อ่านได้เฉพาะ repo ที่ติดตั้ง arbiter ไปก่อน~~ → **ต้องมี `spike.read_dirs` ในระยะ A** เพราะ spike ใบแรกรันใน `hbs3/hbs-strangler-service` ซึ่งยังไม่มีซอร์สโค้ดเลย ความรู้อยู่ใน repo อื่นทั้งหมด (ทดสอบ `--add-dir` เป็นขั้น 0) · spike ใบแรกอ่านแค่ `hbs-legacy/hbs-web-core` (hbs3 เป็นงาน modernize คนละเรื่อง) · Claude เสนอชื่อ Epic/Release และใส่ใต้ Epic เดิมถ้ามี · เตือนเมื่อค่า Claude เกิน $15 · สร้าง `/spike` ตอนนี้
- **requirement มาได้ทุกแบบปนกัน** (ข้อความ, .docx, .xlsx, PDF, รูป): arbiter แปลง .docx/.xlsx เป็นข้อความเองตอนรับไฟล์ (เป็น zip ของ XML ใช้ stdlib ได้) เก็บไฟล์ต้นฉบับไว้ใน `inputs/` · PDF/รูปให้ Claude อ่านตรง · แปลงหรืออ่านไฟล์ไหนไม่ได้ ให้บอกชื่อไฟล์แล้วหยุดรอ ห้ามเดาเนื้อหา
- **แต้ม** = ขนาดงาน ไม่ใช่คุณค่า · ใส่ที่ Story/Task 1–8 · ใหญ่กว่า 8 ให้ซอยเป็น Story เล็กหลายใบที่แต่ละใบยังส่งมอบได้ · subtask ใช้แบ่งขั้นตอนภายในการ์ด ไม่ต้องมีแต้ม
- **ใครตรวจรับ:** Story → `UAT / QA` ให้ BA/QA ตรวจ AC แล้ว sign-off จึง Done · Task → ผ่าน AC + Gate 2 แล้ว Done · AC ของ Story เขียนให้ BA อ่านเข้าใจ (ไม่ใช่ศัพท์ dev)
- ป้าย model/effort บนการ์ด = model ที่ Claude ใช้ทำ spec/review ของการ์ดนั้นใน /feature
- **โจทย์ spike ใบแรกเปลี่ยน:** backend service ดึงข้อมูลจาก HBS ส่งให้หน้า UI เพื่อให้โปรเจกต์ TA ดูว่าข้อมูลฝั่งขายเข้า HBS แล้วหรือยังและสถานะเป็นอะไร (HBS อ่านอย่างเดียว)
ต้องทดสอบก่อน (ขั้น 0): `claude -p --add-dir <repo อื่น> --tools Read` อ่านนอก cwd ได้โดยไม่ค้าง

## 9. ตัวชี้วัดของ spike

จำนวนรอบที่คุยและค่า Claude จนถึง อนุมัติแผน · จำนวนการ์ดที่ต้องแตกใหม่หรือถูกแก้มากตอน Gate 1 ของ /feature (บอกคุณภาพของการแตกการ์ด) · จด 1 บรรทัดต่อ spike ใน LESSONS.md
