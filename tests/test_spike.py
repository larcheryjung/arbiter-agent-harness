"""/spike: plan -> cards -> Jira link, on a throwaway repo with a fake Claude."""
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_arbiter import JIRA_OK, Base, review_resp, spec_resp  # noqa: E402

SOLUTION = "# SPIKE-1: สถานะข้อมูลขาย\n## ความเข้าใจโจทย์\nทีม TA อยากเห็นสถานะ\n## แนะนำ\nทาง A"


def plan_resp(questions="NONE", solution=SOLUTION, reply="NONE", tools=None):
    return {"result": f"===REPLY===\n{reply}\n===SOLUTION===\n{solution}\n===QUESTIONS===\n{questions}\n===END===",
            "tools": tools or []}


def card(cid="C1", **kw):
    c = {"id": cid, "type": "Story", "title": f"การ์ด {cid}", "goal": "g", "ac": ["Given a When b Then c"],
         "points": 3, "model": "sonnet", "effort": "medium", "repo": "repo",
         "scope_hint": ["src/**", "new:src/status.py"], "depends": [], "subtasks": ["ขั้นย่อย"]}
    c.update(kw)
    return c


def cards_resp(cards=None, epic=None, release="TA-Sales-Status-Phase-1"):
    d = {"epic": epic or {"title": "TA status", "goal": "ดูสถานะ"}, "release": release,
         "cards": cards or [card("C1"), card("C2", type="Task", depends=["C1"])]}
    return {"result": f"===REPLY===\nNONE\n===CARDS===\n```json\n{json.dumps(d, ensure_ascii=False)}\n```\n===END==="}


class SpikeTest(Base):
    def sp(self, name, key="SPIKE-1"):
        return self.repo / ".arbiter" / "spikes" / key / name

    def to_cards(self):
        self.queue([plan_resp(), cards_resp()])
        self.assertEqual(self.h("spike", "new", "--text", "อยากรู้สถานะข้อมูลขาย")[0], "READY_FOR_PLAN_APPROVAL")
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติแผน")
        self.assertEqual(st, "READY_FOR_CARDS_APPROVAL", out)
        return out

    def test_full_flow_to_jira_link_and_feature_pointer(self):
        self.queue([plan_resp("1. TA ต้องดูกี่สถานะ? [default: 3]"), plan_resp(reply="ได้ครับ ใช้ 3 สถานะ"),
                    cards_resp()])
        st, out = self.h("spike", "new", "--text", "อยากรู้ว่าข้อมูลขายเข้า HBS หรือยัง")
        self.assertEqual(st, "NEEDS_ANSWERS", out)
        self.assertIn("TA ต้องดูกี่สถานะ?", out)
        self.assertIn("▶ สิ่งที่คุณต้องทำ: ตอบ ถามกลับ หรือแย้ง", out)
        self.assertEqual(self.sp("intent.md").read_text(encoding="utf-8"), "อยากรู้ว่าข้อมูลขายเข้า HBS หรือยัง\n")
        self.assertEqual(self.h("spike", "approve", "SPIKE-1", "อนุมัติ")[0], "BLOCKED")  # questions pending
        st, out = self.h("spike", "answer", "SPIKE-1", "ทำไมต้อง 3 สถานะ")
        self.assertEqual(st, "READY_FOR_PLAN_APPROVAL", out)
        self.assertIn("Claude ตอบ:\nได้ครับ ใช้ 3 สถานะ", out)
        self.assertIn("ได้ครับ ใช้ 3 สถานะ", self.sp("replies.md").read_text(encoding="utf-8"))
        self.assertIn("--resume", self.calls()[1]["args"])
        self.assertIn("ทำไมต้อง 3 สถานะ", self.calls()[1]["prompt"])
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติแผน")
        self.assertEqual(st, "READY_FOR_CARDS_APPROVAL", out)
        self.assertIn("| C2 | Task | การ์ด C2 | 3 | sonnet · medium | repo | C1 |", out)
        self.assertIn("แต้มรวม 6", out)
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติการ์ด")
        self.assertEqual(st, "APPROVED_CARDS", out)
        self.assertIn('link SPIKE-1 "E=KS-? C1=KS-? C2=KS-?"', out)
        self.assertEqual(self.h("spike", "link", "SPIKE-1", "E=KS-10 C1=KS-11")[0], "BLOCKED")  # C2 missing
        self.assertIn("ไม่ต้องดึงการ์ดขึ้นบอร์ดเอง", out)
        st, out = self.h("spike", "link", "SPIKE-1", "E=KS-10 C1=KS-11 C2=KS-12")
        self.assertEqual(st, "SPIKE_DONE", out)
        self.assertIn("(ดึงการ์ด KS-11, KS-12 ขึ้นบอร์ด 5335 แล้ว)", out)  # the epic stays off the board
        board = (self.fake / "board.jsonl").read_text(encoding="utf-8")
        self.assertEqual(json.loads(board), {"board": "5335", "issues": ["KS-11", "KS-12"]})
        files = self.git("show", "--name-only", "--format=%B", "HEAD").stdout
        self.assertIn("Arbiter-Spike: SPIKE-1", files)
        self.assertTrue(files.startswith("docs(spike): SPIKE-1 สถานะข้อมูลขาย\n"), files)
        self.assertTrue(all(f.startswith(".arbiter/spikes/SPIKE-1/") for f in files.split("\n\n", 1)[1].split()
                            if "/" in f), files)
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")
        # the card created from the spike: /feature's Claude is pointed at the approved plan, not a retelling
        self.queue([spec_resp(tools=[{"name": "mcp__jira__jira_get_issue", "input": {"issue_key": "KS-11"}}])])
        self.h("spec", "KS-11")
        self.assertIn(".arbiter/spikes/SPIKE-1/solution.md", self.calls()[-1]["prompt"])
        self.assertIn("`C1`", self.calls()[-1]["prompt"])

    def test_invalid_cards_go_back_to_claude_once_then_error(self):
        bad = cards_resp([card("C1", type="Task", points=13, scope_hint=["nope/**"], depends=["C9"])])
        self.queue([plan_resp(), bad, bad])
        self.h("spike", "new", "--text", "x")
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติแผน")
        self.assertEqual(st, "ERROR", out)
        for msg in ("แต้ม 13", "`nope/**` ไม่พบ", "C9 ซึ่งไม่มีอยู่"):
            self.assertIn(msg, out)
        fix = self.calls()[-1]
        self.assertIn("--resume", fix["args"])
        self.assertIn("แต้ม 13", fix["prompt"])
        self.assertFalse(self.sp("cards.json").exists())
        self.assertEqual(self.sp("cards-errors.md").read_text(encoding="utf-8").count("## "), 2)

    def test_fixed_cards_are_accepted_and_warnings_shown(self):
        self.queue([plan_resp(), cards_resp([card("C1", points=21)]), cards_resp(release="epolicy-v1.4.0")])
        self.h("spike", "new", "--text", "x")
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติแผน")
        self.assertEqual(st, "READY_FOR_CARDS_APPROVAL", out)
        self.assertIn("ดูเหมือนเลขเวอร์ชันของ service", out)

    def test_task_only_without_epic_or_release_is_accepted(self):
        d = {"epic": None, "release": None, "cards": [card("C1", type="Task"), card("C2", type="Task")]}
        resp = {"result": f"===REPLY===\nNONE\n===CARDS===\n{json.dumps(d, ensure_ascii=False)}\n===END==="}
        self.queue([plan_resp(), resp])
        self.h("spike", "new", "--text", "ออกแบบ architecture")
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติแผน")
        self.assertEqual(st, "READY_FOR_CARDS_APPROVAL", out)
        self.assertIn("Epic: - (ไม่ผูก epic)", out)
        self.assertIn("Release: - (ไม่ใส่ fix version)", out)
        self.assertIn("ไม่มี Story: ไม่มีการ์ดใดเข้า UAT / QA", out)
        self.assertEqual(len(self.calls()), 2)  # accepted first time, no fix round
        st, out = self.h("spike", "approve", "SPIKE-1", "อนุมัติการ์ด")
        self.assertEqual(st, "APPROVED_CARDS", out)
        self.assertIn("ไม่ต้องผูก epic ไม่ต้องใส่ fix version", out)
        self.assertIn('link SPIKE-1 "C1=KS-? C2=KS-?"', out)

    def test_plan_change_voids_card_list_and_approvals(self):
        self.to_cards()
        self.assertEqual(self.h("spike", "approve", "SPIKE-1", "อนุมัติการ์ด")[0], "APPROVED_CARDS")
        self.queue([plan_resp(solution=SOLUTION + "\n## เปลี่ยน\nใช้ทาง B")])
        st, out = self.h("spike", "answer", "SPIKE-1", "เปลี่ยนไปใช้ทาง B")
        self.assertEqual(st, "READY_FOR_PLAN_APPROVAL", out)
        self.assertIn("ขั้น: **ซอยการ์ด**", self.calls()[-1]["prompt"])
        self.assertFalse(self.sp("cards.json").exists())
        self.assertEqual(self.h("spike", "link", "SPIKE-1", "E=KS-1 C1=KS-2 C2=KS-3")[0], "BLOCKED")

    def test_edited_cards_file_loses_approval(self):
        self.to_cards()
        self.h("spike", "approve", "SPIKE-1", "อนุมัติการ์ด")
        p = self.sp("cards.json")
        p.write_text(p.read_text(encoding="utf-8").replace('"points": 3', '"points": 1', 1), encoding="utf-8")
        self.assertEqual(self.h("spike", "link", "SPIKE-1", "E=KS-1 C1=KS-2 C2=KS-3")[0], "BLOCKED")

    def test_attachments_docx_xlsx_converted_and_unreadable_stops(self):
        tmp = Path(self.tmp.name)
        st, out = self.h("spike", "new", "--file", str(tmp / "missing.pdf"), "--file", str(self.make_doc(tmp)))
        self.assertEqual(st, "ERROR", out)
        self.assertIn("missing.pdf: ไม่พบไฟล์", out)
        self.assertIn("old.doc: ยังอ่านไฟล์ชนิด .doc ไม่ได้", out)
        self.assertEqual(self.calls(), [])  # nothing sent to Claude
        self.queue([plan_resp()])
        st, out = self.h("spike", "answer", "SPIKE-1", "ไฟล์ใหม่", "--file", str(self.make_docx(tmp)),
                         "--file", str(self.make_xlsx(tmp)))
        self.assertEqual(st, "READY_FOR_PLAN_APPROVAL", out)
        docx = self.sp("inputs/req.docx.txt").read_text(encoding="utf-8")
        self.assertIn("สถานะการขาย", docx)
        self.assertIn("| A1 | B1 |", docx)
        xlsx = self.sp("inputs/sales.xlsx.txt").read_text(encoding="utf-8")
        self.assertIn("## Sheet: Sales", xlsx)
        self.assertIn("| policy |  | 42 |", xlsx)
        self.assertIn("แนบไฟล์: req.docx, sales.xlsx", self.sp("answers.md").read_text(encoding="utf-8"))

    def test_jira_spike_card_must_be_read(self):
        self.queue([plan_resp()])
        st, out = self.h("spike", "new", "KS-5")
        self.assertEqual(st, "ERROR", out)
        self.assertIn("อ่านการ์ด KS-5 จาก Jira ไม่สำเร็จ", out)
        self.assertIn("mcp__jira__jira_search", self.calls()[0]["args"][-1])
        self.queue([plan_resp(tools=[{"name": "mcp__jira__jira_get_issue", "input": {"issue_key": "KS-5"}}])])
        self.assertEqual(self.h("spike", "answer", "KS-5", "ลองใหม่")[0], "READY_FOR_PLAN_APPROVAL")

    def test_read_dirs_and_fresh_session_after_max_rounds(self):
        other = Path(self.tmp.name) / "legacy"
        other.mkdir()
        self.cfg(spike={"read_dirs": [other.as_posix()], "max_rounds_resume": 1})
        self.queue([plan_resp("1. q [default: a]"), plan_resp("1. q2 [default: b]"), plan_resp()])
        self.h("spike", "new", "--text", "x")
        args = self.calls()[0]["args"]
        self.assertEqual(args[args.index("--add-dir") + 1], other.as_posix())
        self.assertIn(other.as_posix(), self.calls()[0]["prompt"])
        self.h("spike", "answer", "SPIKE-1", "a")  # resumed once
        self.h("spike", "answer", "SPIKE-1", "b")  # streak reached max_rounds_resume: fresh from the files
        self.assertIn("--resume", self.calls()[1]["args"])
        self.assertNotIn("--resume", self.calls()[2]["args"])
        self.assertIn("answers.md", self.calls()[2]["prompt"])

    def test_feature_commit_leaves_open_spike_out(self):
        self.queue([plan_resp("1. q [default: a]")])
        self.h("spike", "new", "--text", "x")
        self.to_gate2()
        self.queue([])
        self.assertEqual(self.h("commit", "KS-1", "อนุมัติ Gate 2")[0], "COMMITTED")
        committed = self.git("show", "--name-only", "--format=", "HEAD").stdout
        self.assertNotIn("spikes", committed)
        self.assertIn(".arbiter/spikes/", self.git("status", "--porcelain", "-uall").stdout)

    # ---------- fixtures ----------
    def make_doc(self, tmp):
        (tmp / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0")
        return tmp / "old.doc"

    def make_docx(self, tmp):
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        cell = "<w:tc><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:tc>"
        doc = (f"<w:document {w}><w:body><w:p><w:r><w:t>สถานะการขาย</w:t></w:r></w:p>"
               f"<w:tbl><w:tr>{cell.format('A1')}{cell.format('B1')}</w:tr></w:tbl></w:body></w:document>")
        p = tmp / "req.docx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", doc)
        return p

    def make_xlsx(self, tmp):
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        p = tmp / "sales.xlsx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("xl/workbook.xml", f'<workbook {ns} {r}><sheets><sheet name="Sales" sheetId="1" r:id="rId1"/>'
                                          "</sheets></workbook>")
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
            z.writestr("xl/sharedStrings.xml", f"<sst {ns}><si><t>policy</t></si></sst>")
            z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="s"><v>0</v>'
                                                   '</c><c r="C1"><v>42</v></c></row></sheetData></worksheet>')
        return p


if __name__ == "__main__":
    unittest.main()
