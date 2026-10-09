# -*- coding: utf-8 -*-
"""两件事的最终裁决：
A. \\b 在非词字符上为什么永不成立（模式10/11 的共同根因）
B. run_command 若返回 1MB，工具输出到达模型前有没有截断兜底
"""
from __future__ import annotations

import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 78)
print("A. \\b 语义裁决")
print("=" * 78)
print(r"\b 成立的条件：位置两侧【恰好一侧】是词字符 [A-Za-z0-9_]")
print()

tests = [
    (r"\b:\(\)\s*\{", ":(){", "模式11 对裸 fork 炸弹"),
    (r"\b:\(\)\s*\{", "x:(){", "前置有词字符时"),
    (r"\b:\(\)\s*\{", "echo ':(){'", "前置是单引号"),
    (r"\b:\(\)\s*\{", ":(){ :|:& };:", "模式11 对完整 fork 炸弹"),
    (r"\bchmod\s+-R\s+777\s+/\b", "chmod -R 777 /etc", "模式10 后有词字符"),
    (r"\bchmod\s+-R\s+777\s+/\b", "chmod -R 777 /", "模式10 后是行尾"),
]
for pat, s, note in tests:
    m = re.search(pat, s, re.IGNORECASE)
    print(f"  {note}")
    print(f"    {pat!r}")
    print(f"    对 {s!r:<22} -> {'命中 ' + str(m.group()) if m else '未命中'}")
    print()

print("  结论：")
print("    模式11 \\b 在 ':' 之前 —— ':' 是非词字符，字符串开头两侧皆非词 -> 永不成立")
print("           => fork 炸弹保护形同虚设")
print("    模式10 \\b 在 '/' 之后 —— '/' 是非词字符，行尾两侧皆非词   -> 漏掉根目录")
print("           => 反而抓得到 'chmod -R 777 /etc'（危险度更低）")
print("    两者同一根因：把词边界 \\b 用在了非词字符的邻接位置")
print()

print("=" * 78)
print("B. 工具输出到达模型前的截断兜底")
print("=" * 78)

from zeroai.core import constants as C

print(f"  CONTEXT_LIMIT (constants)      = {getattr(C, 'CONTEXT_LIMIT', '(未定义)')}")
print(f"  TOOL_OUTPUT_SUMMARY_MAX_LEN    = {C.TOOL_OUTPUT_SUMMARY_MAX_LEN}")
print(f"  MAX_FILE_SIZE                  = {C.MAX_FILE_SIZE}")
print()

# 找到模型上下文上限
import zeroai.core.context as ctx
import inspect

f = getattr(ctx, "_get_model_context_limit", None)
if f:
    try:
        print(f"  _get_model_context_limit('claude') = {f('claude')}")
    except Exception as e:
        print(f"  _get_model_context_limit 调用失败: {e}")
print()

# 找 tool 结果回填处：是否在回填时截断
src = inspect.getsource(ctx)
print("  >>> context.py 中 tool 结果回填相关行")
for i, line in enumerate(src.splitlines(), 1):
    if "role" in line and "tool" in line and ("content" in line or "append" in line):
        print(f"     L{i}: {line.strip()[:110]}")
print()

# 找 _summarize_tool_output 的调用条件
print("  >>> _summarize_tool_output 调用点上下文（判断是「发送前」还是「历史清理时」）")
lines = src.splitlines()
for i, line in enumerate(lines, 1):
    if "_summarize_tool_output(" in line and "def " not in line:
        lo, hi = max(0, i - 12), min(len(lines), i + 3)
        print(f"     ---- (L{lo+1}-L{hi}) ----")
        for j in range(lo, hi):
            mark = ">>" if j + 1 == i else "  "
            print(f"     {mark}L{j+1}: {lines[j].strip()[:110]}")
        print()

print("  >>> 关键：找 to_clean / keep_recent 的划分条件")
for i, line in enumerate(lines, 1):
    if any(k in line for k in ("keep_start_idx", "to_clean =", "def clean", "def compress",
                               "def prepare", "超过", "exceed")):
        print(f"     L{i}: {line.strip()[:115]}")
