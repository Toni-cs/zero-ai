# -*- coding: utf-8 -*-
"""查证两件事：
1. _SSH_DANGEROUS_PATTERNS 里 fork 炸弹 / chmod 模式到底能不能匹配
2. run_command 的 1MB 输出下游有没有兜底（CONTEXT_LIMIT 只有 8192）
"""
from __future__ import annotations

import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 74)
print("1. 危险命令模式实测")
print("=" * 74)

PATTERNS = [
    (r"\brm\s+-rf\s+/(?!\S)", "rm -rf /"),
    (r"\bmkfs\b", "mkfs"),
    (r"\bdd\s+if=", "dd"),
    (r"\bshutdown\b", "shutdown"),
    (r"\binit\s+0\b", "init 0"),
    (r"\bhalt\b", "halt"),
    (r"\breboot\b", "reboot"),
    (r">\s*/dev/sd[a-z]", "写裸设备"),
    (r"\biptables\s+-F\b", "清空防火墙"),
    (r"\bchmod\s+-R\s+777\s+/\b", "全盘777"),
    (r"\:\(\)\s*\{", "fork炸弹"),
]

CASES = [
    "chmod -R 777 /",
    "chmod -R 777 /etc",
    "chmod -R 777 / && echo done",
    ":(){ :|:& };:",
    ":(){",
    "echo x",
]

print(f"{'命令':<26} {'命中的模式':<22}")
print("-" * 74)
for cmd in CASES:
    hits = [(label) for pat, label in PATTERNS if re.search(pat, cmd, re.IGNORECASE)]
    print(f"{cmd!r:<26} {hits if hits else '—— 未命中 ——'}")

print()
print(">>> 关键验证：fork 炸弹模式单独测")
m = re.search(r"\:\(\)\s*\{", ":(){ :|:& };:", re.IGNORECASE)
print(f"    r'\\:\\(\\)\\s*\\{{' 对 ':(){{ :|:& }};:'  ->  {m}")
_span = m.span() if m else "无"
_grp = repr(m.group(0)) if m else "无"
print(f"    匹配位置: {_span}  文本: {_grp}")

print()
print(">>> chmod 模式的 \\b 语义")
for s in ["chmod -R 777 /", "chmod -R 777 /etc"]:
    pat = r"\bchmod\s+-R\s+777\s+/\b"
    _orig = bool(re.search(pat, s))
    _nob = bool(re.search(r"\bchmod\s+-R\s+777\s+/", s))
    print(f"    {s!r:<24} 原模式 -> {_orig}")
    print(f"    {'':<24} 去掉\\b -> {_nob}")

print()
print(">>> \\b 解释：'/' 是非词字符")
print("    '/etc' 之后位置: '/'非词 + 'e'词  -> 有边界 -> \\b 成立")
print("    '/结尾' 之后位置: '/'非词 + 结束   -> 两侧都非词 -> \\b 不成立")
print("    => 原模式恰好漏掉最危险的『根目录本身』")

print()
print("=" * 74)
print("2. 输出上限的下游兜底")
print("=" * 74)
from zeroai.core import constants as C

for name in ("CONTEXT_LIMIT", "TOOL_OUTPUT_SUMMARY_MAX_LEN", "MAX_FILE_SIZE",
             "PERMISSION_LEVEL"):
    print(f"    {name:<32} = {getattr(C, name, '(未定义)')}")

print()
print(">>> 搜寻工具输出的下游截断/摘要逻辑")
import zeroai.core.agent as agent_mod
import inspect

src = inspect.getsource(agent_mod)
for kw in ("TOOL_OUTPUT_SUMMARY_MAX_LEN", "truncat", "截断", "[:", "summary("):
    hits = [i + 1 for i, line in enumerate(src.splitlines()) if kw in line]
    print(f"    {kw!r:<30} 命中 {len(hits)} 处 -> {hits[:8]}")
