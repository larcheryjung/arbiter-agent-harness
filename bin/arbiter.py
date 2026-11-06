#!/usr/bin/env python3
"""arbiter v0: /feature flow  spec -> answer -> approve -> review -> commit.

The orchestrating agent (Gemini) only passes pointers (card key) and the user's verbatim words.
Prompts to Claude are built here from fixed templates; verdicts come from this script.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arbiter_core as hc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
SELF = f'python "{Path(__file__).resolve()}"'
STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
             "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED")
BUILTIN_TOOLS = "Read,Grep,Glob"  # --tools: the ONLY built-ins that exist in the session (no Bash/Edit/Write)
SPEC_TOOLS = "Read,Grep,Glob,mcp__jira__jira_get_issue,mcp__jira__jira_search"
LOCAL_SPEC_TOOLS = BUILTIN_TOOLS  # LOCAL cards have no Jira card, so no Jira access at all
REVIEW_TOOLS = BUILTIN_TOOLS
# Written in the target language itself: a model reading a Thai prompt drifts back to Thai otherwise.
LANG_RULES = {
    "th": "สำคัญ: เขียนเนื้อหาทั้งหมดเป็นภาษาไทย ชื่อไฟล์ คลาส เมธอด คำสั่ง และศัพท์เทคนิคใช้ตามต้นฉบับ "
          "marker `===...===` และบล็อก ```arbiter ห้ามแปล",
    "en": "IMPORTANT: Write all prose in English, even though these instructions are in Thai. Keep file, class "
          "and method names, commands and the `===...===` markers and ```arbiter block exactly as they are.",
}
USER_ESCALATE = 'ตัดสินว่าจะให้ทำต่อหรือหยุด ถ้าให้ทำต่อ พิมพ์ "ทำต่อ" พร้อมแนวทางที่ต้องการ'
THAI = re.compile(r"[฀-๿]")
LATIN = re.compile(r"[A-Za-z]")
DENY_TOOLS = "Edit,Write,NotebookEdit,Bash,PowerShell"
SHELL_META = re.compile(r"[|&;<>`]")


def now():
    return datetime.now().isoformat(timespec="seconds")


def render(name, **kw):
    text = (hc.HOME / "prompts" / name).read_text(encoding="utf-8")
    for k, v in kw.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def sections(text):
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = re.fullmatch(r"===([A-Z_]+)===", line.strip())
        if m:
            cur = m.group(1)
            if cur == "END":
                break
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


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
        return render("spec.md", SOURCE=source, KEY=self.key, CARD_DIR=self.rel, LANG_RULE=self.lang_rule())

    def lang_rule(self):
        code = self.lang()
        return LANG_RULES.get(code, f"IMPORTANT: Write all prose in the language with code '{code}'.")

    def lang(self):
        """Language code of the user's latest decisive words on this card (intent, answers), else `language`."""
        texts = [self.p("intent.md").read_text(encoding="utf-8")] if self.p("intent.md").exists() else []
        if self.p("answers.md").exists():
            texts += re.split(r"^## .*$", self.p("answers.md").read_text(encoding="utf-8"), flags=re.M)
        code = self.cfg.get("language", "th")
        for t in reversed(texts):
            if THAI.search(t):
                code = "th"
                break
            if len(LATIN.findall(t)) >= 20:  # short replies like "1a 2b" decide nothing
                code = "en"
                break
        return code

    def claude(self, prompt, tools, model_key, mode, resume=None):
        override = os.environ.get("ARBITER_CLAUDE_CMD")
        cmd = override.split("|") if override else [shutil.which("claude") or "claude"]
        c = self.cfg.get("claude", {})
        args = cmd + ["-p", "--output-format", "stream-json", "--verbose",
                      "--model", os.environ.get("ARBITER_MODEL") or c.get(model_key, "opus")]
        if c.get("effort"):
            args += ["--effort", c["effort"]]
        if resume:
            args += ["--resume", resume]
        args += ["--permission-mode", "default", "--tools", BUILTIN_TOOLS,
                 "--disallowedTools", DENY_TOOLS, "--allowedTools", tools]
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        r = subprocess.run(args, cwd=self.root, input=prompt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=c.get("timeout_sec", 1200))
        data = parse_stream(r.stdout) or {
            "is_error": True, "result": f"(exit {r.returncode}) {(r.stderr or r.stdout)[-800:]}", "tool_calls": []}
        data["jira_read"] = any(t["ok"] and t["name"].endswith("jira_get_issue")
                                and str(t["input"].get("issue_key", "")).upper() == self.key
                                for t in data["tool_calls"])
        with self.p("sessions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now(), "mode": mode, "session_id": data.get("session_id"),
                                 "resumed": resume, "is_error": bool(data.get("is_error")),
                                 "cost_usd": data.get("total_cost_usd"),
                                 "denials": len(data.get("permission_denials") or []),
                                 "jira_read": data["jira_read"]}) + "\n")
        return data

    def require_jira(self):
        return not self.local and self.cfg.get("claude", {}).get("require_jira", True)

    def last_session(self, modes):
        p = self.p("sessions.jsonl")
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
        rows = [r for r in rows if r["mode"] in modes and r.get("session_id") and not r.get("is_error")]
        return rows[-1]["session_id"] if rows else None


def parse_stream(out):
    """Fold `claude -p --output-format stream-json` into the final result event + every tool call and its outcome."""
    calls, result = {}, None
    for line in (out or "").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (ev.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_use":
                calls[c.get("id")] = {"name": c.get("name", ""), "input": c.get("input") or {}, "ok": False}
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in calls:
                calls[c["tool_use_id"]]["ok"] = not c.get("is_error")
        if ev.get("type") == "result":
            result = ev
    if result is not None:
        result["tool_calls"] = list(calls.values())
    return result


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
    ap["gate1"] = {"at": now(), "text": " ".join(a.text), "spec_sha256": hc.sha256(card.p("spec.md"))}
    hc.write_json(card.p("approvals.json"), ap)
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
    tree, base = hc.code_tree(card.root, card.docs)
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
    if hc.code_tree(card.root, card.docs)[0] != tree:  # verify must test exactly the recorded tree
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
    ap = hc.read_json(card.p("approvals.json"), {})
    if "gate1" not in ap:
        hc.say("BLOCKED", f"{card.key} ยังไม่ผ่าน Gate 1", "แจ้งผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    if hc.sha256(card.p("spec.md")) != ap["gate1"]["spec_sha256"]:
        hc.say("BLOCKED", "spec.md ถูกแก้หลังอนุมัติ Gate 1", "แจ้งผู้ใช้ว่าต้องอนุมัติ spec ใหม่ แล้วหยุดรอคำสั่ง")
        return 1
    st = hc.read_json(card.p("state.json"), {"gate_runs": 0, "review_rounds": 0, "rejections": 0})
    if a.cont:
        st["rejections"], st["gate_runs"] = 0, 0
        st.setdefault("overrides", []).append({"at": now(), "text": " ".join(a.cont)})
    max_rej, max_runs = card.cfg.get("max_rejections", 2), card.cfg.get("max_gate_runs", 6)
    if st["rejections"] >= max_rej or st["gate_runs"] >= max_runs:
        hc.write_json(card.p("state.json"), st)
        hc.say("ESCALATE", f"ครบจำนวนรอบแล้ว ดู {card.rel}/review.md และ evidence.json",
               f'แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร หยุดแก้เอง ให้ผู้ใช้ตัดสิน ถ้าผู้ใช้ให้ทำต่อ ให้รัน '
               f'{SELF} review {card.key} --continue "<ข้อความของผู้ใช้>"', user=USER_ESCALATE)
        return 0
    st["gate_runs"] += 1
    ev, base = run_gate(card, st["gate_runs"])
    hc.write_json(card.p("state.json"), st)
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
    if data.get("is_error") or verdict not in ("PASS", "FAIL"):
        st["review_rounds"] -= 1
        hc.write_json(card.p("state.json"), st)
        card.p("last-claude-output.txt").write_text(str(data.get("result")), encoding="utf-8")
        hc.say("ERROR", f"Claude review ไม่ได้ส่งผลตามรูปแบบ ดู {card.rel}/last-claude-output.txt",
               "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1
    block = (f"\n## รอบ {rnd}: {verdict} ({now()}, tree {ev['tree'][:10]})\n{s.get('FINDINGS', '')}\n\n"
             f"**ยังไม่ได้ทดสอบ:** {s.get('UNTESTED', '-')}\n\n**จุดที่ควรดู:** {s.get('LOOK_AT', '-')}\n")
    with card.p("review.md").open("a", encoding="utf-8") as fh:
        fh.write(block)
    ev["review"] = {"round": rnd, "verdict": verdict, "session_id": data.get("session_id")}
    hc.write_json(card.p("evidence.json"), ev)
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
               f'แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร หยุดแก้เอง ให้ผู้ใช้ตัดสิน ถ้าผู้ใช้ให้ทำต่อ ให้รัน '
               f'{SELF} review {card.key} --continue "<ข้อความของผู้ใช้>"', user=USER_ESCALATE)
    else:
        hc.say("FAIL", f"REVIEW รอบ {rnd} ไม่ผ่าน:\n{s.get('FINDINGS', '')}",
               f"แก้ตาม findings ข้างบน (ห้ามแก้ spec.md หรือไฟล์ใน .arbiter/) แล้วรัน {SELF} review {card.key}")
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
    tree, base = hc.code_tree(card.root, card.docs)
    drift = hc.code_drift(card.root, ev["tree"], tree, card.docs)  # docs and .arbiter/ may move freely
    if drift:
        hc.say("STALE", "ไฟล์เหล่านี้เปลี่ยนหลังจาก review ผ่านแล้ว:\n" + "\n".join(f"- {f}" for f in drift),
               f"แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร ห้ามแก้ ย้อน stash หรือลบไฟล์เหล่านี้เอง แล้วรอ "
               f"ถ้าผู้ใช้สั่งให้ตรวจใหม่ ให้รัน {SELF} review {card.key}",
               user='ดูว่าไฟล์ข้างบนใครแก้ ถ้าแก้ได้ พิมพ์ "review ใหม่" เพื่อตรวจรอบใหม่ ถ้าไม่ควรแก้ ให้บอก Gemini ว่าจะจัดการอย่างไร')
        return 0
    scope = hc.parse_block(spec)["scope_allow"]
    outside = [f for f in hc.changed_files(card.root, base, tree) if not hc.in_scope(f, scope)]
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
    hc.git("add", "-A", cwd=card.root)
    pending_docs = hc.unstage_docs(card.root, card.docs)  # docs go in their own commit, never the card's
    staged = hc.git("write-tree", cwd=card.root).strip()
    if not hc.same_code(card.root, staged, tree, card.docs):  # assume-unchanged, skip-worktree, force-added...
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
    if card.p("questions.md").exists():
        nxt = f'มีคำถามค้าง แสดง {card.rel}/questions.md ให้ผู้ใช้ แล้วรัน {SELF} answer {key} "<ข้อความของผู้ใช้>"'
    elif "gate1" not in ap:
        nxt = f"แสดง {card.rel}/spec.md ให้ผู้ใช้เพื่ออนุมัติ Gate 1"
    else:
        nxt = f"ทำงานต่อตาม spec แล้วรัน {SELF} review {key}"
    hc.say("INFO", body, nxt)
    return 0


def main():
    ap = argparse.ArgumentParser(prog="arbiter", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spec")
    sp.add_argument("key", nargs="?")
    sp.add_argument("--local", nargs="+", help="งานที่ไม่มีการ์ด Jira: ข้อความโจทย์ของผู้ใช้ตามตัวอักษร")
    for name in ("answer", "approve", "commit"):
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
                "commit": cmd_commit, "status": cmd_status}[a.cmd](a)
    except Exception as e:  # report through the same channel the agent reads
        hc.say("ERROR", f"{type(e).__name__}: {e}", "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง")
        return 1


if __name__ == "__main__":
    sys.exit(main())
