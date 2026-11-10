"""End-to-end tests on a throwaway git repo with a fake Claude. Run: python -m unittest discover -s tests -v"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME / "bin"))
import arbiter_core as hc  # noqa: E402

ARBITER = HOME / "bin" / "arbiter.py"
FAKE = HOME / "tests" / "fake_claude.py"

SPEC = """# KS-1: Greeting
## Goal
say hello
```arbiter
scope_allow:
  - src/**
verify:
  - <optional>
```"""


JIRA_OK = [{"name": "mcp__jira__jira_get_issue", "input": {"issue_key": "KS-1"}, "ok": True}]


def spec_resp(questions="NONE", spec=SPEC, tools=None):
    return {"result": f"===SPEC===\n{spec}\n===QUESTIONS===\n{questions}\n===END===",
            "tools": JIRA_OK if tools is None else tools}


def review_resp(verdict, findings="ไม่มี"):
    return {"result": f"===VERDICT===\n{verdict}\n===FINDINGS===\n{findings}\n===UNTESTED===\n-\n===LOOK_AT===\n-\n===END==="}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.fake = Path(self.tmp.name) / "fake"
        self.repo.mkdir()
        self.fake.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("src/app.py", "print('hi')\n")
        self.write("check.py", "import sys\nsys.exit(1 if 'BUG' in open('src/app.py').read() else 0)\n")
        self.write("README.md", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        r = subprocess.run([sys.executable, str(HOME / "install.py"), "--project", str(self.repo),
                            "--verify", f"unit={Path(sys.executable).as_posix()} check.py"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.git("add", ".arbiter", ".agent/workflows")
        self.git("commit", "-q", "--no-verify", "-m", "chore: install arbiter")  # the one-time human step
        self.queue([])

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True,
                              encoding="utf-8", check=check)

    def write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def queue(self, items):
        (self.fake / "queue.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def calls(self):
        p = self.fake / "calls.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def h(self, *args):
        env = dict(os.environ, FAKE_DIR=str(self.fake), ARBITER_CLAUDE_CMD=f"{sys.executable}|{FAKE}",
                   ARBITER_BOARD_FAKE=getattr(self, "board_fake", None) or str(self.fake / "board.jsonl"))
        r = subprocess.run([sys.executable, str(ARBITER), *args], cwd=self.repo, capture_output=True,
                           text=True, encoding="utf-8", env=env)
        status = next((ln[8:].split()[0] for ln in r.stdout.splitlines() if ln.startswith("STATUS: ")), None)
        return status, r.stdout

    def card(self, name, key="KS-1"):
        return self.repo / ".arbiter" / "tasks" / key / name

    def cfg(self, commit=True, **kw):
        p = self.repo / ".arbiter" / "config.json"
        c = json.loads(p.read_text(encoding="utf-8"))
        c.update(kw)
        p.write_text(json.dumps(c), encoding="utf-8")
        if commit:  # arbiter only trusts the committed config; a human commits it past the hook
            self.git("add", ".arbiter/config.json")
            self.git("commit", "-q", "--no-verify", "-m", "config")

    def to_gate2(self, content="print('a')\n"):
        self.to_gate1()
        self.write("src/app.py", content)
        self.queue([review_resp("PASS")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE2", out)

    def to_gate1(self):
        self.queue([spec_resp()])
        self.assertEqual(self.h("spec", "KS-1")[0], "READY_FOR_GATE1")
        self.assertEqual(self.h("approve", "KS-1", "อนุมัติ")[0], "APPROVED_GATE1")


class ArbiterTest(Base):
    # ---------- flows ----------
    def test_happy_path_with_questions_resume_and_commit(self):
        self.queue([spec_resp("1. สีอะไร? [default: แดง]"), spec_resp()])
        st, out = self.h("spec", "KS-1")
        self.assertEqual(st, "NEEDS_ANSWERS")
        self.assertIn("สีอะไร?", out)
        self.assertIn("▶ สิ่งที่คุณต้องทำ: ตอบคำถาม", out)
        self.assertIn("ภาษาไทย", self.calls()[0]["prompt"])  # config default
        self.assertIn("jira_get_issue", self.calls()[0]["prompt"])
        self.assertEqual((self.repo / ".arbiter" / "ACTIVE").read_text(encoding="utf-8"), "KS-1")
        self.assertEqual(self.h("approve", "KS-1", "ok")[0], "BLOCKED")  # open questions

        st, out = self.h("answer", "KS-1", "1 เอา", "น้ำเงิน")
        self.assertEqual(st, "READY_FOR_GATE1")
        self.assertIn('พิมพ์ "อนุมัติ Gate 1"', out)
        self.assertIn("1 เอา น้ำเงิน", self.card("answers.md").read_text(encoding="utf-8"))
        second = self.calls()[1]
        self.assertIn("--resume", second["args"])
        self.assertIn("1 เอา น้ำเงิน", second["prompt"])
        self.assertEqual(second["args"][second["args"].index("--tools") + 1], "Read,Grep,Glob")  # read-only

        self.assertEqual(self.h("approve", "KS-1", "อนุมัติ Gate 1")[0], "APPROVED_GATE1")
        self.write("src/app.py", "print('hello')\n")
        self.write("src/new_module.py", "X = 1\n")  # untracked file counts too
        self.queue([review_resp("PASS")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE2", out)
        self.assertIn('พิมพ์ "อนุมัติ Gate 2"', out)
        ev = json.loads(self.card("evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(ev["result"], "pass")
        self.assertEqual(sorted(ev["changed_files"]), ["src/app.py", "src/new_module.py"])
        self.assertIn("src/new_module.py", self.card("review-input.diff").read_text(encoding="utf-8"))

        st, out = self.h("commit", "KS-1", "อนุมัติ Gate 2")
        self.assertEqual(st, "COMMITTED", out)
        self.assertIn("Jira", out)
        log = self.git("log", "-1", "--format=%B").stdout
        self.assertIn(f"Arbiter-Tree: {ev['tree']}", log)
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")

    def test_spec_without_jira_read_is_error_and_writes_no_spec(self):
        self.queue([spec_resp(tools=[]), spec_resp()])
        st, out = self.h("spec", "KS-1")
        self.assertEqual(st, "ERROR", out)
        self.assertIn("Jira", out)
        self.assertFalse(self.card("spec.md").exists())
        self.assertIn('"jira_read": false', self.card("sessions.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(self.h("spec", "KS-1")[0], "READY_FOR_GATE1")  # rerun works once Jira is read

    def test_failed_or_wrong_card_jira_call_does_not_count(self):
        failed = [{"name": "mcp__jira__jira_get_issue", "input": {"issue_key": "KS-1"}, "ok": False}]
        other = [{"name": "mcp__jira__jira_get_issue", "input": {"issue_key": "KS-2"}, "ok": True}]
        self.queue([spec_resp(tools=failed), spec_resp(tools=other)])
        self.assertEqual(self.h("spec", "KS-1")[0], "ERROR")
        self.assertEqual(self.h("spec", "KS-1")[0], "ERROR")

    def test_resumed_answer_needs_no_new_jira_read_but_fresh_fallback_does(self):
        self.queue([spec_resp("1. ? [default: a]"), spec_resp(tools=[])])
        self.h("spec", "KS-1")
        self.assertEqual(self.h("answer", "KS-1", "a")[0], "READY_FOR_GATE1")
        self.queue([{"is_error": True, "result": "No conversation found"}, spec_resp(tools=[])])
        self.assertEqual(self.h("answer", "KS-1", "b")[0], "ERROR")

    def test_require_jira_can_be_turned_off(self):
        c = json.loads((self.repo / ".arbiter" / "config.json").read_text(encoding="utf-8"))
        self.cfg(claude={**c["claude"], "require_jira": False})
        self.queue([spec_resp(tools=[])])
        self.assertEqual(self.h("spec", "KS-1")[0], "READY_FOR_GATE1")

    def test_answer_falls_back_to_fresh_session_when_resume_fails(self):
        self.queue([spec_resp("1. ? [default: a]"), {"is_error": True, "result": "No conversation found"}, spec_resp()])
        self.h("spec", "KS-1")
        self.assertEqual(self.h("answer", "KS-1", "a")[0], "READY_FOR_GATE1")
        third = self.calls()[2]
        self.assertNotIn("--resume", third["args"])
        self.assertIn("answers.md", third["prompt"])

    def test_review_blocked_without_gate1_or_after_spec_edit(self):
        self.queue([spec_resp()])
        self.h("spec", "KS-1")
        self.assertEqual(self.h("review", "KS-1")[0], "BLOCKED")
        self.h("approve", "KS-1", "ok")
        self.card("spec.md").write_text(SPEC.replace("src/**", "**"), encoding="utf-8")
        self.assertEqual(self.h("review", "KS-1")[0], "BLOCKED")

    def test_out_of_scope_change_fails_gate_without_calling_claude(self):
        self.to_gate1()
        self.write("src/app.py", "print('hello')\n")
        self.write("README.md", "changed\n")
        n = len(self.calls())
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "FAIL")
        self.assertIn("README.md", out)
        self.assertEqual(len(self.calls()), n)

    def test_failing_verify_fails_gate(self):
        self.to_gate1()
        self.write("src/app.py", "BUG\n")
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "FAIL")
        self.assertIn("unit: exit 1", out)

    def test_two_rejections_escalate_then_continue(self):
        self.to_gate1()
        self.write("src/app.py", "print('a')\n")
        self.queue([review_resp("FAIL", "- [MAJOR] src/app.py:1 x"), review_resp("FAIL", "- [MAJOR] src/app.py:1 y"),
                    review_resp("PASS")])
        self.assertEqual(self.h("review", "KS-1")[0], "FAIL")
        self.assertEqual(self.h("review", "KS-1")[0], "ESCALATE")
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "ESCALATE")  # stays blocked
        self.assertIn('"ทำต่อ: <คำตัดสินของคุณ>"', out)  # the user is offered all three ways out
        self.assertIn('"แก้ spec: <สิ่งที่จะแก้>"', out)
        self.assertIn("park KS-1", out)
        st, _ = self.h("review", "KS-1", "--continue", "ทำต่อได้ ใช้ inline examples")
        self.assertEqual(st, "READY_FOR_GATE2")
        self.assertIn("รอบที่ 3", self.calls()[-1]["prompt"])
        self.assertIn("review.md", self.calls()[-1]["prompt"])
        # the user's ruling is binding for the reviewer (KS-160 looped because it never reached Claude)
        self.assertIn("ทำต่อได้ ใช้ inline examples", self.card("decisions.md").read_text(encoding="utf-8"))
        self.assertIn("decisions.md", self.calls()[-1]["prompt"])
        self.assertIn("ห้ามตีกลับเรื่องที่ผู้ใช้ตัดสินแล้ว", self.calls()[-1]["prompt"])

    # ---------- deadlock: SPEC_ISSUE, dispute, park ----------
    def test_spec_issue_is_not_a_rejection_and_a_revised_spec_resets_the_budget(self):
        self.to_gate1()
        self.write("src/app.py", "print('a')\n")
        issue = {"result": "===VERDICT===\nSPEC_ISSUE\n===SPEC_CHANGE===\nแก้ Approach ให้ใช้ inline\n"
                           "===FINDINGS===\n- [MAJOR] src/app.py:1 ทำตาม Approach ไม่ได้\n===UNTESTED===\n-\n"
                           "===LOOK_AT===\n-\n===END==="}
        self.queue([review_resp("FAIL", "- [MAJOR] src/app.py:1 x"), issue, spec_resp()])
        self.assertEqual(self.h("review", "KS-1")[0], "FAIL")
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "SPEC_ISSUE", out)
        self.assertIn("แก้ Approach ให้ใช้ inline", out)
        self.assertIn('พิมพ์ "แก้ spec ตามที่เสนอ"', out)
        self.assertIn("answer KS-1", out)
        self.assertEqual(json.loads(self.card("state.json").read_text(encoding="utf-8"))["rejections"], 1)
        self.assertIn("SPEC_CHANGE", self.card("review.md").read_text(encoding="utf-8"))
        self.assertEqual(self.h("answer", "KS-1", "แก้ spec ตามที่เสนอ")[0], "READY_FOR_GATE1")
        self.assertIn("SPEC_CHANGE", self.calls()[-1]["prompt"])  # the spec writer is pointed at the proposal
        self.assertEqual(self.h("review", "KS-1")[0], "BLOCKED")  # new spec needs Gate 1 again
        self.assertEqual(self.h("approve", "KS-1", "อนุมัติ Gate 1")[0], "APPROVED_GATE1")
        st = json.loads(self.card("state.json").read_text(encoding="utf-8"))
        self.assertEqual((st["rejections"], st["gate_runs"]), (0, 0))

    def dispute_resp(self, ruling, proposal="ทางเลือก A / B"):
        return {"result": f"===RULING===\n{ruling}\n===REASON===\nlog แสดงว่า lint ไม่ resolve\n"
                          f"===PROPOSAL===\n{proposal}\n===END==="}

    def test_dispute_runs_the_proof_and_architect_rules(self):
        py = Path(sys.executable).as_posix()
        self.to_gate1()
        self.write("src/app.py", "print('a')\n")
        self.queue([review_resp("FAIL", "- [MAJOR] src/app.py:1 ใช้ value $ref"),
                    self.dispute_resp("UPHOLD", "ใช้ components/examples"),
                    self.dispute_resp("REVISE_SPEC", "แก้ AC11"), self.dispute_resp("NEED_HUMAN")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "FAIL")
        self.assertIn("dispute KS-1 --claim", out)  # the builder is told how to object instead of working around
        prove = f"{py} -c \"print('proof-out')\""
        st, out = self.h("dispute", "KS-1", "--claim", "lint", "ไม่", "resolve", "--prove", prove)
        self.assertEqual(st, "DISPUTE_UPHELD", out)
        self.assertIn("ใช้ components/examples", out)
        self.assertIn("proof-out", self.card("dispute-1-p1.log").read_text(encoding="utf-8"))
        log = self.card("disputes.md").read_text(encoding="utf-8")
        self.assertIn("**ข้ออ้างของ Builder (ยังไม่ยืนยัน):** lint ไม่ resolve", log)
        self.assertIn("→ exit 0", log)
        self.assertIn("**ชี้ขาด: UPHOLD**", log)
        call = self.calls()[-1]
        self.assertIn("ข้อโต้แย้งครั้งที่ 1", call["prompt"])
        self.assertIn("ห้ามเชื่อจากข้อความ", call["prompt"])
        self.assertNotIn("lint ไม่ resolve", call["prompt"])  # the claim reaches Claude only as a labelled file entry
        st, out = self.h("dispute", "KS-1", "--claim", "x", "--prove", prove)
        self.assertEqual(st, "SPEC_ISSUE", out)
        self.assertIn("แก้ AC11", out)
        n = len(self.calls())
        st, out = self.h("dispute", "KS-1", "--claim", "y", "--prove", prove)
        self.assertEqual(st, "ESCALATE", out)  # max_disputes 2: the human decides, no third Claude call
        self.assertEqual(len(self.calls()), n)
        self.assertEqual(json.loads(self.card("state.json").read_text(encoding="utf-8"))["rejections"], 1)

    def test_dispute_need_human_offers_options(self):
        py = Path(sys.executable).as_posix()
        self.to_gate1()
        self.queue([self.dispute_resp("NEED_HUMAN", "A: inline · B: externalValue")])
        st, out = self.h("dispute", "KS-1", "--claim", "z", "--prove", f"{py} -c \"print(1)\"")
        self.assertEqual(st, "NEEDS_DECISION", out)
        self.assertIn("A: inline · B: externalValue", out)
        self.assertIn("review KS-1 --continue", out)

    def test_dispute_with_bad_input_runs_nothing(self):
        self.to_gate1()
        n = len(self.calls())
        st, out = self.h("dispute", "KS-1", "--claim", "x", "--prove", "echo a && echo b")
        self.assertEqual(st, "BLOCKED", out)
        self.assertIn("ห้ามมี", out)
        self.assertEqual(self.h("dispute", "KS-1", "--claim", "x")[0], "BLOCKED")  # no proof, no dispute
        self.assertEqual(len(self.calls()), n)
        self.assertFalse(self.card("disputes.md").exists())

    def test_model_picked_at_card_start_is_used_for_every_call(self):
        self.queue([spec_resp(), review_resp("PASS")])
        st, out = self.h("spec", "KS-1", "--model", "sonnet", "--effort", "low")
        args = self.calls()[-1]["args"]
        self.assertEqual((args[args.index("--model") + 1], args[args.index("--effort") + 1]), ("sonnet", "low"))
        self.assertIn("Claude ขั้นรีวิว: sonnet · low (คุณสั่งตอนเริ่มการ์ด)", out)
        self.h("approve", "KS-1", "อนุมัติ Gate 1")
        self.write("src/app.py", "print('a')\n")
        self.h("review", "KS-1")
        args = self.calls()[-1]["args"]
        self.assertEqual((args[args.index("--model") + 1], args[args.index("--effort") + 1]), ("sonnet", "low"))

    def test_fable_picked_at_card_start(self):
        self.queue([spec_resp(), review_resp("PASS")])
        st, out = self.h("spec", "KS-1", "--model", "fable")
        args = self.calls()[-1]["args"]
        self.assertEqual((args[args.index("--model") + 1], args[args.index("--effort") + 1]), ("fable", "high"))
        self.assertIn("Claude ขั้นรีวิว: fable · high (คุณสั่งตอนเริ่มการ์ด)", out)
        self.h("approve", "KS-1", "อนุมัติ Gate 1")
        self.write("src/app.py", "print('a')\n")
        self.h("review", "KS-1")
        args = self.calls()[-1]["args"]
        self.assertEqual(args[args.index("--model") + 1], "fable")

    def test_fable_named_in_gate1_approval_is_used_for_review_only(self):
        self.queue([spec_resp(), review_resp("PASS")])
        self.h("spec", "KS-1")
        spec_args = self.calls()[-1]["args"]
        self.assertEqual(spec_args[spec_args.index("--model") + 1], "opus")  # the default did not move
        st, out = self.h("approve", "KS-1", "อนุมัติ Gate 1 ใช้ fable high")
        self.assertIn("Claude ขั้นรีวิว: fable · high (คุณสั่งตอนอนุมัติ Gate 1)", out)
        self.write("src/app.py", "print('a')\n")
        self.h("review", "KS-1")
        args = self.calls()[-1]["args"]
        self.assertEqual((args[args.index("--model") + 1], args[args.index("--effort") + 1]), ("fable", "high"))

    def test_default_model_is_opus_high_everywhere(self):
        self.queue([spec_resp(), review_resp("PASS")])
        st, out = self.h("spec", "KS-1")
        self.assertIn("Claude ขั้นรีวิว: opus · high (ค่าตั้งต้น)", out)
        self.h("approve", "KS-1", "อนุมัติ Gate 1")
        self.write("src/app.py", "print('a')\n")
        self.h("review", "KS-1")
        for c in self.calls():
            self.assertEqual((c["args"][c["args"].index("--model") + 1], c["args"][c["args"].index("--effort") + 1]),
                             ("opus", "high"))

    def test_park_and_unpark(self):
        self.to_gate1()
        st, out = self.h("park", "KS-1", "ไปทำเรื่องด่วนก่อน")
        self.assertEqual(st, "PARKED", out)
        self.assertFalse((self.repo / ".arbiter" / "ACTIVE").exists())
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "BLOCKED")
        self.assertIn("ถูกพักไว้", out)
        self.assertIn("unpark KS-1", self.h("status", "KS-1")[1])
        self.assertEqual(self.h("unpark", "KS-1", "ทำการ์ด KS-1 ต่อ")[0], "UNPARKED")
        self.assertEqual((self.repo / ".arbiter" / "ACTIVE").read_text(encoding="utf-8"), "KS-1")
        decisions = self.card("decisions.md").read_text(encoding="utf-8")
        self.assertIn("ไปทำเรื่องด่วนก่อน", decisions)
        self.assertIn("ทำการ์ด KS-1 ต่อ", decisions)

    def test_commit_refuses_code_changed_after_review(self):
        self.to_gate2()
        self.write("src/app.py", "print('sneaky')\n")
        st, out = self.h("commit", "KS-1", "ok")
        self.assertEqual(st, "STALE")
        self.assertIn("- src/app.py", out)  # says which file, so nobody guesses
        self.assertIn("▶ สิ่งที่คุณต้องทำ", out)
        self.assertIn("ห้ามแก้ ย้อน stash หรือลบ", out)

    # ---------- LOCAL cards (no Jira) ----------
    def test_local_card_from_users_words_to_commit(self):
        self.queue([spec_resp(tools=[])])  # no Jira read, and none needed
        st, out = self.h("spec", "--local", "เพิ่ม", "endpoint ตรวจคิว")
        self.assertEqual(st, "READY_FOR_GATE1", out)
        self.assertIn("LOCAL-1", out)
        self.assertEqual(self.card("intent.md", "LOCAL-1").read_text(encoding="utf-8"), "เพิ่ม endpoint ตรวจคิว\n")
        call = self.calls()[0]
        self.assertIn(".arbiter/tasks/LOCAL-1/intent.md", call["prompt"])
        self.assertNotIn("jira_get_issue", call["prompt"])
        self.assertNotIn("jira", call["args"][call["args"].index("--allowedTools") + 1])
        self.assertEqual(self.h("approve", "LOCAL-1", "อนุมัติ Gate 1")[0], "APPROVED_GATE1")
        self.write("src/app.py", "print('q')\n")
        self.queue([review_resp("PASS")])
        self.assertEqual(self.h("review", "LOCAL-1")[0], "READY_FOR_GATE2")
        st, out = self.h("commit", "LOCAL-1", "อนุมัติ Gate 2")
        self.assertEqual(st, "COMMITTED", out)
        self.assertIn("ห้ามแตะ Jira", out)
        self.assertIn("Arbiter-Card: LOCAL-1", self.git("log", "-1", "--format=%B").stdout)
        self.queue([spec_resp()])
        st, out = self.h("spec", "--local", "งานที่สอง")
        self.assertIn("LOCAL-2", out)  # numbering continues
        self.assertFalse((self.fake / "board.jsonl").exists())  # LOCAL cards are not on Jira: no board call

    def test_local_key_needs_users_words(self):
        st, out = self.h("spec", "LOCAL-7")
        self.assertEqual(st, "ERROR")
        self.assertIn("--local", out)
        self.assertEqual(self.h("spec", "KS-1", "--local", "x")[0], "ERROR")

    def test_language_follows_the_users_latest_words(self):
        self.queue([spec_resp("1. ? [default: a]"), spec_resp()])
        self.h("spec", "--local", "Add a health endpoint that reports queue depth")
        self.assertIn("English", self.calls()[0]["prompt"])
        self.h("answer", "LOCAL-1", "เอาแบบ a")  # switches back to Thai
        self.assertIn("ภาษาไทย", self.calls()[1]["prompt"])
        self.h("approve", "LOCAL-1", "ok")
        self.write("src/app.py", "print('q')\n")
        self.queue([review_resp("PASS")])
        self.h("review", "LOCAL-1")
        self.assertIn("ภาษาไทย", self.calls()[2]["prompt"])

    # ---------- docs_allow ----------
    def test_doc_change_during_card_is_kept_out_of_the_card(self):
        self.cfg(docs_allow=["*.md"])
        self.to_gate1()
        self.write("src/app.py", "print('a')\n")
        self.write("README.md", "doc edit\n")  # outside scope, but a doc: not a scope failure
        self.queue([review_resp("PASS")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE2", out)
        self.assertNotIn("README.md", self.card("review-input.diff").read_text(encoding="utf-8"))
        self.write("README.md", "doc edited again\n")  # no STALE for docs
        st, out = self.h("commit", "KS-1", "อนุมัติ Gate 2")
        self.assertEqual(st, "COMMITTED", out)
        self.assertIn("- README.md", out)
        self.assertNotIn("README.md", self.git("show", "--name-only", "--format=", "HEAD").stdout)
        self.assertIn("README.md", self.git("status", "--porcelain").stdout)
        self.git("add", "README.md")  # docs-only commit passes the hook without a card
        self.assertEqual(self.git("commit", "-q", "-m", "docs", check=False).returncode, 0)

    def test_card_whose_work_is_docs_is_reviewed_and_committed(self):  # KS-160: OpenAPI spec under kong-workspace/
        self.cfg(docs_allow=["kong-workspace/**", "*.md"])
        spec = SPEC.replace("  - src/**", "  - kong-workspace/notes/api.yaml\n  - kong-workspace/payloads/*.json")
        self.queue([spec_resp(spec=spec)])
        self.assertEqual(self.h("spec", "KS-1")[0], "READY_FOR_GATE1")
        self.assertEqual(self.h("approve", "KS-1", "อนุมัติ Gate 1")[0], "APPROVED_GATE1")
        self.write("kong-workspace/notes/api.yaml", "openapi: 3.1.0\n")
        self.write("kong-workspace/payloads/req.json", "{}\n")
        self.write("kong-workspace/notes/other.md", "side note\n")  # a doc the card does not name: stays a side doc
        self.queue([review_resp("PASS")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE2", out)
        diff = self.card("review-input.diff").read_text(encoding="utf-8")
        self.assertIn("api.yaml", diff)
        self.assertIn("req.json", diff)
        self.assertNotIn("other.md", diff)
        self.write("kong-workspace/notes/api.yaml", "openapi: 3.1.0\n# edited\n")  # the card's doc: STALE
        self.assertEqual(self.h("commit", "KS-1", "อนุมัติ Gate 2")[0], "STALE")
        self.write("kong-workspace/notes/api.yaml", "openapi: 3.1.0\n")
        st, out = self.h("commit", "KS-1", "อนุมัติ Gate 2")
        self.assertEqual(st, "COMMITTED", out)
        files = self.git("show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertIn("kong-workspace/notes/api.yaml", files)
        self.assertIn("kong-workspace/payloads/req.json", files)
        self.assertNotIn("kong-workspace/notes/other.md", files)
        self.assertIn("- kong-workspace/notes/other.md", out)

    def test_review_with_nothing_changed_blocks_instead_of_looping(self):
        self.cfg(docs_allow=["*.md"])
        self.to_gate1()
        self.write("README.md", "doc only\n")  # in docs_allow, not in scope_allow: not the card's work
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "BLOCKED", out)
        self.assertIn("ไม่พบไฟล์ที่เปลี่ยนตาม scope_allow ของ KS-1", out)
        self.assertIn("- README.md", out)
        self.assertIn("ห้ามรัน review ซ้ำเอง", out)
        self.assertIn("▶ สิ่งที่คุณต้องทำ: ถ้ายังไม่ได้ลงมือทำ", out)
        self.assertEqual(len(self.calls()), 1)  # only the spec call: no Claude review

    def test_human_doc_commit_mid_card_is_not_stale_and_not_reverted(self):
        self.cfg(docs_allow=["*.md"])
        self.to_gate2()
        self.write("README.md", "human report\n")
        self.git("add", "README.md")
        self.assertEqual(self.git("commit", "-q", "-m", "docs", check=False).returncode, 0)
        st, out = self.h("commit", "KS-1", "อนุมัติ Gate 2")
        self.assertEqual(st, "COMMITTED", out)
        self.assertEqual(self.git("show", "HEAD:README.md").stdout, "human report\n")

    def test_verify_that_rewrites_a_doc_does_not_break_tree_stable(self):
        py = Path(sys.executable).as_posix()
        self.cfg(docs_allow=["*.md"], verify=[{"name": "report", "cmd": f"{py} -c \"open('README.md','w').write('r')\""}])
        self.to_gate1()
        self.write("src/app.py", "print('a')\n")
        self.queue([review_resp("PASS")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE2", out)

    def test_agent_rule_files_are_never_docs(self):
        self.cfg(docs_allow=["*.md"])
        self.write("AGENTS.md", "new rules\n")
        self.git("add", "AGENTS.md")
        r = self.git("commit", "-q", "-m", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(hc.is_doc("AGENTS.md", ["*.md"]))
        self.assertFalse(hc.is_doc("sub/claude.md", ["**"]))
        self.assertFalse(hc.is_doc(".env", ["**"]))
        self.assertTrue(hc.is_doc("docs/a/b.md", ["docs/**"]))

    # ---------- Jira board ----------
    def test_jira_card_is_moved_onto_the_board_when_work_starts(self):
        self.queue([spec_resp()])
        st, out = self.h("spec", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE1", out)
        self.assertIn("(ดึงการ์ด KS-1 ขึ้นบอร์ด 5335 แล้ว)", out)
        rows = (self.fake / "board.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(r) for r in rows], [{"board": "5335", "issues": ["KS-1"]}])

    def test_board_failure_tells_the_user_and_does_not_block(self):
        self.board_fake = "fail"
        self.queue([spec_resp()])
        st, out = self.h("spec", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE1", out)
        self.assertIn("⚠ ดึงการ์ด KS-1 ขึ้นบอร์ดไม่สำเร็จ (fake failure)", out)
        self.assertIn("boards/5335/backlog", out)
        self.assertLess(out.index("⚠ ดึงการ์ด"), out.index("STATUS:"))

    def test_board_only_ever_sends_ks_keys(self):
        import arbiter_board
        log = self.fake / "direct.jsonl"
        os.environ[arbiter_board.FAKE_ENV] = str(log)
        try:
            hc.NOTICES.clear()
            self.assertTrue(arbiter_board.to_board(["ABC-1", "LOCAL-2", "KS-7", "KS-7"], {"jira_board_id": 99}))
        finally:
            del os.environ[arbiter_board.FAKE_ENV]
            hc.NOTICES.clear()
        self.assertEqual(json.loads(log.read_text(encoding="utf-8")), {"board": "99", "issues": ["KS-7"]})

    def test_uncommitted_config_edit_is_refused(self):
        self.to_gate1()
        self.cfg(commit=False, docs_allow=["**"])  # an agent widening docs_allow locally
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "ERROR")
        self.assertIn("config.json", out)
        self.write("src/app.py", "print('x')\n")
        self.git("add", "src/app.py")
        r = self.git("commit", "-q", "-m", "x", check=False)  # the hook reads HEAD's docs_allow, not the local one
        self.assertNotEqual(r.returncode, 0)

    def test_precommit_needs_gate2_not_just_the_env(self):
        self.to_gate1()
        self.write("src/app.py", "print('forged')\n")
        self.git("add", "src/app.py")
        tree = self.git("write-tree").stdout.strip()
        env = dict(os.environ, **{hc.COMMIT_ENV: tree})
        r = subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=self.repo, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Gate 2", r.stderr)

    def test_commit_refuses_index_that_differs_from_reviewed_tree(self):  # review finding 2
        self.to_gate2()
        self.write("src/app.py", "EVIL\n")
        self.git("add", "src/app.py")
        self.git("update-index", "--assume-unchanged", "src/app.py")
        self.write("src/app.py", "print('a')\n")  # worktree looks reviewed, index holds EVIL
        st, out = self.h("commit", "KS-1", "ok")
        self.assertEqual(st, "ERROR", out)
        self.assertNotIn("EVIL", self.git("show", "HEAD:src/app.py").stdout)

    def test_commit_refuses_when_spec_changed_after_review(self):  # review finding 5
        self.to_gate2()
        self.queue([spec_resp(spec=SPEC.replace("say hello", "say bye"))])
        self.h("answer", "KS-1", "เปลี่ยนเป็น bye")
        self.assertEqual(self.h("commit", "KS-1", "ok")[0], "BLOCKED")

    def test_gate_fails_without_any_verify_command(self):  # review finding 7
        self.cfg(verify=[])
        self.to_gate1()
        self.write("src/app.py", "BUG\n")
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "FAIL")
        self.assertIn("ไม่มีคำสั่ง verify", out)

    def test_spec_verify_with_shell_operators_is_rejected(self):
        self.queue([spec_resp(spec=SPEC.replace("<optional>", "python check.py || echo ok"))])
        self.h("spec", "KS-1")
        self.h("approve", "KS-1", "ok")
        self.write("src/app.py", "print('a')\n")
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "FAIL")
        self.assertIn("spec-1", out)

    def test_verify_that_modifies_code_fails_gate(self):  # review finding 10
        self.cfg(verify=[{"name": "dirty", "cmd": f"{Path(sys.executable).as_posix()} -c \"open('src/gen.txt','w')\""}])
        self.to_gate1()
        self.write("src/app.py", "print('a')\n")
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "FAIL")
        self.assertIn("tree-stable", out)

    def test_continue_also_resets_gate_run_budget(self):  # review finding 9
        self.cfg(max_gate_runs=1)
        self.to_gate1()
        self.write("src/app.py", "BUG\n")
        self.assertEqual(self.h("review", "KS-1")[0], "FAIL")
        self.assertEqual(self.h("review", "KS-1")[0], "ESCALATE")
        self.write("src/app.py", "print('fixed')\n")
        self.queue([review_resp("PASS")])
        self.assertEqual(self.h("review", "KS-1", "--continue", "ทำต่อ")[0], "READY_FOR_GATE2")

    # ---------- git floor ----------
    def test_precommit_blocks_out_of_scope_and_secrets(self):
        self.to_gate1()
        self.write("README.md", "changed\n")
        self.git("add", "README.md")
        r = self.git("commit", "-m", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("README.md", r.stderr)
        self.git("reset", "-q")
        self.write("src/key.txt", "-----BEGIN RSA PRIVATE KEY-----\n")
        self.git("add", "src/key.txt")
        r = self.git("commit", "-m", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("private key", r.stderr)

    def test_precommit_blocks_plain_commit_of_unreviewed_code(self):  # review finding 3
        self.to_gate1()
        self.write("src/app.py", "print('unreviewed')\n")
        self.git("add", "src/app.py")
        r = self.git("commit", "-q", "-m", "x", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("arbiter commit", r.stderr)

    def test_precommit_allows_arbiter_only_commit(self):
        self.to_gate1()
        self.git("add", ".arbiter")
        self.assertEqual(self.git("commit", "-q", "-m", "card files", check=False).returncode, 0)

    def test_precommit_fails_closed_without_config(self):  # review finding 4
        (self.repo / ".arbiter" / "config.json").unlink()
        self.write("README.md", "changed\n")
        self.git("add", "README.md")
        r = self.git("commit", "-q", "-m", "x", check=False)
        self.assertNotEqual(r.returncode, 0)

    # ---------- units ----------
    def test_parse_block_and_scope(self):  # review finding 8
        blk = hc.parse_block(SPEC)
        self.assertEqual(blk, {"scope_allow": ["src/**"], "verify": []})
        self.assertEqual(hc.parse_block("```arbiter\nscope_allow:\n  - src/a.py  # main\n```")["scope_allow"],
                         ["src/a.py"])
        self.assertTrue(hc.in_scope("src/a/b.java", ["src/**"]))
        self.assertTrue(hc.in_scope("src/a/b.java", ["src/"]))
        self.assertTrue(hc.in_scope(".arbiter/tasks/KS-1/spec.md", []))
        self.assertFalse(hc.in_scope("srcx/a.java", ["src/**"]))
        self.assertTrue(hc.in_scope("pom.xml", ["./pom.xml"]))
        self.assertFalse(hc.in_scope("docs/a/b/c.md", ["docs/*.md"]))
        self.assertFalse(hc.in_scope("src/x/README.md", ["*.md"]))
        self.assertTrue(hc.in_scope("src/test_a.py", ["src/**/test_*.py"]))
        self.assertTrue(hc.in_scope("src/ก/ข.java", ["src/**"]))

    def test_thai_filename_passes_gate(self):  # review finding 6 (quotePath)
        self.to_gate1()
        self.write("src/ไทย.py", "x = 1\n")
        self.queue([review_resp("PASS")])
        st, out = self.h("review", "KS-1")
        self.assertEqual(st, "READY_FOR_GATE2", out)


if __name__ == "__main__":
    unittest.main()
