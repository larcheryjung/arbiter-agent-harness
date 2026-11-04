#!/usr/bin/env python3
"""Install the arbiter into a project (idempotent).

    python install.py --project C:/path/to/repo --verify "unit=powershell -NoProfile -File run-unit-tests.ps1"

Creates .arbiter/ (config, tasks/), points git core.hooksPath at floor/git, installs the Antigravity workflow.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "bin"))
import arbiter_core as hc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--verify", action="append", default=[], help='"name=command", repeatable')
    ap.add_argument("--force-hooks", action="store_true", help="override an existing core.hooksPath")
    a = ap.parse_args()

    root = hc.repo_root(a.project)
    h = hc.hdir(root)
    (h / "tasks").mkdir(parents=True, exist_ok=True)
    done = []

    cfg_path = h / "config.json"
    if not cfg_path.exists():
        verify = [{"name": v.split("=", 1)[0].strip(), "cmd": v.split("=", 1)[1].strip()} for v in a.verify]
        hc.write_json(cfg_path, {
            "arbiter_version": hc.VERSION, "key_pattern": r"KS-\d+", "verify": verify,
            "verify_timeout_sec": 3600, "max_rejections": 2, "max_gate_runs": 6,
            "claude": {"spec_model": "opus", "review_model": "opus", "effort": "high", "timeout_sec": 1200}})
        done.append(f"สร้าง {cfg_path}")
    else:
        done.append(f"มี {cfg_path} อยู่แล้ว (ไม่แตะ)")

    gi = h / ".gitignore"
    if not gi.exists():
        gi.write_text("ACTIVE\n*.log\nreview-input.diff\nlast-claude-output.txt\n", encoding="utf-8")
        done.append(f"สร้าง {gi}")

    hooks = (HERE / "floor" / "git").as_posix()
    current = hc.git("config", "--get", "core.hooksPath", cwd=root, check=False).stdout.strip()
    if current and current != hooks and not a.force_hooks:
        done.append(f"ข้าม core.hooksPath: ตั้งไว้เป็น {current} อยู่แล้ว (ใช้ --force-hooks ถ้าต้องการแทนที่)")
    else:
        hc.git("config", "core.hooksPath", hooks, cwd=root)
        done.append(f"git core.hooksPath = {hooks}")

    wf = root / ".agent" / "workflows" / "feature.md"
    wf.parent.mkdir(parents=True, exist_ok=True)
    src = (HERE / "adapters" / "antigravity" / "workflows" / "feature.md").read_text(encoding="utf-8")
    wf.write_text(src.replace("{{ARBITER}}", (HERE / "bin" / "arbiter.py").as_posix()), encoding="utf-8")
    done.append(f"เขียน {wf}")

    print("\n".join(f"- {d}" for d in done))
    print(f"\narbiter {hc.VERSION} ติดตั้งที่ {root} แล้ว")
    print("ขั้นสุดท้าย (คนทำเอง ครั้งเดียว): commit การติดตั้ง ซึ่งต้องข้าม pre-commit เพราะยังไม่มีการ์ด ACTIVE\n"
          "  git add .arbiter .agent/workflows/feature.md\n"
          '  git commit --no-verify -m "chore: install arbiter"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
