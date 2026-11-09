#!/usr/bin/env python3
"""arbiter v0: /feature flow  spec -> answer -> approve -> review -> commit.

The orchestrating agent (Gemini) only passes pointers (card key) and the user's verbatim words.
Prompts to Claude are built here from fixed templates; verdicts come from this script.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arbiter_core as hc  # noqa: E402
import arbiter_board  # noqa: E402
import arbiter_claude as ac  # noqa: E402
from arbiter_claude import now, render, sections  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
SELF = f'python "{Path(__file__).resolve()}"'
SPEC_TOOLS = f"{ac.BUILTIN_TOOLS},{ac.JIRA_READ_TOOLS}"
LOCAL_SPEC_TOOLS = ac.BUILTIN_TOOLS  # LOCAL cards have no Jira card, so no Jira access at all
REVIEW_TOOLS = ac.BUILTIN_TOOLS
USER_ESCALATE = ('เลือกหนึ่งทาง: "ทำต่อ: <คำตัดสินของคุณ>" (reviewer ต้องยึดคำตัดสินนี้) · '
                 '"แก้ spec: <สิ่งที่จะแก้>" (แล้วอนุมัติ Gate 1 ใหม่) · "พักการ์ด"')
SHELL_META = re.compile(r"[|&;<>`]")
SHOW = "แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร"
MAX_PROVE = 3


def decide_next(key):
    """NEXT for every page where the user must rule: continue with a binding decision, revise the spec, or park."""
    return (f'{SHOW} หยุดแก้เอง แล้วรอผู้ใช้ตัดสิน: ถ้าให้ทำต่อ ให้รัน {SELF} review {key} --continue "<ข้อความของผู้ใช้>" · '
            f'ถ้าให้แก้ spec ให้รัน {SELF} answer {key} "<ข้อความของผู้ใช้ ห้ามแก้>" · '
            f'ถ้าให้พักการ์ด ให้รัน {SELF} park {key} "<ข้อความของผู้ใช้>"')


class Card:
    def __init__(self, key):
        self.root = hc.repo_root()
        self.cfg = hc.load_config(self.root)
        self.key = key.upper()
        self.local = bool(hc.LOCAL_RE.fullmatch(self.key))
        if not self.local and not re.fullmatch(self.cfg.get("key_pattern", r"KS-\d+"), self.key):
            raise RuntimeError(f"รหัสการ์ด {self.key} ไม่ตรงกับ key_pattern ใน .arbiter/config.json")
        self.dir = hc.card_dir(self.root, self.key)
        self.rel = self.dir.relative_to(self.root).as_posix()
        self.docs = self.cfg.get("docs_allow", [])

    def p(self, name):
        return self.dir / name

    def spec_text(self):
        return self.p("spec.md").read_text(encoding="utf-8") if self.p("spec.md").exists() else ""

    def spec_tools(self):
        return LOCAL_SPEC_TOOLS if self.local else SPEC_TOOLS

    def spec_prompt(self):
        source = render("source_local.md" if self.local else "source_jira.md", KEY=self.key, CARD_DIR=self.rel)
        origin = hc.spike_origin(self.root, self.key)
        if origin:  # decided in a spike: Claude reads the approved plan itself instead of anyone retelling it
            source += render("source_spike.md", KEY=self.key, SPIKE_DIR=origin[0], CARD_ID=origin[1])
        return render("spec.md", SOURCE=source, KEY=self.key, CARD_DIR=self.rel, LANG_RULE=self.lang_rule())

    def lang_rule(self):
        return ac.lang_rule(self.lang())

    def lang(self):
        """Language code of the user's latest decisive words on this card (intent, answers), else `language`."""
        texts = [self.p("intent.md").read_text(encoding="utf-8")] if self.p("intent.md").exists() else []
        if self.p("answers.md").exists():
            texts += re.split(r"^## .*$", self.p("answers.md").read_text(encoding="utf-8"), flags=re.M)
        return ac.lang_of(texts, self.cfg.get("language", "th"))

    def reco(self):
        """(model, effort) the approved spike card recommends for this card, else (None, None). Shown, never applied."""
        origin = hc.spike_origin(self.root, self.key)
        if not origin:
            return None, None
        cards = hc.read_json(self.root / origin[0] / "cards.json", {}).get("cards", [])
        c = next((c for c in cards if str(c.get("id")) == origin[1]), {})
        return c.get("model"), c.get("effort")

    def review_pick(self, said=""):
        """(model, effort, source) for review/dispute, decided at Gate 1. The user's words win (in the Gate 1
        approval, or at card start), else the spike card's recommendation (shown on the Gate 1 page), else config."""
        c = self.cfg.get("claude", {})
        m = re.search(r"\b(haiku|sonnet|opus)\b", said, re.I)
        e = re.search(r"\b(low|medium|high|xhigh|max)\b", said, re.I)
        if m or e:
            return ((m.group(1).lower() if m else c.get("review_model", "opus")),
                    (e.group(1).lower() if e else c.get("effort", "high")), "คุณสั่งตอนอนุมัติ Gate 1")
        start = hc.read_json(self.p("model.json"), {})
        if start:
            return (start.get("model") or c.get("review_model", "opus"), start.get("effort") or c.get("effort", "high"),
                    "คุณสั่งตอนเริ่มการ์ด")
        rm, re_ = self.reco()
        if rm:
            return rm, re_ or c.get("effort", "high"), "ตามที่การ์ดจาก spike แนะนำ"
        return c.get("review_model", "opus"), c.get("effort", "high"), "ค่าตั้งต้น"

    def claude(self, prompt, tools, model_key, mode, resume=None):
        # spec/answer: opus · high (config) unless the user picked at card start. review/dispute: fixed at Gate 1.
        c = self.cfg.get("claude", {})
        pick = hc.read_json(self.p("model.json"), {})
        if mode in ("review", "dispute"):
            pick = hc.read_json(self.p("approvals.json"), {}).get("gate1", {}).get("review_model") or pick
        return ac.call(self.root, c, prompt, tools, pick.get("model") or c.get(model_key, "opus"),
                       log=self.p("sessions.jsonl"), mode=mode, key=self.key, resume=resume,
                       effort=pick.get("effort"))

    def decide(self, source, text):
        """Record the user's ruling, verbatim. The reviewer and the dispute Architect must follow decisions.md."""
        with self.p("decisions.md").open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {now()} ({source})\n{text}\n")

    def ready(self):
        """True if the card may be worked on (not parked, Gate 1 approved, spec unchanged); else tells the agent why."""
        ap = hc.read_json(self.p("approvals.json"), {})
        if self.p("parked.json").exists():
            hc.say("BLOCKED", f"การ์ด {self.key} ถูกพักไว้", "แจ้งผู้ใช้ แล้วหยุดรอคำสั่ง",
                   user=f'ถ้าจะทำต่อ พิมพ์ "ทำการ์ด {self.key} ต่อ"')
        elif "gate1" not in ap:
            hc.say("BLOCKED", f"{self.key} ยังไม่ผ่าน Gate 1", "แจ้งผู้ใช้ แล้วหยุดรอคำสั่ง")
        elif hc.sha256(self.p("spec.md")) != ap["gate1"]["spec_sha256"]:
            hc.say("BLOCKED", "spec.md ถูกแก้หลังอนุมัติ Gate 1", "แจ้งผู้ใช้ว่าต้องอนุมัติ spec ใหม่ แล้วหยุดรอคำสั่ง")
        else:
            return True
        return False

    def require_jira(self):
        return not self.local and self.cfg.get("claude", {}).get("require_jira", True)

    def last_session(self, modes):
        rows = [r for r in ac.session_rows(self.p("sessions.jsonl")) if r["mode"] in modes and r.get("session_id") and not r.get("is_error")]
        return rows[-1]["session_id"] if rows else None


def spec_result(card, data, fresh=True):
    s = sections(data.get("result"))
    if fresh and card.require_jira() and not data.get("is_error") and not data.get("jira_read"):
        card.p("last-claude-output.txt").write_text(str(data.get("result")), encoding="utf-8")
        hc.say("ERROR", f"Claude อ่านการ์ด {card.key} จาก Jira ไม่สำเร็จ arbiter จึงไม่ใช้ spec รอบนี้ "
                        f"(คำตอบของ Claude เก็บไว้ที่ {card.rel}/last-claude-output.txt)",
               f"แสดงข้อความนี้ให้ผู้ใช้ตามตัวอักษร ห้ามเดาสาเหตุ ห้ามแก้เอง แล้วหยุดรอ "
               f"ถ้าผู้ใช้สั่งลองใหม่ ให้รัน {SELF} spec {card.key}",
               user='พิมพ์ "ลองใหม่" เพื่อให้อ่านการ์ดอีกครั้ง ถ้าเป็นซ้ำ ให้ถาม Claude Code ตรวจการเชื่อมต่อ Jira')
        return 1
    if data.get("is_error") or "SPEC" not in s:
        card.p("last-claude-output.txt").write_text(str(data.get("result")), encoding="utf-8")
        hc.say("ERROR", f"Claude ไม่ได้ส่ง spec ตามรูปแบบ ดู {card.rel}/last-claude-output.txt",
               "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    card.p("spec.md").write_text(s["SPEC"] + "\n", encoding="utf-8")
    q = s.get("QUESTIONS", "").strip()
    if q and q.upper() != "NONE":
        card.p("questions.md").write_text(f"<!-- {now()} -->\n{q}\n", encoding="utf-8")
        hc.say("NEEDS_ANSWERS", f"QUESTIONS ({card.key}):\n{q}",
               f'แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร แล้วรอคำตอบ จากนั้นรัน '
               f'{SELF} answer {card.key} "<ข้อความของผู้ใช้ ห้ามแก้>"',
               user="ตอบคำถามข้างบนในแชทนี้ ข้อที่ไม่ตอบจะใช้ค่า [default] ของข้อนั้น")
        return 0
    card.p("questions.md").unlink(missing_ok=True)
    warn = "" if hc.parse_block(s["SPEC"])["scope_allow"] else "\n(คำเตือน: spec ไม่มี scope_allow)"
    rm, re_, src = card.review_pick()
    warn += (f"\n\nClaude ขั้นรีวิว: {rm} · {re_} ({src}) ถ้าจะใช้ model อื่น ให้บอกพร้อมคำอนุมัติ "
             f'เช่น "อนุมัติ Gate 1 ใช้ opus high"')
    hc.say("READY_FOR_GATE1", f"SPEC {card.key} ({card.rel}/spec.md):\n\n{s['SPEC']}{warn}",
           f'แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร แล้วรอ ถ้าผู้ใช้อนุมัติ ให้รัน '
           f'{SELF} approve {card.key} "<ข้อความอนุมัติของผู้ใช้ ห้ามแก้>" '
           f'ถ้าผู้ใช้ขอแก้ ให้รัน {SELF} answer {card.key} "<ข้อความของผู้ใช้ ห้ามแก้>"',
           user='อ่าน spec แล้วพิมพ์ "อนุมัติ Gate 1" เพื่อให้เริ่มเขียนโค้ด หรือพิมพ์สิ่งที่ต้องการให้แก้ใน spec')
    return 0


def next_local_key(root):
    d = hc.hdir(root) / "tasks"
    nums = [int(m.group(1)) for p in (d.iterdir() if d.exists() else []) if (m := hc.LOCAL_RE.fullmatch(p.name))]
    return f"LOCAL-{max(nums, default=0) + 1}"


def cmd_spec(a):
    if a.local and a.key:
        raise RuntimeError("ใช้ได้อย่างใดอย่างหนึ่ง: spec <รหัสการ์ด> หรือ spec --local \"<โจทย์>\"")
    if a.local:
        text = " ".join(a.local).strip()
        if not text:
            raise RuntimeError("โจทย์ว่าง")
        card = Card(next_local_key(hc.repo_root()))
        card.dir.mkdir(parents=True, exist_ok=True)
        card.p("intent.md").write_text(text + "\n", encoding="utf-8")  # the user's words, verbatim
    elif a.key:
        card = Card(a.key)
        if card.local and not card.p("intent.md").exists():
            raise RuntimeError(f"{card.key} ไม่มีโจทย์ (intent.md) งานใหม่ที่ไม่มีการ์ดให้เริ่มด้วย spec --local \"<โจทย์>\"")
    else:
        raise RuntimeError("ต้องระบุรหัสการ์ด หรือ --local \"<โจทย์>\"")
    if card.p("spec.md").exists():
        hc.say("ERROR", f"{card.key} มี spec อยู่แล้ว", f"ใช้ {SELF} status {card.key} แทน",
               user="ไม่ต้องทำอะไร Gemini จะดูสถานะการ์ดนี้ต่อ")
        return 1
    card.dir.mkdir(parents=True, exist_ok=True)
    (hc.hdir(card.root) / "ACTIVE").write_text(card.key, encoding="utf-8")
    if a.model or a.effort:  # the user's own choice at card start: used for every Claude call of this card
        hc.write_json(card.p("model.json"), {"model": a.model, "effort": a.effort, "at": now()})
    if not card.local:  # a card worked on while it sits in the Backlog compartment vanishes from the board
        arbiter_board.to_board([card.key], card.cfg)
    data = card.claude(card.spec_prompt(), card.spec_tools(), "spec_model", "spec")
    return spec_result(card, data)


def cmd_answer(a):
    card = Card(a.key)
    text = " ".join(a.text)
    with card.p("answers.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {now()}\n{text}\n")
    ap = hc.read_json(card.p("approvals.json"), {})
    if ap.pop("gate1", None):
        hc.write_json(card.p("approvals.json"), ap)  # spec will change, approval no longer valid
    data, sid = None, card.last_session({"spec", "answer"})
    if sid:  # the resumed session already read the card from Jira when it was verified
        data = card.claude(render("answer.md", KEY=card.key, ANSWER=text, CARD_DIR=card.rel,
                                  LANG_RULE=card.lang_rule()),
                           card.spec_tools(), "spec_model", "answer", resume=sid)
        if data.get("is_error") or "SPEC" not in sections(data.get("result")):
            data = None
        else:
            return spec_result(card, data, fresh=False)
    # fresh session rebuilds context from the card files and must read Jira again
    data = card.claude(card.spec_prompt(), card.spec_tools(), "spec_model", "answer")
    return spec_result(card, data)


def cmd_approve(a):
    card = Card(a.key)
    if not card.p("spec.md").exists() or card.p("questions.md").exists():
        hc.say("BLOCKED", "ยังไม่มี spec หรือยังมีคำถามค้าง", f"ใช้ {SELF} status {card.key}")
        return 1
    ap = hc.read_json(card.p("approvals.json"), {})
    said = " ".join(a.text)
    rm, re_, src = card.review_pick(said)  # no objection in the approval = the model shown on the Gate 1 page
    ap["gate1"] = {"at": now(), "text": said, "spec_sha256": hc.sha256(card.p("spec.md")),
                   "review_model": {"model": rm, "effort": re_, "source": src}}
    hc.write_json(card.p("approvals.json"), ap)
    hc.NOTICES.append(f"Claude ขั้นรีวิว: {rm} · {re_} ({src})")
    st = hc.read_json(card.p("state.json"), None)
    if st:  # a spec revised mid-card is a new contract: the old rejection and gate-run budget no longer apply
        st["rejections"], st["gate_runs"] = 0, 0
        hc.write_json(card.p("state.json"), st)
    (hc.hdir(card.root) / "ACTIVE").write_text(card.key, encoding="utf-8")
    scope = "\n".join(f"  - {s}" for s in hc.parse_block(card.spec_text())["scope_allow"])
    hc.say("APPROVED_GATE1", f"scope_allow:\n{scope}",
           f"ลงมือเขียนโค้ดตาม {card.rel}/spec.md แก้เฉพาะไฟล์ใน scope_allow (ห้ามแก้ไฟล์ใน .arbiter/) "
           f"เสร็จแล้วรัน {SELF} review {card.key}")
    return 0


def run_cmd(cmd, cwd, log, timeout):
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":  # kill the whole tree (cmd.exe -> mvn -> java), not just the shell
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                proc.kill()
            proc.wait()
            return 124


def run_gate(card, rnd):
    spec = card.spec_text()
    blk = hc.parse_block(spec)
    tree, base = hc.code_tree(card.root, card.docs, blk["scope_allow"])
    changed = hc.changed_files(card.root, base, tree)
    outside = [f for f in changed if not hc.in_scope(f, blk["scope_allow"])]
    checks = [{"name": "scope", "exit": 1 if outside or not changed else 0,
               "outside_scope": outside, "note": "" if changed else "ไม่มีไฟล์เปลี่ยน"}]
    verify = card.cfg.get("verify", []) + [{"name": f"spec-{i + 1}", "cmd": c, "from_spec": True}
                                           for i, c in enumerate(blk["verify"])]
    if not verify:
        checks.append({"name": "verify", "exit": 1, "note": "ไม่มีคำสั่ง verify เลย (ตั้งใน .arbiter/config.json)"})
    for v in verify:
        if checks[0]["exit"]:
            checks.append({"name": v["name"], "cmd": v["cmd"], "exit": None, "note": "skipped (scope failed)"})
        elif v.get("from_spec") and SHELL_META.search(v["cmd"]):
            checks.append({"name": v["name"], "cmd": v["cmd"], "exit": 1,
                           "note": "คำสั่ง verify จาก spec ห้ามมี | & ; < > ` (ซ่อน exit code ได้)"})
        else:
            log, t0 = card.p(f"gate-r{rnd}-{v['name']}.log"), time.time()
            code = run_cmd(v["cmd"], card.root, log, card.cfg.get("verify_timeout_sec", 3600))
            checks.append({"name": v["name"], "cmd": v["cmd"], "exit": code,
                           "seconds": round(time.time() - t0, 1), "log": log.name})
    if hc.code_tree(card.root, card.docs, blk["scope_allow"])[0] != tree:  # verify must test exactly the recorded tree
        checks.append({"name": "tree-stable", "exit": 1,
                       "note": "โค้ดเปลี่ยนระหว่างรัน verify (คำสั่ง verify แก้ไฟล์ หรือมีคนแก้ระหว่างรัน)"})
    ok = all(c["exit"] == 0 for c in checks)
    base_sha = hc.git("rev-parse", "HEAD", cwd=card.root).strip() if base == "HEAD" else None
    ev = {"card": card.key, "gate_run": rnd, "at": now(), "arbiter_version": hc.VERSION, "base": base_sha,
          "tree": tree, "spec_sha256": hc.sha256(card.p("spec.md")), "changed_files": changed,
          "checks": checks, "result": "pass" if ok else "fail"}
    hc.write_json(card.p("evidence.json"), ev)
    return ev, base


def cmd_review(a):
    card = Card(a.key)
    if not card.ready():
        return 1
    st = hc.read_json(card.p("state.json"), {"gate_runs": 0, "review_rounds": 0, "rejections": 0})
    if a.cont:
        st["rejections"], st["gate_runs"] = 0, 0
        st.setdefault("overrides", []).append({"at": now(), "text": " ".join(a.cont)})
        card.decide("ทำต่อ", " ".join(a.cont))
    max_rej, max_runs = card.cfg.get("max_rejections", 2), card.cfg.get("max_gate_runs", 6)
    if st["rejections"] >= max_rej or st["gate_runs"] >= max_runs:
        hc.write_json(card.p("state.json"), st)
        hc.say("ESCALATE", f"ครบจำนวนรอบแล้ว ดู {card.rel}/review.md และ evidence.json",
               decide_next(card.key), user=USER_ESCALATE)
        return 0
    st["gate_runs"] += 1
    ev, base = run_gate(card, st["gate_runs"])
    hc.write_json(card.p("state.json"), st)
    if not ev["changed_files"]:  # nothing for the agent to fix by retrying: stop instead of looping
        side = hc.worktree_changes(card.root)
        hc.say("BLOCKED", f"ไม่พบไฟล์ที่เปลี่ยนตาม scope_allow ของ {card.key} (เทียบกับ HEAD)"
               + ("\nไฟล์ที่เปลี่ยนแต่ไม่นับเป็นงานของการ์ด (อยู่ใน docs_allow และไม่อยู่ใน scope_allow):\n"
                  + "\n".join(f"- {f}" for f in side) if side else ""),
               "แสดงข้อความนี้ให้ผู้ใช้ตามตัวอักษร ห้ามแก้ git index ห้ามรัน review ซ้ำเอง แล้วหยุดรอคำสั่ง",
               user="ถ้ายังไม่ได้ลงมือทำ ให้สั่ง Gemini ทำงานต่อ ถ้าทำแล้วแต่ยังขึ้นข้อความนี้ ให้ถาม Claude Code ตรวจ")
        return 0
    if ev["result"] != "pass":
        bad = [f"- {c['name']}: exit {c['exit']} {c.get('note', '')} {c.get('outside_scope') or ''} "
               f"{('log: ' + card.rel + '/' + c['log']) if c.get('log') else ''}".rstrip()
               for c in ev["checks"] if c["exit"] != 0]
        hc.say("FAIL", "GATE (เครื่องตรวจ) ไม่ผ่าน:\n" + "\n".join(bad),
               f"แก้ตามนี้ (ห้ามแก้ spec.md หรือไฟล์ใน .arbiter/) แล้วรัน {SELF} review {card.key}")
        return 0
    diff = hc.diff_text(card.root, base, ev["tree"])
    if not diff.strip():
        hc.say("ERROR", "มีไฟล์เปลี่ยนแต่ได้ diff ว่าง (ตรวจ git config diff.* / .gitattributes)",
               "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    card.p("review-input.diff").write_text(diff, encoding="utf-8")
    st["review_rounds"] += 1
    rnd = st["review_rounds"]
    prev = render("review_prev.md", ROUND=rnd, CARD_DIR=card.rel) if rnd > 1 else ""
    data = card.claude(render("review.md", KEY=card.key, CARD_DIR=card.rel, ROUND=rnd, PREV_NOTE=prev,
                              LANG_RULE=card.lang_rule()), REVIEW_TOOLS, "review_model", "review")
    s = sections(data.get("result"))
    verdict = (s.get("VERDICT", "").split() or [""])[0].upper()
    if data.get("is_error") or verdict not in ("PASS", "FAIL", "SPEC_ISSUE"):
        st["review_rounds"] -= 1
        hc.write_json(card.p("state.json"), st)
        card.p("last-claude-output.txt").write_text(str(data.get("result")), encoding="utf-8")
        hc.say("ERROR", f"Claude review ไม่ได้ส่งผลตามรูปแบบ ดู {card.rel}/last-claude-output.txt",
               "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    change = s.get("SPEC_CHANGE", "") if verdict == "SPEC_ISSUE" else ""
    block = (f"\n## รอบ {rnd}: {verdict} ({now()}, tree {ev['tree'][:10]})\n"
             + (f"**SPEC_CHANGE (ข้อเสนอแก้ spec):**\n{change}\n\n" if change else "")
             + f"{s.get('FINDINGS', '')}\n\n"
             f"**ยังไม่ได้ทดสอบ:** {s.get('UNTESTED', '-')}\n\n**จุดที่ควรดู:** {s.get('LOOK_AT', '-')}\n")
    with card.p("review.md").open("a", encoding="utf-8") as fh:
        fh.write(block)
    ev["review"] = {"round": rnd, "verdict": verdict, "session_id": data.get("session_id")}
    hc.write_json(card.p("evidence.json"), ev)
    if verdict == "SPEC_ISSUE":  # the spec is the problem: fixing the work cannot end this, so it is not a rejection
        hc.write_json(card.p("state.json"), st)
        say_spec_issue(card, f"REVIEW รอบ {rnd}: ปัญหาอยู่ที่ spec ไม่ใช่ที่งาน", change, s.get("FINDINGS", ""))
        return 0
    if verdict == "PASS":
        page = gate2_page(card, ev, s)
        card.p("gate2.md").write_text(page, encoding="utf-8")
        hc.write_json(card.p("state.json"), st)
        hc.say("READY_FOR_GATE2", page,
               f'แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร แล้วรอ ถ้าผู้ใช้อนุมัติ ให้รัน '
               f'{SELF} commit {card.key} "<ข้อความอนุมัติของผู้ใช้ ห้ามแก้>" '
               f"ถ้าผู้ใช้สั่งแก้ ให้แก้ตามนั้นแล้วรัน {SELF} review {card.key}",
               user='ตรวจผลข้างบน แล้วพิมพ์ "อนุมัติ Gate 2" เพื่อ commit หรือพิมพ์สิ่งที่ต้องแก้')
        return 0
    st["rejections"] += 1
    hc.write_json(card.p("state.json"), st)
    if st["rejections"] >= max_rej:
        hc.say("ESCALATE", f"Reviewer ตีกลับครบ {max_rej} รอบ:\n{s.get('FINDINGS', '')}",
               decide_next(card.key), user=USER_ESCALATE)
    else:
        hc.say("FAIL", f"REVIEW รอบ {rnd} ไม่ผ่าน:\n{s.get('FINDINGS', '')}",
               f"แก้ตาม findings ข้างบน (ห้ามแก้ spec.md หรือไฟล์ใน .arbiter/) แล้วรัน {SELF} review {card.key} "
               f"ถ้าทำตาม finding หรือ spec แล้วขัดกับพฤติกรรมจริงของเครื่องมือ ห้ามหาทางเลี่ยง ให้รัน "
               f'{SELF} dispute {card.key} --claim "<ข้ออ้าง 1 บรรทัด>" --prove "<คำสั่งที่พิสูจน์>"')
    return 0


def say_spec_issue(card, title, proposal, detail):
    hc.say("SPEC_ISSUE", f"{title}\n\nข้อเสนอแก้ spec:\n{proposal or '-'}\n\nรายละเอียด:\n{detail or '-'}",
           decide_next(card.key),
           user='ถ้าเห็นด้วย พิมพ์ "แก้ spec ตามที่เสนอ" (หรือบอกว่าจะแก้อย่างไร) แล้วอนุมัติ Gate 1 ใหม่ · '
                'ถ้าไม่เห็นด้วย พิมพ์ "ทำต่อ: <คำตัดสินของคุณ>" · หรือ "พักการ์ด"')


def cmd_dispute(a):
    card = Card(a.key)
    if not card.ready():
        return 1
    st = hc.read_json(card.p("state.json"), {"gate_runs": 0, "review_rounds": 0, "rejections": 0})
    n, max_d = st.get("disputes", 0), card.cfg.get("max_disputes", 2)
    if n >= max_d:
        hc.say("ESCALATE", f"แย้งผลตรวจครบ {max_d} ครั้งแล้ว ดู {card.rel}/disputes.md และ review.md",
               decide_next(card.key), user=USER_ESCALATE)
        return 0
    claim, proves = " ".join(a.claim or []).strip(), a.prove or []
    errs = (["ไม่มีข้ออ้าง (--claim)"] if not claim else []) + \
        ([f"ต้องมีคำสั่งพิสูจน์ 1–{MAX_PROVE} คำสั่ง (--prove)"] if not 1 <= len(proves) <= MAX_PROVE else []) + \
        [f"คำสั่งพิสูจน์ห้ามมี | & ; < > ` : {p}" for p in proves if SHELL_META.search(p)]
    if errs:
        hc.say("BLOCKED", "dispute ไม่ถูกต้อง:\n" + "\n".join(f"- {e}" for e in errs),
               f'แก้แล้วรัน {SELF} dispute {card.key} --claim "<ข้ออ้าง 1 บรรทัด>" --prove "<คำสั่ง>"')
        return 1
    n += 1
    keep = hc.parse_block(card.spec_text())["scope_allow"]
    before = hc.code_tree(card.root, card.docs, keep)[0]
    rows = []
    for i, cmd in enumerate(proves, 1):  # arbiter runs the proof itself: the Architect reads output, not prose
        log = card.p(f"dispute-{n}-p{i}.log")
        code = run_cmd(cmd, card.root, log, card.cfg.get("verify_timeout_sec", 3600))
        rows.append(f"- `{cmd}` → exit {code} (log: `{card.rel}/{log.name}`)")
    moved = hc.code_tree(card.root, card.docs, keep)[0] != before
    entry = (f"\n## ข้อโต้แย้งครั้งที่ {n} ({now()})\n**ข้ออ้างของ Builder (ยังไม่ยืนยัน):** {claim}\n\n"
             f"**คำสั่งพิสูจน์ที่ arbiter รัน:**\n" + "\n".join(rows) + "\n"
             + ("\n⚠ คำสั่งพิสูจน์แก้ไฟล์งานระหว่างรัน\n" if moved else ""))
    with card.p("disputes.md").open("a", encoding="utf-8") as fh:
        fh.write(entry)
    st["disputes"] = n
    hc.write_json(card.p("state.json"), st)
    data = card.claude(render("dispute.md", KEY=card.key, CARD_DIR=card.rel, N=n, LANG_RULE=card.lang_rule()),
                       REVIEW_TOOLS, "review_model", "dispute")
    s = sections(data.get("result"))
    ruling = (s.get("RULING", "").split() or [""])[0].upper()
    if data.get("is_error") or ruling not in ("UPHOLD", "REVISE_SPEC", "NEED_HUMAN"):
        st["disputes"] = n - 1  # a broken call must not eat the dispute budget
        hc.write_json(card.p("state.json"), st)
        card.p("last-claude-output.txt").write_text(str(data.get("result")), encoding="utf-8")
        hc.say("ERROR", f"Claude ชี้ขาดไม่ได้ส่งผลตามรูปแบบ ดู {card.rel}/last-claude-output.txt",
               "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    reason, proposal = s.get("REASON", "-"), s.get("PROPOSAL", "-")
    with card.p("disputes.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n**ชี้ขาด: {ruling}**\n\n{reason}\n\n**ข้อเสนอ:**\n{proposal}\n")
    head = f"ข้อโต้แย้งครั้งที่ {n}: {claim} (ยังไม่ยืนยัน)\n" + "\n".join(rows) + f"\n\nชี้ขาด: {ruling}\n{reason}"
    if ruling == "UPHOLD":
        hc.say("DISPUTE_UPHELD", f"{head}\n\nทางที่ทำตามได้:\n{proposal}",
               f"{SHOW} แล้วแก้งานตามผลตรวจเดิมและทางข้างบน (ห้ามแก้ spec.md หรือไฟล์ใน .arbiter/) "
               f"แล้วรัน {SELF} review {card.key}",
               user='ไม่ต้องทำอะไร Gemini จะแก้ต่อ · ถ้าไม่เห็นด้วยกับผลชี้ขาด พิมพ์ "ทำต่อ: <คำตัดสิน>" หรือ "แก้ spec: ..."')
    elif ruling == "REVISE_SPEC":
        say_spec_issue(card, f"ข้อโต้แย้งครั้งที่ {n}: Architect เห็นว่าต้องแก้ spec", proposal, head)
    else:
        hc.say("NEEDS_DECISION", f"{head}\n\nทางเลือก:\n{proposal}", decide_next(card.key),
               user='เลือกทาง แล้วพิมพ์ "ทำต่อ: <ทางที่เลือก>" หรือ "แก้ spec: <สิ่งที่จะแก้>" หรือ "พักการ์ด"')
    return 0


def cmd_park(a):
    card = Card(a.key)
    text = " ".join(a.text)
    hc.write_json(card.p("parked.json"), {"at": now(), "text": text})
    card.decide("พักการ์ด", text)
    if hc.active_key(card.root) == card.key:
        (hc.hdir(card.root) / "ACTIVE").unlink(missing_ok=True)
    hc.say("PARKED", f"พักการ์ด {card.key} แล้ว งานและไฟล์ทั้งหมดยังอยู่ครบ", f"{SHOW} แล้วหยุด ห้ามแก้งานของการ์ดนี้ต่อ",
           user=f'กลับมาทำต่อเมื่อพร้อม พิมพ์ "ทำการ์ด {card.key} ต่อ"')
    return 0


def cmd_unpark(a):
    card = Card(a.key)
    if not card.p("parked.json").exists():
        hc.say("INFO", f"การ์ด {card.key} ไม่ได้ถูกพักไว้", f"รัน {SELF} status {card.key} แล้วทำตาม NEXT")
        return 0
    card.p("parked.json").unlink()
    card.decide("ทำการ์ดต่อ", " ".join(a.text))
    (hc.hdir(card.root) / "ACTIVE").write_text(card.key, encoding="utf-8")
    hc.say("UNPARKED", f"การ์ด {card.key} กลับมาทำงานต่อ", f"รัน {SELF} status {card.key} แล้วทำตาม NEXT")
    return 0


def gate2_page(card, ev, s):
    title = next((ln.lstrip("# ").strip() for ln in card.spec_text().splitlines() if ln.startswith("# ")), card.key)
    rows = "\n".join(f"| {c['name']} | {c['exit']} | {c.get('seconds', '-')} |" for c in ev["checks"])
    files = "\n".join(f"- {f}" for f in ev["changed_files"])
    return (f"# Gate 2: {title}\n\nโค้ดที่ตรวจ: tree `{ev['tree'][:12]}` (base `{(ev['base'] or 'none')[:10]}`) · "
            f"spec: {card.rel}/spec.md\n\n## ไฟล์ที่เปลี่ยน ({len(ev['changed_files'])})\n{files}\n\n"
            f"## ผลตรวจจากเครื่อง\n| check | exit | วินาที |\n|---|---|---|\n{rows}\n\n"
            f"## Review รอบ {ev['review']['round']}: {ev['review']['verdict']}\n{s.get('FINDINGS', '')}\n\n"
            f"**ยังไม่ได้ทดสอบ:** {s.get('UNTESTED', '-')}\n\n**จุดที่ควรดู:** {s.get('LOOK_AT', '-')}\n")


def cmd_commit(a):
    card = Card(a.key)
    if card.p("parked.json").exists():
        hc.say("BLOCKED", f"การ์ด {card.key} ถูกพักไว้", "แจ้งผู้ใช้ แล้วหยุดรอคำสั่ง",
               user=f'ถ้าจะทำต่อ พิมพ์ "ทำการ์ด {card.key} ต่อ"')
        return 1
    ev = hc.read_json(card.p("evidence.json"), {})
    if ev.get("result") != "pass" or ev.get("review", {}).get("verdict") != "PASS":
        hc.say("BLOCKED", "ยังไม่มี evidence ที่ผ่านทั้ง gate และ review", f"รัน {SELF} review {card.key}",
               user="ยังไม่ต้องทำอะไร งานยังไม่ผ่าน review Gemini จะรัน review ต่อ")
        return 1
    # Re-check everything cheap ourselves instead of trusting the JSON files.
    spec = hc.approved_spec(card.root, card.key)
    if spec is None or ev.get("spec_sha256") != hc.sha256(card.p("spec.md")):
        hc.say("BLOCKED", "spec ไม่ตรงกับที่อนุมัติ Gate 1 หรือไม่ตรงกับตอน review",
               "แจ้งผู้ใช้ว่าต้องอนุมัติ spec ใหม่และ review ใหม่ แล้วหยุดรอคำสั่ง")
        return 1
    keep = hc.parse_block(spec)["scope_allow"]
    tree, base = hc.code_tree(card.root, card.docs, keep)
    drift = hc.code_drift(card.root, ev["tree"], tree, card.docs, keep)  # side docs and .arbiter/ may move freely
    if drift:
        hc.say("STALE", "ไฟล์เหล่านี้เปลี่ยนหลังจาก review ผ่านแล้ว:\n" + "\n".join(f"- {f}" for f in drift),
               f"แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร ห้ามแก้ ย้อน stash หรือลบไฟล์เหล่านี้เอง แล้วรอ "
               f"ถ้าผู้ใช้สั่งให้ตรวจใหม่ ให้รัน {SELF} review {card.key}",
               user='ดูว่าไฟล์ข้างบนใครแก้ ถ้าแก้ได้ พิมพ์ "review ใหม่" เพื่อตรวจรอบใหม่ ถ้าไม่ควรแก้ ให้บอก Gemini ว่าจะจัดการอย่างไร')
        return 0
    outside = [f for f in hc.changed_files(card.root, base, tree) if not hc.in_scope(f, keep)]
    if outside:
        hc.say("BLOCKED", "มีไฟล์นอก scope_allow: " + ", ".join(outside), "แจ้งผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    ap = hc.read_json(card.p("approvals.json"), {})
    ap["gate2"] = {"at": now(), "text": " ".join(a.text), "tree": tree}
    hc.write_json(card.p("approvals.json"), ap)
    title = next((ln.lstrip("# ").strip() for ln in card.spec_text().splitlines() if ln.startswith("# ")), card.key)
    title = re.sub(rf"^{re.escape(card.key)}\s*:\s*", "", title)
    (hc.hdir(card.root) / "CURRENT_STATE.md").write_text(
        f"# Current state\n\n- การ์ดล่าสุดที่ commit: {card.key} ({now()})\n- spec: {card.rel}/spec.md\n"
        f"- สิ่งที่ยังไม่ได้ทดสอบ: ดู {card.rel}/gate2.md\n", encoding="utf-8")
    msg = (a.message or f"feat({card.key.lower()}): {title}") + \
        f"\n\nArbiter-Card: {card.key}\nArbiter-Tree: {tree}\nArbiter-Gate2: {ap['gate2']['at']}\n"
    hc.git("add", "-A", "--", ".", f":(exclude){hc.SPIKES_REL}", cwd=card.root)  # spikes commit themselves
    pending_docs = hc.unstage_docs(card.root, card.docs, keep=keep)  # side docs go in their own commit
    staged = hc.git("write-tree", cwd=card.root).strip()
    if not hc.same_code(card.root, staged, tree, card.docs, keep):  # assume-unchanged, skip-worktree, force-added...
        hc.git("reset", "-q", cwd=card.root)
        hc.say("ERROR", "สิ่งที่ stage อยู่ใน git index ไม่ตรงกับโค้ดที่ review ผ่าน (มีไฟล์ที่ถูก force-add, "
               "assume-unchanged หรือ skip-worktree) จึงยกเลิก commit และ unstage แล้ว",
               "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    r = hc.git("commit", "-F", "-", cwd=card.root, check=False, stdin=msg,
               env=dict(os.environ, **{hc.COMMIT_ENV: tree}))
    if r.returncode != 0:
        hc.say("ERROR", f"git commit ไม่ผ่าน:\n{r.stdout}{r.stderr}", "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    (hc.hdir(card.root) / "ACTIVE").unlink(missing_ok=True)
    sha = hc.git("rev-parse", "--short", "HEAD", cwd=card.root).strip()
    body, user = f"commit {sha}: {card.key}", "ไม่ต้องทำอะไร งานนี้เสร็จแล้ว"
    if card.local:
        nxt = "แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร การ์ด LOCAL ไม่มีบน Jira ห้ามแตะ Jira"
    else:
        nxt = (f"แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร แล้วขยับการ์ด {card.key} บน Jira ตามกฎ lifecycle "
               f"(Story ไป UAT / QA, Task ไป Done)")
        user = f"ไม่ต้องทำอะไร Gemini จะขยับการ์ด {card.key} บน Jira ให้"
    if pending_docs:
        body += "\n\nเอกสารนอกการ์ดที่เปลี่ยนแต่ไม่ได้รวมใน commit นี้:\n" + "\n".join(f"- {f}" for f in pending_docs)
        user += " · เอกสารข้างบนยังไม่ได้ commit ถ้าต้องการ ให้สั่ง Gemini หรือ commit เอง (ไม่ต้องใช้ --no-verify)"
    hc.say("COMMITTED", body, nxt, user=user)
    return 0


def cmd_status(a):
    root = hc.repo_root()
    key = (a.key or hc.active_key(root) or "").upper()
    if not key:
        hc.say("INFO", "ไม่มีการ์ด ACTIVE",
               f'เริ่มการ์ดด้วย {SELF} spec <รหัสการ์ด> หรืองานที่ไม่มีการ์ด {SELF} spec --local "<โจทย์>"')
        return 0
    card = Card(key)
    files = sorted(p.name for p in card.dir.iterdir()) if card.dir.exists() else []
    ap = hc.read_json(card.p("approvals.json"), {})
    st = hc.read_json(card.p("state.json"), {})
    ev = hc.read_json(card.p("evidence.json"), {})
    body = (f"การ์ด: {key} (ACTIVE: {hc.active_key(root)})\nไฟล์: {', '.join(files) or '-'}\n"
            f"Gate 1: {'ผ่าน' if 'gate1' in ap else '-'} · Gate 2: {'ผ่าน' if 'gate2' in ap else '-'}\n"
            f"state: {st or '-'}\nevidence ล่าสุด: {ev.get('result', '-')} / review: {ev.get('review', {}).get('verdict', '-')}")
    if card.p("parked.json").exists():
        body += f"\nพักไว้: {hc.read_json(card.p('parked.json'), {}).get('at')}"
        nxt = f'การ์ดถูกพักไว้ ถ้าผู้ใช้สั่งทำต่อ ให้รัน {SELF} unpark {key} "<ข้อความของผู้ใช้>"'
    elif card.p("questions.md").exists():
        nxt = f'มีคำถามค้าง แสดง {card.rel}/questions.md ให้ผู้ใช้ แล้วรัน {SELF} answer {key} "<ข้อความของผู้ใช้>"'
    elif "gate1" not in ap:
        nxt = f"แสดง {card.rel}/spec.md ให้ผู้ใช้เพื่ออนุมัติ Gate 1"
    else:
        nxt = f"ทำงานต่อตาม spec แล้วรัน {SELF} review {key}"
    hc.say("INFO", body, nxt)
    return 0


def main():
    if sys.argv[1:2] == ["spike"]:
        import arbiter_spike
        return arbiter_spike.main(sys.argv[2:])
    ap = argparse.ArgumentParser(prog="arbiter", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spec")
    sp.add_argument("key", nargs="?")
    sp.add_argument("--local", nargs="+", help="งานที่ไม่มีการ์ด Jira: ข้อความโจทย์ของผู้ใช้ตามตัวอักษร")
    sp.add_argument("--model", choices=("haiku", "sonnet", "opus"), help="เฉพาะเมื่อผู้ใช้สั่งเอง (ค่าตั้งต้นจาก config)")
    sp.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), help="เฉพาะเมื่อผู้ใช้สั่งเอง")
    dp = sub.add_parser("dispute", help="แย้งผลตรวจด้วยหลักฐานจากคำสั่ง แทนการหาทางเลี่ยง")
    dp.add_argument("key")
    dp.add_argument("--claim", nargs="+", help="ข้ออ้าง 1 บรรทัด (ถือว่ายังไม่ยืนยัน)")
    dp.add_argument("--prove", action="append", help="คำสั่งที่ arbiter จะรันเป็นหลักฐาน (ใส่ได้หลายครั้ง)")
    for name in ("answer", "approve", "commit", "park", "unpark"):
        sp = sub.add_parser(name)
        sp.add_argument("key")
        sp.add_argument("text", nargs="+")
        if name == "commit":
            sp.add_argument("-m", "--message")
    rv = sub.add_parser("review")
    rv.add_argument("key")
    rv.add_argument("--continue", dest="cont", nargs="+")
    sub.add_parser("status").add_argument("key", nargs="?")
    a = ap.parse_args()
    try:
        return {"spec": cmd_spec, "answer": cmd_answer, "approve": cmd_approve, "review": cmd_review,
                "commit": cmd_commit, "status": cmd_status, "dispute": cmd_dispute, "park": cmd_park,
                "unpark": cmd_unpark}[a.cmd](a)
    except Exception as e:  # report through the same channel the agent reads
        hc.say("ERROR", f"{type(e).__name__}: {e}", "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1


if __name__ == "__main__":
    sys.exit(main())
