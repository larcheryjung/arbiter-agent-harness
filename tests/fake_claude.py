"""Stand-in for `claude -p --output-format stream-json`. Pops responses from $FAKE_DIR/queue.json, logs calls.

A queued response may carry "tools": [{"name", "input", "ok"}]; each becomes a tool_use + tool_result event pair
before the final result event, so arbiter's tool-call checks see them like real stream output. "init_model" becomes
the `model` of the init event.
"""
import json
import os
import sys
from pathlib import Path

d = Path(os.environ["FAKE_DIR"])
sys.stdin.reconfigure(encoding="utf-8")  # Windows default is cp1252; arbiter sends UTF-8
prompt = sys.stdin.read()
calls = json.loads((d / "calls.json").read_text(encoding="utf-8")) if (d / "calls.json").exists() else []
calls.append({"args": sys.argv[1:], "prompt": prompt})
(d / "calls.json").write_text(json.dumps(calls, ensure_ascii=False), encoding="utf-8")
queue = json.loads((d / "queue.json").read_text(encoding="utf-8"))
resp = queue.pop(0) if queue else {"is_error": True, "result": "queue empty"}
(d / "queue.json").write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
resp.setdefault("session_id", f"sess-{len(calls)}")
resp.setdefault("is_error", False)
sys.stdout.reconfigure(encoding="utf-8")
init = {"type": "system", "subtype": "init", "session_id": resp["session_id"]}
if "init_model" in resp:  # the real CLI reports the model it resolved the alias to
    init["model"] = resp.pop("init_model")
print(json.dumps(init))
for i, t in enumerate(resp.pop("tools", [])):
    tid = f"tu_{i}"
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": t["name"], "input": t.get("input", {})}]}}))
    print(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": not t.get("ok", True), "content": "x"}]}}))
print(json.dumps({"type": "result", **resp}, ensure_ascii=False))
