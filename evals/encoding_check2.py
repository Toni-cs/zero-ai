# -*- coding: utf-8 -*-
"""编辑前编码体检：BOM / UTF-8 合法性 / 行尾（以 git 索引为准）/ sha 基线 / git 状态。

用法: python evals/encoding_check2.py <file> [<file> ...]
输出: 逐文件判定 + 改动前 sha（供改后对账）

【CRLF 判定规则，2026-10-10 修订】
本仓库 core.autocrlf=true 且无 .gitattributes：检出时 git 把 LF 写成工作区
的 CRLF，提交时再归一化回 LF。因此【不能拿工作区字节里的 CRLF 当缺陷】：
  - git 索引 i/lf  + 工作区 w/crlf  → 检出产物，放行
  - git 索引 i/crlf 或 i/mixed      → 仓库本身被污染，阻断
  - 文件未被 git 跟踪 + 含 CRLF      → 没有归一化兜底，阻断
修订前无条件按工作区字节数 CRLF 就阻断，实测 tests/ 下 47 个跟踪文件里
有 12 个是 w/crlf（索引全为 i/lf），每次 fresh clone 都会被误拦。
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

    # git 状态与索引行尾一起查
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--", str(p)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace").stdout.strip()
        git = "clean" if not st else "MODIFIED"
        eol = subprocess.run(["git", "ls-files", "--eol", "--", str(p)],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout.strip().split()
    except Exception:
        git, eol = "?", []

    # ---- CRLF 判定：看 git 索引，不看工作区字节 ----
    # 本仓库 core.autocrlf=true 且无 .gitattributes：检出时 git 把 LF 写成
    # 工作区的 CRLF，提交时再归一化回 LF。实测 tests/ 下 47 个跟踪文件
    # 【索引全是 i/lf】，其中 12 个工作区是 w/crlf —— 那是检出产物，不是缺陷。
    # 真正的缺陷只有两种：索引存成 CRLF（i/crlf / i/mixed），或未跟踪文件
    # 自带 CRLF（没有归一化兜底）。此前无条件按工作区字节数 CRLF 就报阻断，
    # 每个 fresh clone 都会误报，属闸门自身的缺陷。
    idx = eol[0] if eol else ""
    if idx in ("i/crlf", "i/mixed"):
        FAIL.append(f"{p}: git 索引行尾为 {idx} —— 仓库本身被 CRLF 污染")
        verdict = idx
    elif idx == "i/lf":
        verdict = "i/lf" + ("+w/crlf(检出产物)" if crlf else "")
    elif idx:
        verdict = idx
    else:
        verdict = "untracked"
        if crlf:
            FAIL.append(f"{p}: 未被 git 跟踪且含 CRLF({crlf}) —— 无归一化兜底")

    print(f"{p.name:<26} BOM={'有' if bom else '-':<3} UTF8={utf8:<6} "
          f"CRLF={crlf:<4} LF={lf:<5} 行尾={verdict:<24} "
          f"sha={sha16(p)} git={git}")


print(f"{'文件':<26} {'BOM':<4} {'UTF8':<7} {'CRLF':<5} {'LF':<6} {'行尾':<24} {'sha':<14} git")
print("-" * 116)
for f in sys.argv[1:]:
    check(f)

print()
if FAIL:
    print("[阻断] 编辑前必须先修复：")
    for f in FAIL:
        print(f"   {f}")
    sys.exit(1)
print("[通过] 合法 UTF-8、无 BOM；CRLF 以 git 索引判定（工作区检出产物不阻断），可安全编辑")
