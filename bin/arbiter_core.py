"""Shared helpers for the arbiter CLI and git hooks. Stdlib only."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
BLOCK_RE = re.compile(r"```arbiter[ \t]*\r?\n(.*?)```", re.S)
KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")
ARBITER_DIR = ".arbiter/"
CODE_ONLY = (".", ":(exclude).arbiter")  # pathspec: everything except arbiter artifacts
COMMIT_ENV = "ARBITER_COMMIT_TREE"  # set only by `arbiter commit`; the hook re-checks the index against it

SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Atlassian API token", re.compile(r"\bATATT3[A-Za-z0-9_\-=]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
]
BLOCKED_FILES = re.compile(r"(^|/)(\.env(\..+)?|id_rsa|id_ed25519|id_ecdsa)$")


def git(*args, cwd, env=None, check=True, stdin=None):
    # Plumbing-safe defaults: never quote non-ASCII paths, never run external diff/textconv.
    r = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd, env=env, input=stdin,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout if check else r


def nul_list(out):
    return [p for p in out.split("\0") if p]


def repo_root(start=None):
    return Path(git("rev-parse", "--show-toplevel", cwd=start or os.getcwd()).strip())


def hdir(root):
    return Path(root) / ".arbiter"


def card_dir(root, key):
    if not KEY_RE.fullmatch(key or ""):
        raise RuntimeError(f"รหัสการ์ดไม่ถูกต้อง: {key!r}")
    return hdir(root) / "tasks" / key


def load_config(root):
    p = hdir(root) / "config.json"
    if not p.exists():
        raise RuntimeError(f"ไม่พบ {p} (ยังไม่ได้ติดตั้ง arbiter ในโปรเจกต์นี้ ให้รัน install.py ก่อน)")
    return json.loads(p.read_text(encoding="utf-8"))


def active_key(root):
    p = hdir(root) / "ACTIVE"
    key = p.read_text(encoding="utf-8").strip() if p.exists() else ""
    return key if KEY_RE.fullmatch(key) else None


def read_json(path, default):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def approved_spec(root, key):
    """Spec text only if it is byte-identical to what passed Gate 1, else None."""
    d = card_dir(root, key)
    gate1 = read_json(d / "approvals.json", {}).get("gate1")
    spec = d / "spec.md"
    if not gate1 or not spec.exists() or sha256(spec) != gate1["spec_sha256"]:
        return None
    return spec.read_text(encoding="utf-8")


def parse_block(spec_text):
    """Parse the ```arbiter block of a spec into {"scope_allow": [...], "verify": [...]}."""
    out = {"scope_allow": [], "verify": []}
    m = BLOCK_RE.search(spec_text or "")
    if not m:
        return out
    cur = None
    for raw in m.group(1).splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if re.fullmatch(r"[a-z_]+:", s):
            cur = s[:-1]
            out.setdefault(cur, [])
        elif s.startswith("- ") and cur:
            item = s[2:].strip()
            if cur == "scope_allow":
                item = re.sub(r"\s+#.*$", "", item)  # trailing comment
            item = item.strip("`").strip()
            if item and not item.startswith("<"):
                out[cur].append(item)
    return out


def glob_re(pat):
    """gitignore-style: `*` stays inside one segment, `**/` spans directories, `dir/` and `dir/**` = subtree."""
    pat = pat.replace("\\", "/")
    if pat.startswith("./"):
        pat = pat[2:]
    if pat.endswith("/"):
        pat += "**"
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out)


def in_scope(path, patterns):
    path = path.replace("\\", "/")
    if path.startswith(ARBITER_DIR):
        return True
    return any(glob_re(p).fullmatch(path) for p in patterns)


def code_tree(root):
    """Hash of the working tree as it would be committed, excluding .arbiter/. No commit needed."""
    idx = Path(root) / git("rev-parse", "--git-path", "arbiter-index", cwd=root).strip()
    env = dict(os.environ, GIT_INDEX_FILE=str(idx))
    has_head = git("rev-parse", "--verify", "-q", "HEAD", cwd=root, check=False).returncode == 0
    try:
        git("read-tree", "HEAD" if has_head else "--empty", cwd=root, env=env)
        git("add", "-A", "--", *CODE_ONLY, cwd=root, env=env)
        tree = git("write-tree", cwd=root, env=env).strip()
    finally:
        idx.unlink(missing_ok=True)
    return tree, ("HEAD" if has_head else EMPTY_TREE)


def changed_files(root, base, tree):
    return nul_list(git("diff-tree", "-r", "-z", "--no-renames", "--name-only", base, tree, cwd=root))


def diff_text(root, base, tree):
    return git("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", base, tree, cwd=root)


def same_code(root, tree_a, tree_b):
    """True if two trees are identical outside .arbiter/."""
    r = git("diff-tree", "-r", "--quiet", tree_a, tree_b, "--", *CODE_ONLY, cwd=root, check=False)
    return r.returncode == 0


def head_or_empty(root):
    return "HEAD" if git("rev-parse", "--verify", "-q", "HEAD", cwd=root, check=False).returncode == 0 else EMPTY_TREE


def say(status, body="", nxt=None):
    """Talk to the orchestrating agent. Status travels in text: PowerShell -Command collapses exit codes."""
    if body:
        print(body.rstrip())
        print()
    print(f"STATUS: {status}")
    if nxt:
        print(f"NEXT: {nxt}")
