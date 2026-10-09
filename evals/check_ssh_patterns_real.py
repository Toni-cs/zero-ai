# -*- coding: utf-8 -*-
"""从 ssh_ops.py 真实源码里抠出危险命令模式并逐一实测，杜绝转述。"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import zeroai.tools.ssh_ops as ssh_ops

SRC = Path(inspect.getfile(ssh_ops))
src = SRC.read_text(encoding="utf-8")
tree = ast.parse(src)

# --- 1. 用 AST 抠出 _SSH_DANGEROUS_PATTERNS 的真实字面量 ---
raw = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_SSH_DANGEROUS_PATTERNS":
                raw = node.value
if raw is None:
    print("!! 未找到 _SSH_DANGEROUS_PATTERNS")
    raise SystemExit(1)

patterns = [elt.value if isinstance(elt, ast.Constant) else ast.unparse(elt)
            for elt in raw.elts]

print("=" * 78)
print(f"1. 文件中真实存在的模式（共 {len(patterns)} 条）")
print("=" * 78)
for i, p in enumerate(patterns, 1):
    print(f"  {i:>2}. {p!r}")

# --- 2. 逐条实测 ---
print()
print("=" * 78)
print("2. 每条模式单独对目标命令实测")
print("=" * 78)

CASES = {
    "chmod -R 777 /": [],
    "chmod -R 777 /etc": [],
    ":(){ :|:& };:": [],
    ":(){": [],
    "echo x": [],
}
for cmd in CASES:
    print(f"\n  命令: {cmd!r}")
    for i, p in enumerate(patterns, 1):
        try:
            if re.search(p, cmd, re.IGNORECASE):
                print(f"      [{i:>2}] 命中 -> {p!r}")
        except re.error as e:
            print(f"      [{i:>2}] 模式本身非法! {p!r} : {e}")

# --- 3. 直接调用函数 ---
print()
print("=" * 78)
print("3. 直接调 _ssh_check_dangerous")
print("=" * 78)
for cmd in CASES:
    d, pat = ssh_ops._ssh_check_dangerous(cmd)
    print(f"  {cmd!r:<24} -> dangerous={d}  pattern={pat!r}")

# --- 4. 对比：模式列表与 AST 抠出的是否一致 ---
print()
print("=" * 78)
print("4. 运行时模块属性 vs AST 抠取")
print("=" * 78)
mod_pats = list(ssh_ops._SSH_DANGEROUS_PATTERNS)
same = mod_pats == patterns
print(f"  运行时 {len(mod_pats)} 条 / AST {len(patterns)} 条  一致={same}")
if not same:
    for a, b in zip(mod_pats, patterns):
        if a != b:
            print(f"    运行时: {a!r}")
            print(f"    AST   : {b!r}")
