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
        env = dict(os.environ, FAKE_DIR=str(self.fake), ARBITER_CLAUDE_CMD=f"{sys.executable}|{FAKE}")
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
        self.assertEqual(self.h("review", "KS-1")[0], "ESCALATE")  # stays blocked
        st, _ = self.h("review", "KS-1", "--continue", "ทำต่อได้")
        self.assertEqual(st, "READY_FOR_GATE2")
        self.assertIn("รอบที่ 3", self.calls()[-1]["prompt"])
        self.assertIn("review.md", self.calls()[-1]["prompt"])

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
