# -*- coding: utf-8 -*-
"""摸底：ssh_ops 与 Zig/C 层的真实状态，用于判断还值不值得投入。

输出 evals/results/audit_ssh_zig.txt
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evals", "results", "audit_ssh_zig.txt")

L = []


def p(s=""):
    L.append(str(s))


# ── ssh_ops ──
p("# ssh_ops 摸底")
p()
ssh = os.path.join(ROOT, "zeroai", "tools", "ssh_ops.py")
if not os.path.exists(ssh):
    p("**zeroai/tools/ssh_ops.py 不存在**")
else:
    src = io.open(ssh, encoding="utf-8").read()
    p("- 文件: `zeroai/tools/ssh_ops.py`  %d 字节" % len(src))
    tools = re.findall(r'"name"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"', src)
    funcs = re.findall(r"^def ([a-z_][a-z0-9_]*)\(", src, re.M)
    p("- 工具名注册数 (`\"name\":`): **%d**" % len(tools))
    p("  - %s" % ", ".join(tools))
    p("- 顶层函数数: %d" % len(funcs))
    p("  - %s" % ", ".join(funcs[:30]))
    # try/except 保护情况
    p()
    p("### 安全相关检查")
    # 常见误报模式：把失败当成功、异常被吞
    swallowed = len(re.findall(r"except[^\n]*:\s*\n\s*pass\b", src))
    bare_except = len(re.findall(r"except\s*:", src))
    p("- `except: pass` 模式: %d 处" % swallowed)
    p("- 裸 `except:`: %d 处" % bare_except)
    p("- return True/False 的分支: %d / %d"
      % (len(re.findall(r"\breturn True\b", src)),
         len(re.findall(r"\breturn False\b", src))))

# 测试
p()
p("### 测试覆盖")
p("- `tests/` 下 ssh 相关测试文件:")
found = []
for f in sorted(os.listdir(os.path.join(ROOT, "tests"))):
    if f.endswith(".py") and ("ssh" in f or "remote" in f):
        found.append(f)
p("  - %s" % (found if found else "**无**"))
# 全量测试里有没有提到 ssh
n_ssh_tests = 0
for f in sorted(os.listdir(os.path.join(ROOT, "tests"))):
    if not f.endswith(".py"):
        continue
    t = io.open(os.path.join(ROOT, "tests", f), encoding="utf-8",
                errors="replace").read()
    n_ssh_tests += len(re.findall(r"def test_[a-z_0-9]*ssh[a-z_0-9]*", t))
p("- 全量测试中名字含 ssh 的用例: **%d**" % n_ssh_tests)

# ── Zig/C 层 ──
p()
p("# Zig/C 层摸底")
p()
zt = os.path.join(ROOT, "zeroai-tui")
if not os.path.isdir(zt):
    p("`zeroai-tui/` 不存在")
else:
    zig, cfiles = [], []
    for dp, dn, fn in os.walk(zt):
        dn[:] = [d for d in dn if d not in ("zig-cache", "zig-out", "__pycache__")]
        for f in fn:
            if f.endswith(".zig"):
                zig.append(os.path.relpath(os.path.join(dp, f), ROOT))
            elif f.endswith((".c", ".h")):
                cfiles.append(os.path.relpath(os.path.join(dp, f), ROOT))
    p("- .zig 文件 %d 个:" % len(zig))
    for x in zig:
        p("  - `%s`" % x)
    p("- .c/.h 文件 %d 个:" % len(cfiles))
    for x in cfiles:
        p("  - `%s`" % x)

    # Python 侧是否引用
    refs = []
    for dp, dn, fn in os.walk(os.path.join(ROOT, "zeroai")):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            fp = os.path.join(dp, f)
            t = io.open(fp, encoding="utf-8", errors="replace").read()
            if re.search(r"zig_render|_renderer|_terminal|zeroai_tui", t):
                refs.append(os.path.relpath(fp, ROOT))
    p("- zeroai/ 里引用它的 .py 文件: %d 个" % len(refs))
    for r in refs:
        p("  - `%s`" % r)

    # wheel 是否携带
    import glob
    import zipfile
    wheels = sorted(glob.glob(os.path.join(ROOT, "dist", "*.whl")))
    p()
    p("### wheel 打包情况")
    if not wheels:
        p("- 无 dist/*.whl")
    else:
        z = zipfile.ZipFile(wheels[-1])
        names = z.namelist()
        p("- 最新 wheel: `%s`" % os.path.basename(wheels[-1]))
        p("- 条目总数: %d" % len(names))
        bins = [n for n in names if n.endswith((".pyd", ".so", ".dll", ".dylib"))]
        p("- 二进制产物 (.pyd/.so/.dll/.dylib): **%d 个**" % len(bins))
        p("- 是否包含 zeroai_tui/: %s"
          % ("是" if any(n.startswith("zeroai_tui/") for n in names) else "否"))
        p("- 是否包含 .zig/.c 源码: %s"
          % ("是" if any(n.endswith((".zig", ".c")) for n in names) else "否"))

# ── config.yaml 死配置 ──
p()
p("# config.yaml experts 段漂移")
p()
import json
try:
    import yaml
    cfg = yaml.safe_load(io.open(os.path.join(ROOT, "zeroai", "config.yaml"),
                                 encoding="utf-8")) or {}
    exp = cfg.get("experts") or {}
    sys.path.insert(0, ROOT)
    from zeroai.core.constants import EXPERT_TEAM
    p("- config.yaml experts 条目: %d" % len(exp))
    p("- constants.EXPERT_TEAM 条目: %d" % len(EXPERT_TEAM))
    p()
    p("| expert | config 词数 | constants 词数 | system_prompt 是否相同 |")
    p("|---|---|---|---|")
    n_drift = 0
    for k in EXPERT_TEAM:
        ck = (exp.get(k) or {}).get("keywords") or []
        nk = EXPERT_TEAM[k].get("keywords") or []
        cs = (exp.get(k) or {}).get("system_prompt")
        ns = EXPERT_TEAM[k].get("system_prompt")
        same = "—" if cs is None else ("是" if cs == ns else "**否**")
        if same == "**否**":
            n_drift += 1
        p("| %s | %d | %d | %s |" % (k, len(ck), len(nk), same))
    p()
    p("- system_prompt 漂移条目数: **%d / %d**" % (n_drift, len(EXPERT_TEAM)))
    p("- 词表逐专家是否完全相同: **%s**"
      % all(((exp.get(k) or {}).get("keywords") or []) ==
            (EXPERT_TEAM[k].get("keywords") or [])
            for k in EXPERT_TEAM))
except Exception as e:  # noqa: BLE001
    p("解析失败: %r" % e)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK ->", OUT)
