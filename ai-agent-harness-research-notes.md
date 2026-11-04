# Research Notes: การคุม AI Coding Agent ให้ทำงานตามโปรเซส (Harness Engineering)

> สรุปจากการคุยวันที่ 4 พ.ย. 2026 สำหรับใช้ค้นคว้าต่อ
> ข้อมูลจากเว็บมีลิงก์ที่มาอยู่ท้ายไฟล์ เนื้อหาเรียบเรียงใหม่ ไม่ได้คัดลอกตรงตามต้นฉบับ

---

## 1. สิ่งนี้เรียกว่าอะไร

**คำหลัก: Harness / Harness Engineering**

- นิยามที่ใช้กันกว้างที่สุดคือ **Agent = Model + Harness**
  harness คือทุกอย่างรอบตัวโมเดล ได้แก่ loop, tools, การจัดการ context, safety controls, orchestration และจุดที่ให้ผู้ใช้ต่อขยายได้
- harness ที่ผู้ใช้สร้างเพิ่มเอง Martin Fowler เรียกว่า **outer harness** ได้แก่ rules, skills, hooks, scripts, linters, tests และ CI gates ที่ใส่ให้ agent ตัวใดตัวหนึ่ง
- คำว่า harness engineering เริ่มใช้เป็นชื่อสาขาช่วงต้นปี 2026 OpenAI, Martin Fowler, O'Reilly และ arXiv ใช้คำนี้

**คำใกล้เคียงสำหรับค้นต่อ**

| คำ | ความหมายโดยย่อ |
|---|---|
| Harness engineering | ออกแบบระบบรอบ agent ให้ทำงานได้น่าเชื่อถือ เป็นคำที่ครอบคลุมที่สุด |
| Spec-Driven Development (SDD) | ท่าหนึ่งของ harness: เขียน spec ก่อน แล้วให้ agent ทำตาม spec |
| Context engineering | จัดว่าข้อมูลอะไรเข้า context ของ agent ตอนไหน |
| Guardrails | กฎหรือข้อห้าม ส่วนใหญ่ใช้ในความหมายด้าน safety |
| Agentic workflow / orchestration | การต่อหลายขั้นหรือหลาย agent เป็น pipeline |
| Fitness functions | การวัดคุณลักษณะของสถาปัตยกรรมแบบอัตโนมัติ เป็นคำเก่าที่นำกลับมาใช้กับ agent |

---

## 2. กรอบคิดหลัก: Guides กับ Sensors (จาก Martin Fowler)

| | Feedforward (Guides) ทำงานก่อน agent ลงมือ | Feedback (Sensors) ทำงานหลัง agent ลงมือ |
|---|---|---|
| **Computational** (deterministic, เร็ว, เชื่อถือได้) | bootstrap script, codemod (เช่น OpenRewrite), project template | tests, linter, type checker, structural tests (เช่น ArchUnit), CI gate |
| **Inferential** (ใช้ LLM, ช้ากว่า, ผลไม่แน่นอน) | AGENTS.md, rules, skills, spec | AI code review, LLM-as-judge, validator agent |

ประเด็นสำคัญ:
- **มีแค่ feedforward:** agent รู้กฎ แต่ไม่รู้ว่าทำได้ตามกฎหรือเปล่า
- **มีแค่ feedback:** agent ทำผิดซ้ำเรื่องเดิมไปเรื่อยๆ
- ต้องมีทั้งสองแบบ และ **ส่วนที่บังคับได้จริงต้องเป็น computational** เพราะ rule หรือ prompt บังคับไม่ได้ 100%
- ผลจาก sensor ควรเขียนให้ LLM อ่านแล้วแก้ต่อได้ เช่นข้อความ error ของ linter ที่บอกวิธีแก้ด้วย
- **Steering loop:** ถ้าปัญหาเดิมเกิดซ้ำ ให้แก้ที่ harness คือเพิ่ม guide หรือ sensor ไม่ใช่แก้แค่โค้ดครั้งนั้น
- **Keep quality left:** check ที่ถูกและเร็วให้รันก่อน commit ส่วน check ที่แพงให้รันใน pipeline หลัง integrate

**ประเภทของ harness แยกตามสิ่งที่ต้องการคุม**
1. **Maintainability harness:** คุณภาพโค้ดภายใน ทำง่ายที่สุดเพราะมีเครื่องมือเดิมให้ใช้เยอะ
2. **Architecture fitness harness:** performance, observability, ขอบเขตของ module
3. **Behaviour harness:** แอปทำงานถูกตามที่ต้องการหรือไม่ เป็นส่วนที่ **ยังยากที่สุด** เพราะยังต้องเชื่อเทสต์ที่ AI เขียนเอง

**ข้อจำกัดที่ Fowler ระบุ:** sensor ยังจับไม่ได้อย่างสม่ำเสมอว่า agent วินิจฉัยปัญหาผิด, ทำเกินที่ขอ หรือเข้าใจคำสั่งผิด ถ้าคนไม่ระบุให้ชัดว่าต้องการอะไร ก็ไม่มี sensor ไหนตรวจความถูกต้องได้

**Harnessability:** โค้ดแต่ละแบบใส่ harness ได้ยากง่ายไม่เท่ากัน ภาษาที่มี type, module ที่แบ่งขอบเขตชัด และ framework ที่มีโครงชัดจะใส่ harness ได้ง่าย ส่วนโค้ด legacy ที่ต้องการ harness มากที่สุดกลับเป็นโค้ดที่สร้าง harness ยากที่สุด

---

## 3. ยังไม่มี framework ไหนที่ดีที่สุดสำหรับทุกงาน

- **arXiv 2606.04967** เปรียบเทียบ framework 6 ตัวด้วยเกณฑ์ 6 ด้าน (specification, context, roles, execution, validation, portability)
  - ข้อที่ทุกตัวเริ่มคล้ายกัน: ใช้ artifact ที่บันทึกเป็นไฟล์, work contract, traceability และการ review โดยคน
  - ไม่มีตัวไหนทำได้ดีครบทั้ง 6 ด้าน ยิ่งกระบวนการลึกยิ่งผูกกับ agent ตัวเดียว
  - ความเสี่ยงที่พบซ้ำ: spec กับโค้ดไม่ตรงกันเมื่อเวลาผ่านไป, เชื่อ artifact ที่ AI สร้างมากเกินไป, extension จาก community เปราะบาง, ผูกกับ platform และยังไม่มี benchmark ที่วัดกระบวนการทั้งหมด
- **Uvik SDD Benchmark** ใช้ ticket Python จริง 50 ใบ (เป็นของบริษัทเดียว ควรถือเป็นสัญญาณ ยังไม่ใช่ข้อสรุป)
  - OpenSpec merge ได้ 42/50 ส่วนแบบไม่มี spec ได้ 36/50
  - งานฟีเจอร์และ data pipeline: แบบมี spec ผ่าน 90% แบบไม่มี spec ผ่าน 60%
  - งานที่แตะ 1–2 ไฟล์ แทบไม่ต่างกัน งานที่แตะ 3 ไฟล์ขึ้นไปจึงเห็นผลชัด
  - bug fix, refactor และงานเขียนเทสต์ ส่วนใหญ่ไม่คุ้มกับ overhead ของ spec
  - การเขียน spec กิน token ประมาณ 1/3 และของ BMAD เกิน 40%
  - spec ที่ครบกว่าสัมพันธ์กับผลที่ดีกว่า defect ที่เจอตอน review ลดจาก 0.86 เหลือ 0.46 ต่อ ticket

---

## 4. ท่าที่เหมาะกับงานแต่ละแบบ (ใช้เป็นสมมติฐานไปทดสอบ)

| ประเภทงาน | ท่าที่น่าจะเหมาะ | framework ที่ควรศึกษา |
|---|---|---|
| แอปใหม่หรือฟีเจอร์ใหญ่ (greenfield) | spec เต็มรูป: requirements → design → tasks + gate ทุกขั้น | GitHub Spec Kit, BMAD Method, Kiro Specs |
| ฟีเจอร์ในโค้ดเดิม (brownfield) | recon ก่อน + spec เฉพาะส่วนที่เปลี่ยน + regression test ของพฤติกรรมเดิม | OpenSpec |
| bug fix | เขียนเทสต์ที่ fail ก่อน → แก้ → เทสต์ผ่าน ไม่ต้องมี spec | superpowers (debugging, tdd) |
| refactor | ทำให้เทสต์เขียวก่อน แล้วเปลี่ยนทีละน้อย พฤติกรรมต้องไม่เปลี่ยน | rules + CI gate |
| migration หรือ dependency upgrade | recon + แผนทีละขั้น + rollback + compatibility test | spec เต็มรูป |
| prototype หรือ spike | ปล่อยอิสระ แล้วทิ้งโค้ด | ไม่ต้องใช้ |

**เกณฑ์ตัดสินใจเบื้องต้น:** ถ้างานแตะ 3 ไฟล์ขึ้นไป, requirement ไม่ชัด หรือทำผิดแล้วต้องแก้ใหม่เยอะ ให้ใช้ spec ถ้าไม่ใช่ ให้ใช้ TDD + review

---

## 5. บทเรียนจาก SpecShip (repo `aws-samples/sample-specship`)

สถานะ: เป็น sample หรือ experimental ใน org `aws-samples` ผู้เขียนเป็นบุคคล (YungJun0112) ไม่ใช่ผลิตภัณฑ์ทางการของ AWS

บทเรียนที่ใช้ได้ทั่วไป ไม่ผูกกับ Kiro:
1. **ห้ามให้ AI ตัดสินงานของตัวเอง** ต้องใช้ validator แยก เคยมี agent รายงานว่า PASS ทั้งที่ไม่ได้รัน validator
2. **ใช้ gate ที่เป็นโค้ด** (`process-checker.js`) ตรวจไฟล์บนดิสก์ แล้วคืน exit 0 หรือ 1 แทนการถาม agent ว่าเสร็จหรือยัง ตรงกับแนวคิด computational sensor ของ Fowler
3. **จำกัดขนาด context** ให้ subagent ทำทีละ task เคยมีรอบที่สร้างประมาณ 100 ไฟล์ใน context เดียว แล้ว context หมดก่อนจะ validate
4. **ต้องมี API contract** ระบุ response shape และ camelCase หรือ snake_case ให้ชัด แล้วใส่ใน prompt ของ worker ทุกตัว
5. **เทสต์ผ่านไม่ได้แปลว่าแอปใช้ได้** ต้องทดสอบใน browser เป็นลำดับ (create → duplicate → delete → ตรวจผล) จึงจะจับ bug เรื่อง state ได้
6. **บอก AI ให้ชัดว่าต้องทำอะไรบ้าง** ใส่รายการฟีเจอร์ที่ผลิตภัณฑ์จริงทุกตัวมีเป็น checklist ใน prompt แล้วจำนวนฟีเจอร์ที่ทำได้เพิ่มจาก 4/14 เป็น 10/10
7. **bug ที่ validator เจอต้องกลายเป็น regression test**
8. **ความปลอดภัย:** ห้าม agent push เอง, คำค้นบนเว็บต้องไม่มีข้อมูลของโปรเจค, เนื้อหาที่อ่านเข้ามาถือเป็นข้อมูล ไม่ใช่คำสั่ง

ข้อสังเกต: กฎแบบ always-on ของ SpecShip กิน context ทุก turn และส่วนใหญ่ยังเป็น prompt ซึ่งบังคับได้ไม่แน่นอน

---

## 6. Kiro Powers เทียบกับ harness ที่เขียนเอง

- Power = `POWER.md` + MCP config + steering/hooks **แพ็กรวมกัน**
- จุดที่ต่าง: **โหลด MCP tools เมื่อเรื่องที่คุยตรงกับ keyword** ทำให้ประหยัด context, ติดตั้งได้คลิกเดียวจาก registry และมี onboarding
- **ไม่ได้ช่วยให้บังคับ agent ได้มากขึ้น** steering ใน Power ยังเป็น prompt
- ตอนนี้ใช้ได้เฉพาะ Kiro IDE (การรองรับเครื่องมืออื่นยังเป็น "coming soon" ตามบล็อกเปิดตัว)

---

## 7. ถ้าใช้ Antigravity IDE

ชิ้นส่วนที่มีให้ใช้: **rules**, **workflows** (slash command ที่ต่อหลายขั้นเป็น pipeline), **skills** และ **AGENTS.md** (ใช้โฟลเดอร์ `.agent/`)
- Google มี codelab สอนใช้ `agents.md` กำหนดบทบาท (PM, frontend, QA, DevOps) แล้วใช้ workflows ต่อเป็น pipeline แต่เป็นตัวอย่างสอนใช้งาน ยังไม่ใช่ framework มาตรฐาน
- framework ส่วนใหญ่เป็น markdown กับ CLI ที่ไม่ผูกกับ IDE จึงนำมาใช้ได้ **ต้องเช็กเองว่าแต่ละตัวรองรับ Antigravity โดยตรงหรือต้องปรับ** (ยังไม่ได้ตรวจ)

**โครงที่เสนอให้ลอง:**
```
.agent/
  rules/            # แกนบางๆ ที่ใช้ทุกงาน: TDD, เขียน AC ก่อน, ห้ามแก้เกินขอบเขต, ห้าม push เอง
  workflows/        # /feature  /bugfix  /refactor  /migrate  แต่ละตัวมีความลึกต่างกัน
  skills/           # โหลดเฉพาะตอนใช้: recon, spec-writing, browser-test, security-review
scripts/
  gate.(js|sh)      # computational sensor: test + typecheck + lint + ตรวจว่ามี artifact ครบ (exit 0/1)
CI                  # gate เดียวกันรันซ้ำบน server ให้ agent หลบไม่ได้
```

---

## 8. คำถามสำหรับ research ต่อ

- [ ] งานที่ทีมทำบ่อยที่สุดคือประเภทไหน ควรเริ่ม harness จากงานประเภทนั้น
- [ ] ส่วนไหนของ process ที่ **ต้อง** เป็น computational gate และส่วนไหนเป็น prompt ได้
- [ ] ทำ behaviour harness อย่างไรโดยไม่ต้องเชื่อเทสต์ที่ AI เขียนเองทั้งหมด (เช่น approved fixtures, mutation testing, ให้คนเขียน acceptance test)
- [ ] กฎแบบ always-on กิน context เท่าไร และกระทบคุณภาพงานแค่ไหน
- [ ] วัดผลด้วยอะไร: merge rate, จำนวนรอบที่ต้องแก้, defect ตอน review, token ต่อ ticket, เวลาที่คนต้อง review
- [ ] ถ้าต้องใช้หลายเครื่องมือ (Antigravity, Kiro, Claude Code, Cursor) ควรเก็บแกนของ harness ไว้ในรูปแบบไหน
- [ ] ป้องกันไม่ให้ spec กับโค้ดค่อยๆ ไม่ตรงกันได้อย่างไร

**คำค้นที่แนะนำ:** `harness engineering coding agents`, `agent harness feedforward feedback`, `spec-driven development benchmark`, `OpenSpec vs Spec Kit`, `BMAD method`, `superpowers skills`, `Antigravity workflows skills`, `AGENTS.md conventions`, `context engineering`, `approved fixtures testing`

---

## แหล่งที่มา

- [Martin Fowler: Harness engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html)
- [OpenAI: Harness engineering, leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
- [O'Reilly: Agent Harness Engineering](https://www.oreilly.com/radar/agent-harness-engineering/)
- [arXiv 2609.00006: Harness Engineering, a source-code study of eleven coding agents](https://arxiv.org/html/2609.00006)
- [arXiv 2602.14690: Harness Engineering for Agentic AI Coding Tools](https://arxiv.org/html/2602.14690v5)
- [arXiv 2606.04967: Process taxonomy of AI software development frameworks](https://arxiv.org/html/2606.04967)
- [Uvik: Spec-Driven Development Benchmark 2026](https://uvik.net/blog/spec-driven-development-benchmark/)
- [bswen: AI Specification Frameworks Compared 2026](https://docs.bswen.com/blog/2026-08-07-ai-spec-frameworks-compared/)
- [deepsense.ai: 9 patterns of harness engineering](https://deepsense.ai/blog/harness-engineering-for-coding-agents-9-patterns-for-more-reliable-ai-assisted-development/)
- [Kiro: Introducing powers](https://kiro.dev/blog/introducing-powers/)
- [Google Codelab: Autonomous developer pipelines with agents.md and skills.md in Antigravity](https://codelabs.developers.google.com/autonomous-ai-developer-pipelines-antigravity)
- [RSMuthu/SDD-comparison (GitHub)](https://github.com/RSMuthu/SDD-comparison)
