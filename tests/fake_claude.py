"""Stand-in for `claude -p --output-format json`. Pops responses from $FAKE_DIR/queue.json, logs calls."""
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
print(json.dumps(resp, ensure_ascii=False))
