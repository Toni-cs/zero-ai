# -*- coding: utf-8 -*-
"""编辑前编码体检：BOM / UTF-8 合法性 / CRLF / sha 基线 / git 工作区状态。

用法: python evals/encoding_check2.py <file> [<file> ...]
输出: 逐文件判定 + 改动前 sha（供改后对账）
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAIL = []


def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def check(path_str: str) -> None:
    p = Path(path_str)
    if not p.exists():
        print(f"{p.name:<26} 不存在")
        return
    raw = p.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        raw.decode("utf-8")
        utf8 = "OK"
    except UnicodeDecodeError as e:
        utf8 = f"FAIL({e.start})"
        FAIL.append(f"{p}: 非法 UTF-8")
    if bom:
        FAIL.append(f"{p}: 含 BOM")
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf:
        FAIL.append(f"{p}: 含 CRLF({crlf})")
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--", str(p)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace").stdout.strip()
        git = "clean" if not st else "MODIFIED"
    except Exception:
        git = "?"
    print(f"{p.name:<26} BOM={'有' if bom else '-':<3} UTF8={utf8:<6} "
          f"CRLF={crlf:<4} LF={lf:<5} sha={sha16(p)}  git={git}")


print(f"{'文件':<26} {'BOM':<5} {'UTF8':<8} {'CRLF':<6} {'LF':<6} {'sha':<14} git")
print("-" * 96)
for f in sys.argv[1:]:
    check(f)

print()
if FAIL:
    print("[阻断] 编辑前必须先修复：")
    for f in FAIL:
        print(f"   {f}")
    sys.exit(1)
print("[通过] 全部为合法 UTF-8、无 BOM、无 CRLF，可安全编辑")
