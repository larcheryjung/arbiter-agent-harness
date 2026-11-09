"""Calling `claude -p` for arbiter: prompt templates, the read-only tool set, stream parsing, language. Stdlib only."""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime

import arbiter_core as hc

STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
             "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED")
BUILTIN_TOOLS = "Read,Grep,Glob"  # --tools: the ONLY built-ins that exist in the session (no Bash/Edit/Write)
JIRA_READ_TOOLS = "mcp__jira__jira_get_issue,mcp__jira__jira_search"
DENY_TOOLS = "Edit,Write,NotebookEdit,Bash,PowerShell"
# Written in the target language itself: a model reading a Thai prompt drifts back to Thai otherwise.
LANG_RULES = {
    "th": "สำคัญ: เขียนเนื้อหาทั้งหมดเป็นภาษาไทย ชื่อไฟล์ คลาส เมธอด คำสั่ง และศัพท์เทคนิคใช้ตามต้นฉบับ "
          "marker `===...===` และบล็อก ```arbiter ห้ามแปล",
    "en": "IMPORTANT: Write all prose in English, even though these instructions are in Thai. Keep file, class "
          "and method names, commands and the `===...===` markers and ```arbiter block exactly as they are.",
}
THAI = re.compile(r"[฀-๿]")
LATIN = re.compile(r"[A-Za-z]")


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


def lang_of(texts, default="th"):
    """Language code of the user's latest decisive words (newest last in `texts`)."""
    for t in reversed(texts):
        if THAI.search(t):
            return "th"
        if len(LATIN.findall(t)) >= 20:  # short replies like "1a 2b" decide nothing
            return "en"
    return default


def lang_rule(code):
    return LANG_RULES.get(code, f"IMPORTANT: Write all prose in the language with code '{code}'.")


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


def call(root, ccfg, prompt, tools, model, *, log, mode, key, resume=None, add_dirs=(), effort=None):
    """Run one read-only `claude -p`, log it to `log` (sessions.jsonl) and return the result event (never raises)."""
    override = os.environ.get("ARBITER_CLAUDE_CMD")
    cmd = override.split("|") if override else [shutil.which("claude") or "claude"]
    args = cmd + ["-p", "--output-format", "stream-json", "--verbose",
                  "--model", os.environ.get("ARBITER_MODEL") or model]
    if effort or ccfg.get("effort"):
        args += ["--effort", effort or ccfg["effort"]]
    if resume:
        args += ["--resume", resume]
    for d in add_dirs:
        args += ["--add-dir", d]
    args += ["--permission-mode", "default", "--tools", BUILTIN_TOOLS,
             "--disallowedTools", DENY_TOOLS, "--allowedTools", tools]
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    try:
        r = subprocess.run(args, cwd=root, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=ccfg.get("timeout_sec", 1200))
        data = parse_stream(r.stdout) or {
            "is_error": True, "result": f"(exit {r.returncode}) {(r.stderr or r.stdout)[-800:]}", "tool_calls": []}
    except subprocess.TimeoutExpired:
        data = {"is_error": True, "result": "claude -p หมดเวลา (timeout_sec)", "tool_calls": []}
    data["jira_read"] = any(t["ok"] and t["name"].endswith("jira_get_issue")
                            and str(t["input"].get("issue_key", "")).upper() == key
                            for t in data["tool_calls"])
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now(), "mode": mode, "session_id": data.get("session_id"),
                             "resumed": resume, "is_error": bool(data.get("is_error")),
                             "cost_usd": data.get("total_cost_usd"),
                             "denials": len(data.get("permission_denials") or []),
                             "jira_read": data["jira_read"]}) + "\n")
    return data


def session_rows(log):
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []
