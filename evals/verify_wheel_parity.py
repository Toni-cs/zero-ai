# -*- coding: utf-8 -*-
"""验收新构建的 wheel 内是否真的含本轮 run_command 对齐改动（不靠转述，直接解包查）。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIST = Path(r"D:\C\C\dist")
whl = sorted(DIST.glob("*.whl"))[-1]
print(f"wheel: {whl.name}  ({whl.stat().st_size / 1024:.1f} KB)\n")

zf = zipfile.ZipFile(whl)

# (文件, 必须出现的片段, 必须【不】出现的片段)
CHECKS = [
    ("zeroai/tools/command_exec.py",
     ["MAX_CAPTURE_BYTES = 1024 * 1024",
      "MAX_TIMEOUT_S = 10 * 60",
      'EXIT_CODE_PREFIX = "[退出码:"',
      "def strip_exit_code_meta(",
      "def _kill_process_tree(",
      "def _run_capturing(",
      "workdir: str = \"\"",
      "timeout: int = 0",
      "输出已截断",
      "调大 timeout 参数后重试"],
     []),
    ("zeroai/tools/ssh_ops.py",
     ['r"\\bchmod\\s+-R\\s+777\\s+/"',
      'r":\\(\\)\\s*\\{"',
      "为什么【不带】词边界"],
     # 注意：旧模式串会在“原写法”说明注释里出现（有意保留作设计依据），
     # 因此【不能】用全文检索判断是否已修，须走下面的 AST 生效代码检查。
     []),
    ("zeroai/tools/system_check.py",
     ["strip_exit_code_meta(cpu_out)",
      "strip_exit_code_meta(mem_out)"],
     []),
    ("zeroai/tools/registry.py",
     ["workdir", "timeout", "1MB 输出采集"],
     ["120s 超时、8000 字符输出"]),
    ("zeroai/core/prompts.py",
     ["1MB 输出采集", "[退出码: N]"],
     ["120s 超时、8000 字符输出"]),
]

ok = True
for name, must_have, must_not in CHECKS:
    print(f"[{name}]")
    try:
        text = zf.read(name).decode("utf-8")
    except KeyError:
        print(f"  FAIL 文件不在 wheel 内: {name}")
        ok = False
        continue
    for s in must_have:
        hit = s in text
        ok &= hit
        print(f"  {'PASS' if hit else 'FAIL'} 含: {s!r}")
    for s in must_not:
        hit = s in text
        verdict = "PASS" if not hit else "FAIL"
        ok &= not hit
        print(f"  {verdict} 不含: {s!r}")
    print()

# ── 关键：用 AST 只看【生效代码】，排除注释里记录的“原写法”干扰 ──────────
import ast  # noqa: E402

print("[ssh_ops.py 生效危险模式表 — AST 抽取]")
src = zf.read("zeroai/tools/ssh_ops.py").decode("utf-8")
tree = ast.parse(src)
raw = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_SSH_DANGEROUS_PATTERNS":
                raw = node.value
patterns = [e.value for e in raw.elts]

BROKEN = ["\\bchmod\\s+-R\\s+777\\s+/\\b", "\\b:\\(\\)\\s*\\{"]
print(f"  共 {len(patterns)} 条")
for i, p in enumerate(patterns, 1):
    if i in (10, 11):
        print(f"   {i:>2}. {p!r}   <- 本轮修复目标")
for bad in BROKEN:
    hit = bad in patterns
    ok &= not hit
    print(f"  {'FAIL 仍生效' if hit else 'PASS 已移除'}: {bad!r}")
for good in ["\\bchmod\\s+-R\\s+777\\s+/", ":\\(\\)\\s*\\{"]:
    hit = good in patterns
    ok &= hit
    print(f"  {'PASS' if hit else 'FAIL 未生效'}: {good!r}")
print()

# 顺带确认测试文件不应进 wheel（那是仓库资产，不是发行资产）
leaked = [n for n in zf.namelist() if n.startswith("tests/")]
print(f"tests/ 泄漏进 wheel: {leaked if leaked else '无'}")
ok &= not leaked

print()
print("=" * 70)
print("结果:", "全部 PASS" if ok else "存在 FAIL")
raise SystemExit(0 if ok else 1)
