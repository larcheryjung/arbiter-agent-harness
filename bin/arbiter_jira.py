"""Write the Gate-1-approved spec onto the Jira card, below the card's own text. Stdlib only.

A card often holds only a few lines from the user; the detail (AC, approach, scope) is worked out in spec.md.
A BA who opens a Story in UAT / QA must find the AC on the card, and a request left in NEXT gets forgotten
(LESSONS SPIKE-1), so arbiter writes it itself when Gate 1 is approved. It never blocks the flow: on failure
it tells the user and the card carries on.

Jira REST v3 (ADF), so the user's own description goes back exactly as it came. arbiter owns everything from
its marker heading to the end of the description and replaces that part on every new Gate 1 approval.
Credentials and the KS-only rule are arbiter_board's.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import arbiter_board
import arbiter_core as hc

MARK = "Spec (อนุมัติ Gate 1"  # the section starts at the heading whose text begins with this
NOTE = "arbiter เขียนส่วนนี้เองจาก spec ที่อนุมัติ ถ้าจะเขียนเพิ่ม ให้เขียนเหนือเส้นคั่น ส่วนนี้ถูกแทนทุกครั้งที่อนุมัติ Gate 1 ใหม่"
FAKE_ENV = "ARBITER_JIRA_FAKE"  # tests: path of a JSON file {key: description} standing in for Jira, or "fail"
INLINE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")
ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
HEAD = re.compile(r"^(#{1,6})\s+(\S.*)$")
FENCE = re.compile(r"^\s*```")
RULE = re.compile(r"\s*(-{3,}|\*{3,}|_{3,})\s*")


def _inline(text):
    out = []
    for part in INLINE.split(text):
        if not part:
            continue
        if INLINE.fullmatch(part):
            bold = part.startswith("**")
            out.append({"type": "text", "text": part[2:-2] if bold else part[1:-1],
                        "marks": [{"type": "strong" if bold else "code"}]})
        else:
            out.append({"type": "text", "text": part})
    return out


def _para(lines):
    """One paragraph; line breaks are kept (Given / When / Then stay on their own lines)."""
    content = []
    for ln in lines:
        if content:
            content.append({"type": "hardBreak"})
        content += _inline(ln.strip())
    return {"type": "paragraph", "content": content}


def _lists(items):
    """items: [indent, marker, lines] in document order -> list nodes, nested by indent."""
    out, i = [], 0
    while i < len(items):
        ind, ordered = items[i][0], items[i][1][0].isdigit()
        node = {"type": "bulletList", "content": []}
        if ordered:
            node = {"type": "orderedList", "attrs": {"order": int(re.match(r"\d+", items[i][1]).group())}, "content": []}
        while i < len(items) and items[i][0] == ind and items[i][1][0].isdigit() == ordered:
            li = {"type": "listItem", "content": [_para(items[i][2])]}
            j = i + 1
            while j < len(items) and items[j][0] > ind:
                j += 1
            if j > i + 1:
                li["content"] += _lists(items[i + 1:j])
            node["content"].append(li)
            i = j
        out.append(node)
    return out


def md_to_adf(md, shift=0):
    """The markdown a spec uses -> ADF block nodes. Anything not recognised stays as plain text, never dropped."""
    out, lines, i = [], md.splitlines(), 0

    def block_start(ln):
        return not ln.strip() or FENCE.match(ln) or HEAD.match(ln) or RULE.fullmatch(ln)

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif FENCE.match(ln):
            j = i + 1
            while j < len(lines) and not FENCE.match(lines[j]):
                j += 1
            code = "\n".join(lines[i + 1:j])
            out.append({"type": "codeBlock", "content": [{"type": "text", "text": code}]} if code else {"type": "codeBlock"})
            i = j + 1
        elif HEAD.match(ln):
            m = HEAD.match(ln)
            out.append({"type": "heading", "attrs": {"level": min(6, len(m.group(1)) + shift)},
                        "content": _inline(m.group(2).strip())})
            i += 1
        elif RULE.fullmatch(ln):
            out.append({"type": "rule"})
            i += 1
        elif ITEM.match(ln):
            items = []
            while i < len(lines) and not block_start(lines[i]):
                m = ITEM.match(lines[i])
                if m:
                    items.append([len(m.group(1).expandtabs(4)), m.group(2), [m.group(3)]])
                else:
                    items[-1][2].append(lines[i])  # continuation line of the item above
                i += 1
            out += _lists(items)
        else:
            para = []
            while i < len(lines) and not block_start(lines[i]) and not ITEM.match(lines[i]):
                para.append(lines[i])
                i += 1
            out.append(_para(para))
    return out


def _text(node):
    return "".join(c.get("text", "") for c in node.get("content") or [])


def merge(desc, spec_md, when):
    """The card's description with arbiter's spec section appended, or replaced if it is already there."""
    doc = desc or {"type": "doc", "version": 1, "content": []}
    keep = list(doc.get("content") or [])
    for i, n in enumerate(keep):
        if n.get("type") == "heading" and _text(n).startswith(MARK):
            keep = keep[:i - 1] if i and keep[i - 1].get("type") == "rule" else keep[:i]
            break
    section = [{"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": f"{MARK} · {when[:10]})"}]},
               {"type": "paragraph", "content": [{"type": "text", "text": NOTE, "marks": [{"type": "em"}]}]}]
    section += md_to_adf(spec_md, shift=1)
    return {**doc, "content": keep + ([{"type": "rule"}] if keep else []) + section}


def _call(method, key, body=None):
    url, user, token = arbiter_board._creds()
    if not (user and token):
        raise RuntimeError("ไม่พบ JIRA_USERNAME / JIRA_API_TOKEN")
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/rest/api/3/issue/{key}" + ("" if body else "?fields=description"),
        data=json.dumps(body).encode() if body else None, method=method,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:  # keep Jira's reason (e.g. which ADF node it refused); never the token
        raise RuntimeError(f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from None
    return json.loads(raw) if raw else {}


def _fake_store(fake):
    if fake == "fail":
        raise RuntimeError("fake failure")
    p = Path(fake)
    return p, (json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})


def _read(key):
    fake = os.environ.get(FAKE_ENV)
    if fake:
        return _fake_store(fake)[1].get(key)
    return (_call("GET", key).get("fields") or {}).get("description")


def _write(key, doc):
    fake = os.environ.get(FAKE_ENV)
    if fake:
        p, store = _fake_store(fake)
        store[key] = doc
        p.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        return
    _call("PUT", key, {"fields": {"description": doc}})


def spec_to_card(key, spec_md, when):
    """Put the approved spec on card `key`. Reports through hc.NOTICES; returns True on success. Never raises."""
    if not arbiter_board.ALLOWED.fullmatch(key):  # Jira sandbox only: every other project is production
        return True
    try:
        desc = _read(key)
        if desc is not None and not (isinstance(desc, dict) and desc.get("type") == "doc"):
            raise RuntimeError("description ของการ์ดไม่ใช่รูปแบบที่รู้จัก จึงไม่แก้")
        _write(key, merge(desc, spec_md, when))
        why = None
    except Exception as e:  # the card text is a convenience for readers: never break the card flow over it
        why = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
    link = f"{arbiter_board.DEFAULT_URL}/browse/{key}"
    if why is None:
        hc.NOTICES.append(f"(เขียน spec ที่อนุมัติลง description ของการ์ด {key} แล้ว: {link})")
        return True
    hc.NOTICES.append(f"⚠ เขียน spec ลงการ์ด {key} ไม่สำเร็จ ({why}) งานเดินต่อได้ แต่การ์ดบน Jira ยังไม่มี AC "
                      f"ถ้าต้องการ ให้คัดลอกจาก spec.md ไปแปะเองที่ {link}")
    return False
