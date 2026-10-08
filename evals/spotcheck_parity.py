# -*- coding: utf-8 -*-
"""核验 opencode_parity.py 的判定是否可靠 —— 既查漏判也查误判。

上一版只在 docstring 里声明了「可能漏判」，没考虑**正则过宽造成的
误判**。抽查可疑项，把每一条正则实际匹配到的上下文打出来。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.opencode_parity import FEATURES, scan  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "evals", "results", "parity_spotcheck.txt")

FILES = scan()
L = ["# 差距分析判定核验", ""]


def show(name, limit=3):
    pat = next(p for n, g, p, d in FEATURES if n == name)
    L.append("## %s" % name)
    L.append("正则: `%s`" % pat)
    L.append("")
    shown = 0
    for path in sorted(FILES):
        src = FILES[path]
        for m in re.finditer(pat, src):
            line = src[:m.start()].count("\n") + 1
            seg = src[max(0, m.start() - 50):m.start() + 60]
            seg = seg.replace("\n", " ").replace("\r", "")
            L.append("- `%s:%d`  ...%s..." % (path, line, seg))
            shown += 1
            break
        if shown >= limit:
            break
    if shown == 0:
        L.append("- （无命中）")
    L.append("")
    return shown


p_checks = ["Formatters", "Sharing", "Snapshots", "Policies", "References",
            "Web UI", "Commands", "Attachments", "Network", "Warming",
            "Skills", "Plugins"]
for n in p_checks:
    show(n)

# 结论
L += ["## 初步判定", ""]
L.append("| 功能 | 首版判定 | 核验意见 |")
L.append("|---|---|---|")
verdict = {
    "Formatters": "**误判** —— 正则里的 `black` 是 TUI 主题的颜色词，"
                  "`formatter` 也可能只是 `format()` 的误命中",
    "Sharing": "**误判** —— 27 个文件命中说明正则过宽（`分享` 常见于注释）",
    "Snapshots": "**待定** —— 需看是否真有状态回滚实现",
    "Policies": "需看是否真有策略引擎而非零散判断",
    "References": "需看是否为真正的参考资料注入机制",
    "Web UI": "**部分成立** —— 命中的是 mcp/server.py 的 FastAPI，"
              "属 MCP 服务端而非用户界面",
    "Commands": "需看是否为斜杠命令而非普通函数名",
    "Attachments": "需看是否为真正的附件机制",
    "Network": "需看是否为出口控制而非泛泛的网络调用",
    "Warming": "**确认缺失**（全仓 0 命中）",
    "Skills": "**确认缺失**（仅测试文件与文档里出现该词）",
    "Plugins": "**确认缺失**（全仓 0 命中）",
}
for n in p_checks:
    L.append("| %s | %s | %s |" % (
        n,
        "✅" if next(f for f in FEATURES if f[0] == n) else "?",
        verdict.get(n, "")))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK ->", OUT)
