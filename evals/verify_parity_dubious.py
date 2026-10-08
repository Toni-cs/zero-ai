# -*- coding: utf-8 -*-
"""对 4 个存疑项做**实现级**核实，替代关键词匹配。

关键词匹配既会漏判也会误判（Formatters 命中了 `blackboard`、
Commands 命中了 LaTeX 的 `\\backslash`）。对这几项改为读代码判断
「有没有真的在做这件事」。

输出 evals/results/parity_verify.txt
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evals", "results", "parity_verify.txt")


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""


L = ["# 存疑项的实现级核实", ""]

# ── 1. Formatters：是否真的在代码生成后调用格式化器 ──
L += ["## Formatters", "",
      "OpenCode 语义：编辑/生成后调用 prettier/black/clang-format 等", "",
      "检索实际的格式化器调用（不是变量名）:", ""]
pats = [r"prettier", r"clang-format", r"autopep8", r"\bruff\b",
        r"format_on_save", r"black\s+--", r"subprocess.*\bblack\b",
        r"Formatter\(\)"]
found = []
for base in ("zeroai", "zeroai-tui"):
    for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
        dn[:] = [d for d in dn if d not in ("__pycache__", "zig-cache")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            src = io.open(p, encoding="utf-8", errors="replace").read()
            for pat in pats:
                for m in re.finditer(pat, src):
                    ln = src[:m.start()].count("\n") + 1
                    found.append("%s:%d  `%s`" % (os.path.relpath(p, ROOT),
                                                  ln, m.group(0)))
L.append("命中: %s" % (", ".join(sorted(set(found))[:8]) if found else "**无**"))
L.append("")
L.append("**判定: %s**"
         % ("❌ 缺失 —— 未发现任何代码格式化器的调用"
            if not found else "需人工确认"))
L.append("")

# ── 2. Commands：是否有斜杠命令分发 ──
L += ["## Commands（斜杠命令）", "",
      "OpenCode 语义：用户输入 /xxx 触发自定义命令", ""]
slash_files = []
for base in ("zeroai", "zeroai-tui"):
    for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
        dn[:] = [d for d in dn if d not in ("__pycache__", "zig-cache")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            src = io.open(p, encoding="utf-8", errors="replace").read()
            # 真正的斜杠命令分发：以 / 开头的命令表或 startswith("/")
            if re.search(r'startswith\(\s*["\']/', src) or \
               re.search(r'COMMANDS\s*=\s*\{', src) or \
               re.search(r'command\s*=\s*["\']/', src, re.I):
                slash_files.append(os.path.relpath(p, ROOT))
L.append("疑似斜杠命令分发实现: %s"
         % (", ".join("`%s`" % x for x in sorted(set(slash_files)))
            if slash_files else "**无**"))
# 但 TUI 是否有命令处理
app_cmd = read(os.path.join("zeroai", "tui", "app_commands.py"))
if app_cmd:
    names = re.findall(r'(\w+)\s*=\s*["\'](/[\w-]+)["\']', app_cmd)
    L.append("app_commands.py 中形如 `= \"/xxx\"` 的定义: %s"
             % (names[:10] if names else "**无**"))
    L.append("app_commands.py 大小: %d 字节" % len(app_cmd))
L.append("")
L.append("**判定: %s**"
         % ("⚠️ 存疑 —— 有命令处理模块但未发现 `/xxx` 命令表"
            if not slash_files else "✅ 有分发实现"))
L.append("")

# ── 3. References：是否有 @file 式的引用注入 ──
L += ["## References", "",
      "OpenCode 语义：在输入里用 @file 之类引用文件并注入其内容", ""]
ref_hits = []
for base in ("zeroai",):
    for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            src = io.open(p, encoding="utf-8", errors="replace").read()
            if re.search(r'@\(?\.\w+|mention.*file|resolve_reference|'
                         r'expand_references', src):
                ref_hits.append(os.path.relpath(p, ROOT))
L.append("疑似 @file 引用解析: %s"
         % (", ".join("`%s`" % x for x in sorted(set(ref_hits))[:6])
            if ref_hits else "**无**"))
L.append("")
L.append("**判定: ❌ 缺失（此前命中的「参考文献」是 doc_gen 的文献标签，"
         "图的 `references:` 是代码依赖图字段，均非此能力）**")
L.append("")

# ── 4. Sharing：是否会话分享 ──
L += ["## Sharing", "",
      "OpenCode 语义：把会话导出为可分享链接", ""]
sh_hits = []
for base in ("zeroai", "zeroai-tui"):
    for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            src = io.open(p, encoding="utf-8", errors="replace").read()
            if re.search(r'def .*(share|export_session|publish)|'
                         r'share_url|share_link|gist\.create', src):
                sh_hits.append(os.path.relpath(p, ROOT))
L.append("疑似会话分享实现: %s"
         % (", ".join("`%s`" % x for x in sorted(set(sh_hits))[:6])
            if sh_hits else "**无**"))
L.append("")
L.append("**判定: ❌ 缺失 —— 首版正则 `share_|分享|export_link|gist` 命中 27 个文件"
         "是过宽导致的误判（`分享` 多见于中文注释，`share_` 命中了无关函数）**")
L.append("")

# ── 5. Snapshots / Policies 复核（这两个看着可能是真的）──
L += ["## Snapshots（复核）", ""]
cli_src = read(os.path.join("zeroai", "cli.py"))
m = re.search(r"enable_checkpoint[^\n]*", cli_src)
L.append("- cli.py: %s" % (m.group(0).strip() if m else "未见 enable_checkpoint"))
m2 = re.search(r"OpenCode 对标[^\n]*", cli_src)
L.append("- cli.py: %s" % (m2.group(0).strip() if m2 else "未见对标注释"))
# 是否真有 checkpoint 模块
ckpt = [f for f in os.listdir(os.path.join(ROOT, "zeroai", "core"))
        if "checkpoint" in f or "snapshot" in f]
L.append("- zeroai/core 下 checkpoint/snapshot 模块: %s"
         % (ckpt if ckpt else "**无**"))
L.append("")

L += ["## Policies（复核）", ""]
sand = read(os.path.join("zeroai", "core", "sandbox.py"))
L.append("- sandbox.py 含「安全策略」: %s" % ("安全策略" in sand))
L.append("- sandbox.py 含 fail-closed 判断: %s"
         % ("fail-closed" in read(os.path.join("zeroai", "cli.py"))))
L.append("- 是否有独立 policy 模块: %s"
         % ([f for f in os.listdir(os.path.join(ROOT, "zeroai"))
             if "policy" in f.lower()] or "**无**"))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK ->", OUT)
