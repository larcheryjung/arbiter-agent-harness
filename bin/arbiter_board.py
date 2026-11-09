"""Move Jira cards out of the Kanban Backlog compartment onto the board. Stdlib only.

A card created through the Jira API lands in the board's Backlog compartment even after it is moved to
"Ready for Dev", and a card finished there never shows on the board. Agents forgot to run move-to-board.ps1,
so arbiter does it itself (spike link, feature spec) and never blocks the flow: on failure it tells the user
to drag the card by hand.

Same call as kong-command-center's move-to-board.ps1: POST /rest/agile/1.0/board/{id}/issue.
Credentials: JIRA_URL / JIRA_USERNAME / JIRA_API_TOKEN from the environment, else the Jira MCP entry of
~/.claude.json, else ~/.gemini/config/mcp_config.json. Only KS keys are ever sent (hard-coded, not config).
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import arbiter_core as hc

ALLOWED = re.compile(r"KS-\d+")  # Jira sandbox only: every other project is production
DEFAULT_BOARD = "5335"  # KS board (kong-sandbox)
DEFAULT_URL = "https://pacificcross.atlassian.net"
FAKE_ENV = "ARBITER_BOARD_FAKE"  # tests: path of a file that records the keys, or "fail"


def _creds():
    env = {k: os.environ.get(k) for k in ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN")}
    for f in (Path.home() / ".claude.json", Path.home() / ".gemini" / "config" / "mcp_config.json"):
        if env["JIRA_USERNAME"] and env["JIRA_API_TOKEN"]:
            break
        if f.exists():
            raw = f.read_text(encoding="utf-8", errors="replace")
            for k in env:
                m = re.search(rf'"{k}"\s*:\s*"([^"]+)"', raw)
                if m and not env[k]:
                    env[k] = m.group(1)
    return env["JIRA_URL"] or DEFAULT_URL, env["JIRA_USERNAME"], env["JIRA_API_TOKEN"]


def _post(board, keys):
    """None on success, else a short reason (never contains the token)."""
    fake = os.environ.get(FAKE_ENV)
    if fake:
        if fake == "fail":
            return "fake failure"
        with open(fake, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"board": board, "issues": keys}) + "\n")
        return None
    url, user, token = _creds()
    if not (user and token):
        return "ไม่พบ JIRA_USERNAME / JIRA_API_TOKEN"
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/agile/1.0/board/{board}/issue",
                                 data=json.dumps({"issues": keys}).encode(), method="POST",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}"
    except (urllib.error.URLError, OSError) as e:
        return f"{type(e).__name__}: {e}"


def to_board(keys, cfg):
    """Move `keys` onto the board. Reports through hc.NOTICES; returns True on success. Never raises."""
    keys = [k for k in dict.fromkeys(keys) if ALLOWED.fullmatch(k)]
    if not keys:
        return True
    board = str(cfg.get("jira_board_id") or DEFAULT_BOARD)
    try:
        why = _post(board, keys)
    except Exception as e:  # board placement is a convenience: never break the card flow over it
        why = f"{type(e).__name__}: {e}"
    names = ", ".join(keys)
    if why is None:
        hc.NOTICES.append(f"(ดึงการ์ด {names} ขึ้นบอร์ด {board} แล้ว)")
        return True
    hc.NOTICES.append(f"⚠ ดึงการ์ด {names} ขึ้นบอร์ดไม่สำเร็จ ({why}) การ์ดจะค้างใน Backlog และมองไม่เห็นบนบอร์ด "
                      f"ให้ลากขึ้นบอร์ดเองที่ {DEFAULT_URL}/jira/software/projects/KS/boards/{board}/backlog")
    return False
