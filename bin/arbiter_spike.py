"""arbiter spike: refine a large requirement with the user into an approved plan, then an approved card list.

No code is written. Claude (read-only) talks through REPLY/SOLUTION/QUESTIONS, then CARDS (JSON) that this script
validates. Two human approvals: the plan (solution.md) and the card list (cards.json), each pinned by sha256.
Gemini creates the Jira cards; `link` records the keys and commits the spike folder.
"""
import argparse
import json
import re
import shutil
from pathlib import Path

import arbiter_core as hc
import arbiter_board
import arbiter_claude as ac
import arbiter_extract as ax
from arbiter_claude import now

SELF = f'python "{Path(__file__).resolve().parent / "arbiter.py"}" spike'
SPIKE_RE = re.compile(r"SPIKE-(\d+)")
TOOLS = f"{ac.BUILTIN_TOOLS},{ac.JIRA_READ_TOOLS}"  # Jira read: find existing epics/cards in KS
DEFAULTS = {"model": "opus", "effort": "high", "repos": {}, "read_dirs": [], "max_points": 8,
            "max_rounds_resume": 6, "budget_usd": 15}
POINTS, TYPES = (1, 2, 3, 5, 8), ("Story", "Task")
MODELS, EFFORTS = ("haiku", "sonnet", "opus"), ("low", "medium", "high")  # what a card may recommend (no fable)
SHOW = "แสดงข้อความข้างบนให้ผู้ใช้ตามตัวอักษร"
STOP = "แสดงข้อความนี้ให้ผู้ใช้ แล้วหยุดรอคำสั่ง"


class Spike:
    def __init__(self, key, root=None):
        self.root = root or hc.repo_root()
        self.cfg = hc.load_config(self.root)
        self.s = {**DEFAULTS, **self.cfg.get("spike", {})}
        self.key = key.upper()
        self.local = bool(SPIKE_RE.fullmatch(self.key))
        if not self.local and not re.fullmatch(self.cfg.get("key_pattern", r"KS-\d+"), self.key):
            raise RuntimeError(f"รหัส {self.key} ไม่ตรงกับ key_pattern และไม่ใช่ SPIKE-n")
        self.dir = self.root / hc.SPIKES_REL / self.key
        self.rel = self.dir.relative_to(self.root).as_posix()

    def p(self, name):
        return self.dir / name

    def text(self, name):
        return self.p(name).read_text(encoding="utf-8") if self.p(name).exists() else ""

    def lang(self):
        texts = [self.text("intent.md")] + re.split(r"^## .*$", self.text("answers.md"), flags=re.M)
        return ac.lang_rule(ac.lang_of(texts, self.cfg.get("language", "th")))

    def approved(self, gate, name):
        a = hc.read_json(self.p("approvals.json"), {}).get(gate)
        return bool(a) and self.p(name).exists() and a.get("sha256") == hc.sha256(self.p(name))

    def plan_ok(self):
        return self.approved("solution", "solution.md")

    def cards_ok(self):
        return self.plan_ok() and self.approved("cards", "cards.json")

    def repos(self):
        return {self.root.name: str(self.root), **self.s["repos"]}

    def title(self):
        t = next((ln.lstrip("# ").strip() for ln in self.text("solution.md").splitlines() if ln.startswith("# ")), "")
        return re.sub(rf"^{re.escape(self.key)}\s*:\s*", "", t) or self.key

    def call(self, prompt, mode, resume=None):
        return ac.call(self.root, self.cfg.get("claude", {}), prompt, TOOLS, self.s["model"], effort=self.s["effort"],
                       log=self.p("sessions.jsonl"), mode=mode, key=self.key, resume=resume,
                       add_dirs=self.s["read_dirs"])

    def session(self):
        """Session to resume, or None: start fresh from the files once a session was resumed max_rounds_resume times."""
        rows = [r for r in ac.session_rows(self.p("sessions.jsonl")) if r.get("session_id") and not r["is_error"]]
        if not self.local and self.cfg.get("claude", {}).get("require_jira", True) and \
                not any(r.get("jira_read") for r in rows):
            return None  # no session has read the Jira card yet: never build on one, start fresh and verify again
        streak = 0
        for r in reversed(rows):
            if not r.get("resumed"):
                break
            streak += 1
        return rows[-1]["session_id"] if rows and streak < self.s["max_rounds_resume"] else None

    def common(self, answer):
        dirs = self.s["read_dirs"]
        note = f'## ข้อความล่าสุดของผู้ใช้ (ตามที่พิมพ์จริง)\n"""\n{answer}\n"""\n' if answer else ""
        return dict(KEY=self.key, DIR=self.rel, ANSWER_NOTE=note,
                    READ_DIRS=(" และใน repo/โฟลเดอร์ที่ผู้ใช้อนุญาตให้อ่าน: " + ", ".join(f"`{d}`" for d in dirs))
                    if dirs else "",
                    FORMAT=ac.render("spike_format.md", KEY=self.key, LANG_RULE=self.lang()))

    def discuss_prompt(self, answer=""):
        source = (ac.render("source_jira.md", KEY=self.key, CARD_DIR=self.rel) if not self.local else
                  "spike นี้ไม่มีการ์ดใน Jira โจทย์คือ intent.md และไฟล์ใน inputs/ ตามข้อ 1\n")
        return ac.render("spike_discuss.md", SOURCE=source, **self.common(answer))

    def cards_prompt(self, answer=""):
        return ac.render("spike_cards.md", MAX_POINTS=self.s["max_points"],
                         REPOS=", ".join(f"`{n}`" for n in self.repos()), **self.common(answer))


# ---------- validation of CARDS ----------
def repo_files(path, cache={}):  # noqa: B006 - per-process cache
    if path not in cache:
        r = hc.git("ls-files", "-z", cwd=path, check=False) if Path(path).is_dir() else None
        cache[path] = hc.nul_list(r.stdout) if r is not None and r.returncode == 0 else None
    return cache[path]


def path_exists(files, pat):
    pat = pat.replace("\\", "/").strip().lstrip("./")
    rx, prefix = hc.glob_re(pat), pat.rstrip("/") + "/"
    return any(rx.fullmatch(f) or f.startswith(prefix) for f in files)


def validate(sp, raw):
    """(cards dict or None, errors, warnings). Errors go back to Claude; warnings are shown to the human."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    try:
        d = json.loads(t[t.find("{"):t.rfind("}") + 1])
    except ValueError as e:
        return None, [f"CARDS ไม่ใช่ JSON ที่ถูกต้อง: {e}"], []
    cards = d.get("cards") if isinstance(d, dict) else None
    if not isinstance(cards, list) or not cards or not all(isinstance(c, dict) for c in cards):
        return None, ["ต้องมี cards เป็นรายการ object อย่างน้อย 1 ใบ"], []
    errors, warns, kp = [], [], sp.cfg.get("key_pattern", r"KS-\d+")
    epic, release = d.get("epic"), d.get("release")  # both optional: Claude recommends, the human decides
    if isinstance(epic, str):
        if not re.fullmatch(kp, epic):
            errors.append(f"epic {epic!r} ไม่ใช่รหัสการ์ดตาม key_pattern")
    elif epic is not None and not (isinstance(epic, dict) and str(epic.get("title", "")).strip()):
        errors.append('epic ต้องเป็นรหัสการ์ดเดิม, {"title", "goal"} หรือ null')
    if release is not None and not (isinstance(release, str) and release.strip()):
        errors.append("release ต้องเป็นชื่อ หรือ null")
    elif release and re.search(r"v?\d+\.\d+(\.\d+)?$", release):
        warns.append(f"release `{release}` ดูเหมือนเลขเวอร์ชันของ service (กฎ: ตั้งชื่อตามฟีเจอร์)")
    ids = [str(c.get("id")) for c in cards]
    errors += [f"id ซ้ำ: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    repos = sp.repos()
    for c in cards:
        cid = str(c.get("id"))
        if not re.fullmatch(r"C\d+", cid):
            errors.append(f"{cid}: id ต้องเป็น C1, C2, ...")
        if c.get("type") not in TYPES:
            errors.append(f"{cid}: type ต้องเป็น Story หรือ Task")
        if not str(c.get("title", "")).strip():
            errors.append(f"{cid}: ไม่มี title")
        if not (isinstance(c.get("ac"), list) and c["ac"] and all(str(x).strip() for x in c["ac"])):
            errors.append(f"{cid}: ต้องมี AC อย่างน้อย 1 ข้อ")
        if c.get("points") not in POINTS or c["points"] > sp.s["max_points"]:
            errors.append(f"{cid}: แต้ม {c.get('points')!r} ต้องเป็น 1, 2, 3, 5, 8 และไม่เกิน {sp.s['max_points']} "
                          f"(ใหญ่กว่านั้นให้ซอยเป็นการ์ดเล็กที่ยังส่งมอบได้)")
        if c.get("model") not in MODELS or c.get("effort") not in EFFORTS:
            errors.append(f"{cid}: model ต้องเป็น {'/'.join(MODELS)} และ effort ต้องเป็น {'/'.join(EFFORTS)}")
        deps = c.get("depends") or []
        errors += [f"{cid}: depends ชี้ไปที่ {x} ซึ่งไม่มีอยู่" for x in deps if str(x) not in ids]
        repo = c.get("repo")
        if repo not in repos:
            errors.append(f"{cid}: repo {repo!r} ต้องเป็นหนึ่งใน {', '.join(repos)}")
            continue
        files = repo_files(repos[repo])
        for h in c.get("scope_hint") or []:
            if files is None:
                warns.append(f"{cid}: ตรวจ scope_hint ไม่ได้ ({repos[repo]} ไม่ใช่ git repo)")
                break
            if not str(h).startswith("new:") and not path_exists(files, str(h)):
                errors.append(f"{cid}: scope_hint `{h}` ไม่พบใน {repo} (ของใหม่ให้ขึ้นต้นด้วย new:)")
    if not errors and has_cycle({str(c["id"]): [str(x) for x in c.get("depends") or []] for c in cards}):
        errors.append("depends วนเป็นวง")
    return d, errors, warns


def has_cycle(graph):
    state = {}

    def visit(n):
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        found = any(visit(m) for m in graph.get(n, []))
        state[n] = 2
        return found
    return any(visit(n) for n in graph)


def render_cards(sp, d):
    cards, epic, release = d["cards"], d.get("epic"), d.get("release")
    epic_line = (f"Epic: {epic} (เดิม)" if isinstance(epic, str) else
                 f"Epic: **E** · {epic['title']} (ใหม่) · {epic.get('goal', '')}" if epic else "Epic: - (ไม่ผูก epic)")
    stories = sum(c["type"] == "Story" for c in cards)
    rows = "\n".join(f"| {c['id']} | {c['type']} | {c['title']} | {c['points']} | {c['model']} · {c['effort']} | "
                     f"{c['repo']} | {', '.join(map(str, c.get('depends') or [])) or '-'} |" for c in cards)
    out = [f"# การ์ดของ {sp.key}: {sp.title()}", "",
           epic_line, f"Release: {release or '- (ไม่ใส่ fix version)'}",
           f"รวม {len(cards)} ใบ · Story {stories} · Task {len(cards) - stories} · "
           f"แต้มรวม {sum(c['points'] for c in cards)}"]
    if not stories:
        out.append("ไม่มี Story: ไม่มีการ์ดใดเข้า UAT / QA ให้ BA ตรวจ ทุกใบไป Done หลัง Gate 2")
    out += ["",
           "| id | ประเภท | ชื่อ | แต้ม | AI (spec/review) | repo | ต้องเสร็จก่อน |", "|---|---|---|---|---|---|---|",
           rows]
    for c in cards:
        out += ["", f"## {c['id']} · {c['type']} · {c['title']}", "", f"**เป้าหมาย:** {c.get('goal', '')}", "",
                "**Acceptance Criteria**"] + [f"- {x}" for x in c["ac"]]
        if c.get("scope_hint"):
            out += ["", "**ขอบเขตโดยประมาณ:** " + ", ".join(f"`{h}`" for h in c["scope_hint"])]
        if c.get("subtasks"):
            out += ["", "**ขั้นตอนย่อย (subtask ไม่มีแต้ม):**"] + [f"- {x}" for x in c["subtasks"]]
        out += ["", f"**ต้องเสร็จก่อน:** {', '.join(map(str, c.get('depends') or [])) or '-'} · "
                    f"**AI:** spec/review ด้วย {c['model']} · {c['effort']}",
                f"**ที่มา:** spike {sp.key} · `{sp.rel}/` · การ์ด {c['id']}"]
    return "\n".join(out) + "\n"


# ---------- talking to Claude and to the human ----------
def ask(sp, mode, resumed_prompt, fresh_prompt, jira_on_fresh):
    sid = sp.session()
    if sid:
        data = sp.call(resumed_prompt, mode, resume=sid)
        if not data.get("is_error") and {"SOLUTION", "CARDS"} & ac.sections(data.get("result")).keys():
            return handle(sp, data)
    return handle(sp, sp.call(fresh_prompt, mode), fresh_jira=jira_on_fresh)


def failed(sp, data, msg):
    sp.p("last-claude-output.txt").write_text(str(data.get("result")), encoding="utf-8")
    hc.say("ERROR", f"{msg} (คำตอบของ Claude เก็บไว้ที่ {sp.rel}/last-claude-output.txt)",
           f"{SHOW} ห้ามเดาสาเหตุ ห้ามแก้เอง แล้วหยุดรอ ถ้าผู้ใช้สั่งลองใหม่ ให้รัน {SELF} status {sp.key}")
    return 1


def handle(sp, data, fresh_jira=False, may_fix=True):
    s = ac.sections(data.get("result"))
    if data.get("is_error") or not {"SOLUTION", "CARDS"} & s.keys():
        return failed(sp, data, "Claude ไม่ได้ตอบตามรูปแบบ")
    req = sp.cfg.get("claude", {}).get("require_jira", True)
    if fresh_jira and not sp.local and req and not data.get("jira_read"):
        return failed(sp, data, f"Claude อ่านการ์ด {sp.key} จาก Jira ไม่สำเร็จ arbiter จึงไม่ใช้ผลรอบนี้")
    reply = s.get("REPLY", "").strip()
    reply = "" if reply.upper() == "NONE" else reply
    if reply:  # the discussion is the spike's record: keep Claude's side next to answers.md
        with sp.p("replies.md").open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {now()}\n{reply}\n")
    if "SOLUTION" in s:
        new = s["SOLUTION"] + "\n"
        if sp.text("solution.md") != new:  # the plan changed: any approval and card list built on it are void
            sp.p("solution.md").write_text(new, encoding="utf-8")
            for f in ("cards.json", "cards.md"):
                sp.p(f).unlink(missing_ok=True)
        q = s.get("QUESTIONS", "").strip()
        if q and q.upper() != "NONE":
            sp.p("questions.md").write_text(f"<!-- {now()} -->\n{q}\n", encoding="utf-8")
        else:
            sp.p("questions.md").unlink(missing_ok=True)
        return present(sp, reply)
    if not sp.plan_ok():
        return failed(sp, data, "Claude ส่งรายการการ์ดทั้งที่แผนยังไม่ผ่านการอนุมัติ")
    d, errors, warns = validate(sp, s["CARDS"])
    if errors:
        with sp.p("cards-errors.md").open("a", encoding="utf-8") as fh:  # retro: what the machine bounced
            fh.write(f"\n## {now()}\n" + "\n".join(f"- {e}" for e in errors) + "\n")
    if errors and may_fix and data.get("session_id"):
        fix = ac.render("spike_fix.md", ERRORS="\n".join(f"- {e}" for e in errors), LANG_RULE=sp.lang())
        return handle(sp, sp.call(fix, "fix", resume=data["session_id"]), may_fix=False)
    if errors:
        return failed(sp, data, "รายการการ์ดไม่ผ่านกฎของ arbiter:\n" + "\n".join(f"- {e}" for e in errors))
    hc.write_json(sp.p("cards.json"), d)
    sp.p("cards.md").write_text(render_cards(sp, d), encoding="utf-8")
    return present(sp, reply, warns)


def footer(sp):
    rows = ac.session_rows(sp.p("sessions.jsonl"))
    cost = sum(r.get("cost_usd") or 0 for r in rows)
    over = f" · ⚠ เกินงบ ${sp.s['budget_usd']} แล้ว" if cost > sp.s["budget_usd"] else ""
    return f"\n(spike {sp.key} · คุยไป {sum(r['mode'] == 'answer' for r in rows)} รอบ · ค่า Claude สะสม ${cost:.2f}{over})"


def present(sp, reply="", warns=()):
    """Show whatever the spike is waiting for, derived from the files (never from what an agent says)."""
    head = f"Claude ตอบ:\n{reply}\n\n---\n\n" if reply else ""
    warn = ("\n\nคำเตือน:\n" + "\n".join(f"- {w}" for w in warns)) if warns else ""
    ans = f'{SELF} answer {sp.key} "<ข้อความของผู้ใช้ ห้ามแก้>"'
    if sp.p("jira-map.json").exists():
        hc.say("INFO", f"spike {sp.key} จบแล้ว การ์ด: {sp.text('jira-map.json').strip()}",
               "แจ้งผู้ใช้ว่าเริ่มการ์ดได้ด้วย /feature <รหัสการ์ด>", user="เริ่มการ์ดแรกได้ด้วย /feature KS-xx")
    elif sp.cards_ok():
        cards = hc.read_json(sp.p("cards.json"), {})
        new_epic = isinstance(cards.get("epic"), dict)
        board = "(ไม่ต้องดึงการ์ดขึ้นบอร์ดเอง arbiter ทำให้ตอน link)"
        ids = (["E"] if new_epic else []) + [c["id"] for c in cards.get("cards", [])]
        hc.say("APPROVED_CARDS", f"อนุมัติการ์ดของ {sp.key} แล้ว ({sp.rel}/cards.md)",
               f"สร้างการ์ดใน Jira project KS เท่านั้น ตาม {sp.rel}/cards.md ทีละใบตามลำดับในตาราง "
               + ("เริ่มจาก Epic E (ชื่อและ goal ตามไฟล์) " if new_epic else "")
               + "แต่ละหัวข้อ `## Cn` คือการ์ด 1 ใบ: ใช้ชื่อ ประเภท แต้ม (Story Points) ตามไฟล์ และใช้เนื้อหาใต้หัวข้อนั้น"
                 "เป็น description ตามตัวอักษร ห้ามแก้หรือสรุป "
               + ("ผูกทุกใบกับ epic " if cards.get("epic") else "ไม่ต้องผูก epic ")
               + ("ใส่ fix version ตาม Release (สร้างถ้ายังไม่มี) " if cards.get("release") else "ไม่ต้องใส่ fix version ")
               + f"สร้าง subtask ตามขั้นตอนย่อย (ไม่ใส่แต้ม) {board} "
                 f'เสร็จแล้วรัน {SELF} link {sp.key} "{" ".join(f"{i}=KS-?" for i in ids)}" (ใส่รหัสจริงครบทุกตัว)',
               user=f"ไม่ต้องทำอะไร Gemini จะสร้างการ์ด {len(ids)} ใบใน Jira ให้")
    elif sp.plan_ok() and sp.p("cards.json").exists():
        hc.say("READY_FOR_CARDS_APPROVAL", head + sp.text("cards.md") + warn + footer(sp),
               f"{SHOW} แล้วรอ ถ้าผู้ใช้อนุมัติการ์ด ให้รัน {SELF} approve {sp.key} \"<ข้อความอนุมัติของผู้ใช้ ห้ามแก้>\" "
               f"ถ้าผู้ใช้ถามหรือขอแก้ ให้รัน {ans}",
               user='ตรวจรายการการ์ดแล้วพิมพ์ "อนุมัติการ์ด" เพื่อให้ Gemini สร้างใน Jira หรือพิมพ์สิ่งที่อยากแก้')
    elif sp.plan_ok():
        hc.say("BLOCKED", f"แผนของ {sp.key} อนุมัติแล้วแต่ยังไม่มีรายการการ์ด",
               f"รัน {SELF} cards {sp.key} เพื่อให้ Claude ซอยการ์ด",
               user="ไม่ต้องทำอะไร Gemini จะสั่งซอยการ์ดอีกครั้ง")
    elif sp.p("questions.md").exists():
        q = re.sub(r"^<!--.*?-->\n", "", sp.text("questions.md"))
        hc.say("NEEDS_ANSWERS", f"{head}{sp.text('solution.md')}\n---\n\nคำถาม ({sp.key}):\n{q}{footer(sp)}",
               f"{SHOW} แล้วรอผู้ใช้ จากนั้นรัน {ans} (ถ้าผู้ใช้แนบไฟล์ ให้เพิ่ม --file <path> ต่อไฟล์)",
               user="ตอบ ถามกลับ หรือแย้งได้ในแชทนี้ ข้อที่ไม่ตอบจะใช้ค่า [default] ของข้อนั้น")
    else:
        hc.say("READY_FOR_PLAN_APPROVAL", f"{head}{sp.text('solution.md')}{footer(sp)}",
               f"{SHOW} แล้วรอ ถ้าผู้ใช้อนุมัติแผน ให้รัน {SELF} approve {sp.key} \"<ข้อความอนุมัติของผู้ใช้ ห้ามแก้>\" "
               f"ถ้าผู้ใช้ถาม แย้ง หรือขอแก้ ให้รัน {ans}",
               user='อ่านแผนแล้วพิมพ์ "อนุมัติแผน" เพื่อให้ซอยการ์ด หรือพิมพ์สิ่งที่อยากถาม แย้ง หรือแก้')
    return 0


# ---------- commands ----------
def attach(sp, files):
    """Copy attachments into inputs/ (+ .txt for .docx/.xlsx). Returns problems; unreadable files are not kept."""
    problems, d = [], sp.p("inputs")
    for f in files:
        src = Path(f)
        if not src.is_file():
            problems.append(f"{f}: ไม่พบไฟล์")
            continue
        try:
            text = ax.extract(src)
        except ValueError as e:
            problems.append(f"{src.name}: {e}")
            continue
        d.mkdir(parents=True, exist_ok=True)
        dst, n = d / src.name, 1
        while dst.exists():
            dst, n = d / f"{src.stem}-{n}{src.suffix}", n + 1
        shutil.copyfile(src, dst)
        if text is not None:
            dst.with_name(dst.name + ".txt").write_text(text, encoding="utf-8")
    return problems


def unreadable(sp, problems):
    hc.say("ERROR", "อ่านไฟล์แนบเหล่านี้ไม่ได้ arbiter จึงยังไม่ส่งให้ Claude:\n" + "\n".join(f"- {p}" for p in problems),
           f'{SHOW} แล้วรอ เมื่อผู้ใช้ส่งไฟล์ใหม่ ให้รัน {SELF} answer {sp.key} "<ข้อความของผู้ใช้>" --file <path>',
           user="ส่งไฟล์เหล่านี้มาใหม่ในรูปแบบที่อ่านได้ (เช่น PDF, .docx, .xlsx หรือคัดลอกข้อความมาวางในแชท)")
    return 1


def cmd_new(a):
    root = hc.repo_root()
    text = " ".join(a.text or []).strip()
    if not a.key and not text and not a.file:
        raise RuntimeError('ต้องระบุรหัสการ์ด หรือ --text "<requirement>" หรือ --file <path>')
    if a.key:
        key = a.key
    else:
        base = root / hc.SPIKES_REL
        nums = [int(m.group(1)) for p in (base.iterdir() if base.exists() else []) if (m := SPIKE_RE.fullmatch(p.name))]
        key = f"SPIKE-{max(nums, default=0) + 1}"
    sp = Spike(key, root)
    if sp.dir.exists():
        hc.say("ERROR", f"มี spike {sp.key} อยู่แล้ว", f"ใช้ {SELF} status {sp.key} แทน",
               user="ไม่ต้องทำอะไร Gemini จะดูสถานะ spike นี้ต่อ")
        return 1
    sp.dir.mkdir(parents=True)
    sp.p("intent.md").write_text((text or (f"(requirement อยู่ในการ์ด {sp.key} บน Jira)" if not sp.local else
                                           "(requirement อยู่ในไฟล์แนบใน inputs/)")) + "\n", encoding="utf-8")
    problems = attach(sp, a.file)
    if problems:
        return unreadable(sp, problems)
    return ask(sp, "discuss", None, sp.discuss_prompt(), True)


def cmd_answer(sp, a):
    text = " ".join(a.text).strip()
    if not text and not a.file:
        raise RuntimeError("ไม่มีข้อความหรือไฟล์")
    if sp.p("jira-map.json").exists():
        hc.say("BLOCKED", f"spike {sp.key} จบแล้ว (สร้างการ์ดใน Jira แล้ว)", STOP)
        return 1
    problems = attach(sp, a.file)
    if problems:
        return unreadable(sp, problems)
    files = ("\n(แนบไฟล์: " + ", ".join(Path(f).name for f in a.file) + ")") if a.file else ""
    with sp.p("answers.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {now()}\n{text}{files}\n")
    phase = "ซอยการ์ด" if sp.plan_ok() else "แผน"
    resumed = ac.render("spike_answer.md", KEY=sp.key, DIR=sp.rel, ANSWER=text + files, PHASE=phase,
                        LANG_RULE=sp.lang())
    fresh = sp.cards_prompt(text + files) if sp.plan_ok() else sp.discuss_prompt(text + files)
    return ask(sp, "answer", resumed, fresh, not sp.plan_ok())


def cmd_approve(sp, a):
    if not sp.p("solution.md").exists() or sp.p("questions.md").exists():
        hc.say("BLOCKED", "ยังไม่มีแผน หรือยังมีคำถามค้างอยู่", f"{STOP} (ดูด้วย {SELF} status {sp.key})")
        return 1
    ap = hc.read_json(sp.p("approvals.json"), {})
    entry = {"at": now(), "text": " ".join(a.text)}
    if not sp.plan_ok():
        ap["solution"] = dict(entry, sha256=hc.sha256(sp.p("solution.md")))
        ap.pop("cards", None)
        hc.write_json(sp.p("approvals.json"), ap)
        return cmd_cards(sp, a)
    if sp.p("cards.json").exists() and not sp.cards_ok():
        ap["cards"] = dict(entry, sha256=hc.sha256(sp.p("cards.json")))
        hc.write_json(sp.p("approvals.json"), ap)
    return present(sp)


def cmd_cards(sp, a=None):
    if not sp.plan_ok():
        hc.say("BLOCKED", "แผนยังไม่ผ่านการอนุมัติ", STOP)
        return 1
    return ask(sp, "cards", sp.cards_prompt(), sp.cards_prompt(), False)


def cmd_link(sp, a):
    if not sp.cards_ok():
        hc.say("BLOCKED", "รายการการ์ดยังไม่ผ่านการอนุมัติ หรือถูกแก้หลังอนุมัติ", STOP)
        return 1
    cards = hc.read_json(sp.p("cards.json"), {})
    need = (["E"] if isinstance(cards.get("epic"), dict) else []) + [str(c["id"]) for c in cards["cards"]]
    got = {k.upper(): v.upper() for k, v in re.findall(r"\b([CE]\d*)\s*=\s*([A-Za-z][A-Za-z0-9]+-\d+)", " ".join(a.text))}
    kp = sp.cfg.get("key_pattern", r"KS-\d+")
    errs = ([f"ไม่มีรหัสของ {i}" for i in need if i not in got] + [f"ไม่มี {i} ในรายการการ์ด" for i in got if i not in need]
            + [f"{i}={k} ไม่ตรงกับ key_pattern" for i, k in got.items() if not re.fullmatch(kp, k)]
            + (["มีรหัสการ์ดซ้ำ"] if len(set(got.values())) != len(got) else []))
    if errs:
        hc.say("BLOCKED", "link ไม่ครบหรือไม่ถูกต้อง:\n" + "\n".join(f"- {e}" for e in errs),
               f'แก้แล้วรัน {SELF} link {sp.key} "{" ".join(f"{i}=KS-?" for i in need)}" ด้วยรหัสจริงจาก Jira')
        return 1
    hc.write_json(sp.p("jira-map.json"), {i: got[i] for i in need})
    hc.git("add", "--", sp.rel, cwd=sp.root)
    msg = f"docs(spike): {sp.key} {sp.title()}\n\nArbiter-Spike: {sp.key}\n"
    r = hc.git("commit", "-F", "-", "--", sp.rel, cwd=sp.root, check=False, stdin=msg)
    if r.returncode != 0:
        hc.say("ERROR", f"บันทึกการ์ดแล้ว แต่ git commit โฟลเดอร์ spike ไม่ผ่าน:\n{r.stdout}{r.stderr}", STOP)
        return 1
    sha = hc.git("rev-parse", "--short", "HEAD", cwd=sp.root).strip()
    arbiter_board.to_board([got[i] for i in need if i != "E"], sp.cfg)  # cards only: the epic is not a board card
    keys = " ".join(f"{i}={got[i]}" for i in need)
    nxt = (f"{SHOW} แล้วขยับการ์ด spike {sp.key} บน Jira ไป Done" if not sp.local else
           f"{SHOW} spike นี้ไม่มีการ์ดบน Jira ของตัวเอง")
    hc.say("SPIKE_DONE", f"commit {sha}: spike {sp.key}\nการ์ด: {keys}", nxt,
           user="เริ่มการ์ดแรกได้ด้วย /feature KS-xx (เริ่มจากใบที่ไม่ต้องรอใบอื่นก่อน)")
    return 0


def cmd_status(a):
    if a.key:
        return present(Spike(a.key))
    base = hc.repo_root() / hc.SPIKES_REL
    open_ = [p.name for p in sorted(base.iterdir())] if base.exists() else []
    open_ = [k for k in open_ if not (base / k / "jira-map.json").exists()]
    hc.say("INFO", "spike ที่ยังไม่จบ: " + (", ".join(open_) or "-"),
           f'ดู spike ด้วย {SELF} status <รหัส> หรือเริ่มใหม่ด้วย {SELF} new --text "<requirement>"')
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="arbiter spike", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("key", nargs="?")
    n.add_argument("--text", nargs="+", help="requirement ตามที่ผู้ใช้พิมพ์ ห้ามแก้")
    n.add_argument("--file", action="append", default=[], help="ไฟล์แนบ ใส่ได้หลายครั้ง")
    an = sub.add_parser("answer")
    an.add_argument("key")
    an.add_argument("text", nargs="*")
    an.add_argument("--file", action="append", default=[])
    for name in ("approve", "link"):
        p = sub.add_parser(name)
        p.add_argument("key")
        p.add_argument("text", nargs="+")
    sub.add_parser("cards").add_argument("key")
    sub.add_parser("status").add_argument("key", nargs="?")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "new":
            return cmd_new(a)
        if a.cmd == "status":
            return cmd_status(a)
        sp = Spike(a.key)
        if not sp.dir.exists():
            raise RuntimeError(f"ไม่พบ spike {sp.key} (เริ่มด้วย {SELF} new)")
        return {"answer": cmd_answer, "approve": cmd_approve, "cards": cmd_cards, "link": cmd_link}[a.cmd](sp, a)
    except Exception as e:  # report through the same channel the agent reads
        hc.say("ERROR", f"{type(e).__name__}: {e}", STOP)
        return 1
