"""git pre-commit (wired via core.hooksPath). Fails closed.

- Code (anything outside .arbiter/ and docs_allow) may only be committed by `arbiter commit`, only the reviewed
  tree, and only with a Gate 2 approval recorded for that tree.
- Staged code must be inside the Gate-1-approved spec's scope_allow.
- Docs-only commits (every file in docs_allow of the COMMITTED config) need no card; secrets are still scanned.
- Blocks obvious secrets, key/.env files, and edits to .arbiter/config.json.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
import arbiter_core as hc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def problems_for(root):
    if not (hc.hdir(root) / "config.json").exists():
        return ["ไม่พบ .arbiter/config.json แต่ repo นี้ใช้ hook ของ arbiter (core.hooksPath)"]
    base = hc.head_or_empty(root)
    staged = hc.nul_list(hc.git("diff-index", "--cached", "-z", "--no-renames", "--name-only", base, cwd=root))
    found = []

    for f in staged:
        if hc.BLOCKED_FILES.search(f):
            found.append(f"ไฟล์ต้องห้าม: {f}")
        if f == ".arbiter/config.json":
            found.append("ห้ามแก้ .arbiter/config.json ผ่าน commit ปกติ")
    patch = hc.git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", base, cwd=root)
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            found += [f"พบ {name} ในไฟล์ที่ stage" for name, pat in hc.SECRET_PATTERNS if pat.search(line)]

    # docs_allow comes from HEAD, never the working tree: an agent must not be able to widen it locally.
    docs = (hc.committed_config(root) or {}).get("docs_allow", [])
    code = [f for f in staged if not f.startswith(hc.ARBITER_DIR) and not hc.is_doc(f, docs)]
    if not code:
        return found
    expected = os.environ.get(hc.COMMIT_ENV)
    if not expected or not hc.same_code(root, hc.git("write-tree", cwd=root).strip(), expected, docs):
        found.append("commit โค้ดต้องทำผ่าน `arbiter commit` หลัง review ผ่านเท่านั้น")
    return found + card_problems(root, code, expected)


def card_problems(root, code, expected):
    key = hc.active_key(root)
    spec = hc.approved_spec(root, key) if key else None
    if spec is None:
        return ["ไม่มีการ์ด ACTIVE ที่ spec ผ่าน Gate 1 (หรือ spec ถูกแก้หลังอนุมัติ)"]
    scope = hc.parse_block(spec)["scope_allow"]
    found = [f"นอก scope_allow ของ {key}: {f}" for f in code if not hc.in_scope(f, scope)]
    gate2 = hc.read_json(hc.card_dir(root, key) / "approvals.json", {}).get("gate2") or {}
    if not expected or gate2.get("tree") != expected or not gate2.get("text"):
        found.append(f"ไม่มีการอนุมัติ Gate 2 ของ {key} สำหรับโค้ดชุดนี้")
    return found


def main():
    found = problems_for(hc.repo_root())
    if found:
        print("[arbiter pre-commit] BLOCKED", file=sys.stderr)
        for p in dict.fromkeys(found):
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # fail closed
        print(f"[arbiter pre-commit] BLOCKED (hook error: {e})", file=sys.stderr)
        sys.exit(1)
