# -*- coding: utf-8 -*-
"""编辑前编码体检：BOM / UTF-8 合法性 / CRLF 情况 / git 状态

用途：含非 ASCII 的文件在被编辑工具改动前必须先过这道检查，
防止出现历史上 .gitignore 被改坏的情况。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGETS = [
    r"D:\C\C\zeroai\tools\ssh_ops.py",
    r"D:\C\C\zeroai\tools\command_exec.py",
    r"D:\C\C\zeroai\core\sandbox.py",
    r"D:\C\C\zeroai\tools\registry.py",
    r"D:\C\C\zeroai\core\prompts.py",
]


def check(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"path": path, "exists": False}
    raw = p.read_bytes()

    bom = None
    if raw.startswith(b"\xef\xbb\xbf"):
        bom = "UTF-8-BOM"
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        bom = "UTF-16"

    utf8_ok = True
    utf8_err = None
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        utf8_ok = False
        utf8_err = str(e)

    crlf = raw.count(b"\r\n")
    lf_only = raw.count(b"\n") - crlf
    has_non_ascii = any(b > 127 for b in raw)

    return {
        "path": path,
        "exists": True,
        "bom": bom,
        "utf8_ok": utf8_ok,
        "utf8_err": utf8_err,
        "crlf": crlf,
        "lf_only": lf_only,
        "non_ascii": has_non_ascii,
        "bytes": len(raw),
        "sha_head": __import__("hashlib").sha256(raw).hexdigest()[:12],
    }


def git_status(path: str) -> str:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--", path],
            capture_output=True, text=True, cwd=str(ROOT), timeout=30,
        )
        out = (r.stdout or "").strip()
        return out if out else "(clean)"
    except Exception as e:  # noqa: BLE001
        return f"git 查询失败: {e}"


def main() -> int:
    bad = []
    print(f"{'文件':<46} {'BOM':<10} {'UTF8':<6} {'CRLF':>6} {'LF':>6} {'非ASCII':<8} {'sha':<12}")
    print("-" * 100)
    for t in TARGETS:
        r = check(t)
        if not r.get("exists"):
            print(f"{t:<46} MISSING")
            continue
        flag = "OK" if r["utf8_ok"] and r["bom"] is None else "!!"
        if flag == "!!":
            bad.append(r)
        name = Path(t).name
        print(f"{name:<46} {str(r['bom'] or '-'):<10} {flag:<6} {r['crlf']:>6} "
              f"{r['lf_only']:>6} {str(r['non_ascii']):<8} {r['sha_head']:<12}")
        if r["utf8_err"]:
            print(f"    错误: {r['utf8_err']}")
    print()
    print("=== git 状态（编辑前基线）===")
    for t in TARGETS:
        print(f"  {Path(t).name:<30} {git_status(t)}")
    print()
    if bad:
        print(f"[阻断] {len(bad)} 个文件编码异常，禁止编辑")
        return 1
    print("[通过] 全部为合法 UTF-8 无 BOM，可安全编辑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
