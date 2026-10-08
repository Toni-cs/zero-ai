# -*- coding: utf-8 -*-
"""编辑前编码体检：确认目标文件是纯 UTF-8，避免重演 .gitignore 损坏。

edit 工具按 UTF-8 解码整个文件再重写。若目标文件是混合编码（如
.gitignore 的 GBK 段），非 UTF-8 字节会被替换成 U+FFFD，中文永久丢失。

因此：任何含非 ASCII 字节的文件，**编辑前必须先过这道检查**。
结果写 evals/results/edit_preflight.txt
"""
import io
import os

TARGETS = [
    "README.md",
    "README.zh-CN.md",
    "AUTHORS",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "zeroai/tools/academic.py",
    "zeroai/tools/registry.py",
    "zeroai/core/prompts.py",
    "tests/test_text_encoding_integrity.py",
    "evals/probe_literature_apis.py",
    "evals/recheck_literature_apis.py",
    "evals/verify_literature_design.py",
]

FFFD = b"\xef\xbf\xbd"
OUT = "evals/results/edit_preflight.txt"

L = []


def p(s=""):
    L.append(str(s))


p("# 编辑前编码体检")
p()
p("| 文件 | 字节 | 非ASCII字节 | FFFD | UTF-8 | GB18030 | 结论 |")
p("|---|---|---|---|---|---|---|")

bad = []
for rel in TARGETS:
    if not os.path.exists(rel):
        p("| %s | - | - | - | - | - | 文件不存在 |" % rel)
        continue
    with open(rel, "rb") as f:
        b = f.read()
    non_ascii = sum(1 for x in b if x > 127)
    n_fffd = b.count(FFFD)
    try:
        b.decode("utf-8")
        u8 = "OK"
    except UnicodeDecodeError as e:
        u8 = "FAIL@%d" % e.start
    try:
        b.decode("gb18030")
        gb = "OK"
    except UnicodeDecodeError:
        gb = "FAIL"

    if u8 == "OK" and n_fffd == 0:
        verdict = "**可安全编辑**"
    elif u8 != "OK":
        verdict = "**禁止直接编辑** —— 非 UTF-8，需先做字节级处理"
        bad.append(rel)
    else:
        verdict = "**已有 FFFD** —— 编辑前需确认是否为既有损坏"
        bad.append(rel)
    p("| `%s` | %d | %d | %d | %s | %s | %s |"
      % (rel, len(b), non_ascii, n_fffd, u8, gb, verdict))

p()
if bad:
    p("## 需要特殊处理的文件")
    for x in bad:
        p("- `%s`" % x)
else:
    p("## 全部可安全编辑（均为纯 UTF-8，且当前无 U+FFFD）")

io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK -> %s  bad=%d" % (OUT, len(bad)))
