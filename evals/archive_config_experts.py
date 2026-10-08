# -*- coding: utf-8 -*-
"""把被删除的 config.yaml experts 段固化成受控 JSON，保住两件事：

1. evals/ablate_keywords.py 的 B 变体（config.yaml 词表）——
   删段后该脚本若直接读 config.yaml 会 KeyError，实验永久不可复跑。
2. **漂移的证据** —— 「同一程序在两个入口拿到不同 system_prompt」这个
   结论需要原始数据支撑，不能只留在 commit message 里。

来源：.backup_*/config.yaml.bak（该目录被 gitignore，故必须落盘到
evals/results/ 才能随仓库传播）。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evals", "results", "config_experts_before.json")

sys.path.insert(0, os.path.join(ROOT))
import yaml  # noqa: E402

# 取最新的含 config.yaml.bak 的备份目录
cands = sorted((d for d in os.listdir(ROOT) if d.startswith(".backup_")),
               reverse=True)
src = None
for d in cands:
    p = os.path.join(ROOT, d, "config.yaml.bak")
    if os.path.exists(p):
        src = p
        break
if src is None:
    raise SystemExit("找不到 config.yaml 备份")

cfg = yaml.safe_load(io.open(src, encoding="utf-8")) or {}
exp = cfg.get("experts")
if not exp:
    raise SystemExit("备份里没有 experts 段: %s" % src)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with io.open(OUT, "w", encoding="utf-8") as f:
    json.dump(exp, f, ensure_ascii=False, indent=2)

# 同时打印与当前 EXPERT_TEAM 的漂移情况，写进结果里
sys.path.insert(0, ROOT)
from zeroai.core.constants import EXPERT_TEAM  # noqa: E402

L = ["# 已删除的 config.yaml `experts` 段（历史存档）", "",
     "- 来源: `%s`" % os.path.relpath(src, ROOT),
     "- 删除于 2026-10-08，原因见 commit（专家配置统一到 "
     "constants.EXPERT_TEAM）",
     "- 本文件存在是为了让 evals/ablate_keywords.py 的 B 变体可复跑，"
     "并保留漂移证据", "",
     "## 删除当时与 constants.EXPERT_TEAM 的漂移", "",
     "| expert | config 词数 | constants 词数 | keywords 是否相同 | "
     "system_prompt 是否相同 | label 是否相同 | desc 是否相同 |",
     "|---|---|---|---|---|---|---|"]
n_prompt_drift = 0
for k in EXPERT_TEAM:
    c = exp.get(k) or {}
    n = EXPERT_TEAM[k]
    same_kw = (c.get("keywords") or []) == (n.get("keywords") or [])
    same_sp = c.get("system_prompt") == n.get("system_prompt")
    same_lb = c.get("label") == n.get("label")
    same_ds = c.get("desc") == n.get("desc")
    if not same_sp:
        n_prompt_drift += 1
    L.append("| %s | %d | %d | %s | %s | %s | %s |" % (
        k, len(c.get("keywords") or []), len(n.get("keywords") or []),
        "是" if same_kw else "**否**",
        "是" if same_sp else "**否**",
        "是" if same_lb else "**否**",
        "是" if same_ds else "**否**",))

L += ["",
      "**system_prompt 漂移 %d / %d 个专家。**" % (n_prompt_drift, len(EXPERT_TEAM)),
      "",
      "其中 academic 的 config 版只有普通的一句学术专家介绍，",
      "而 constants 版是一整套学术规范（禁止编造文献、引用前必须",
      "citation_check、公式必须 LaTeX 等）。走 config.yaml 的入口",
      "（expert.py 全链路、zeroai_tui/integration）拿不到这些规则 ——",
      "即同一程序在不同入口上学术诚信约束不一致。"]
if "academic" in exp:
    L += ["",
          "### 两者 academic system_prompt 的字数对比", "",
          "- config.yaml 版: **%d 字**" % len(exp["academic"].get("system_prompt") or ""),
          "- constants 版: **%d 字**" % len(EXPERT_TEAM["academic"].get("system_prompt") or "")]

with io.open(os.path.join(ROOT, "evals", "results",
                          "config_experts_drift.md"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")

print("OK ->", OUT)
print("     evals/results/config_experts_drift.md")
print("system_prompt 漂移: %d/%d" % (n_prompt_drift, len(EXPERT_TEAM)))
