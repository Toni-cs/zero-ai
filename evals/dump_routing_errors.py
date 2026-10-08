# -*- coding: utf-8 -*-
"""导出路由当前全部错判，按混淆对分组，供定点分析。

输出 evals/results/routing_errors.md（人读）与 evals/results/routing_errors.json（脚本读）
"""
import io
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core import expert_route  # noqa: E402

EVAL = os.path.join("evals", "routing_eval.jsonl")
OUT_MD = os.path.join("evals", "results", "routing_errors.md")
OUT_JSON = os.path.join("evals", "results", "routing_errors.json")

rows = [json.loads(l) for l in io.open(EVAL, encoding="utf-8") if l.strip()]

errors = []
for i, r in enumerate(rows):
    pred = expert_route.route_expert(r["text"])
    if pred != r["gold"]:
        errors.append({
            "idx": i, "text": r["text"], "gold": r["gold"],
            "pred": pred, "tag": r.get("tag", ""),
            "len": len(r["text"]),
        })

acc = 1 - len(errors) / len(rows)
by_pair = Counter((e["gold"], e["pred"]) for e in errors)
by_gold = Counter(e["gold"] for e in errors)

L = ["# 路由错判全量导出", "",
     "- 评测集: `%s`（%d 条）" % (EVAL, len(rows)),
     "- 当前准确率: **%.1f%%**（对 %d 条，错 %d 条）"
     % (acc * 100, len(rows) - len(errors), len(errors)),
     "", "## 按混淆对分组", "", "| gold | pred | 条数 |", "|---|---|---|"]
for (g, p), n in by_pair.most_common():
    L.append("| `%s` | `%s` | %d |" % (g, p, n))

L += ["", "## 按 gold 分组（哪些专家最常被漏掉）", "",
      "| gold | 错判条数 |", "|---|---|"]
gold_total = Counter(r["gold"] for r in rows)
for g, n in by_gold.most_common():
    L.append("| `%s` | %d / %d |" % (g, n, gold_total[g]))

L += ["", "## 逐条明细", ""]
cur = None
for e in sorted(errors, key=lambda x: (x["gold"], x["pred"])):
    key = (e["gold"], e["pred"])
    if key != cur:
        cur = key
        L += ["", "### `%s` → `%s`  ×%d" % (key[0], key[1], by_pair[key]), ""]
    L.append("- [%s] (len=%d) %s" % (e["tag"] or "-", e["len"], e["text"]))

os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
io.open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
io.open(OUT_JSON, "w", encoding="utf-8").write(
    json.dumps({"accuracy": round(acc, 4), "n": len(rows),
                "n_error": len(errors), "errors": errors},
               ensure_ascii=False, indent=2))

print("accuracy=%.4f  errors=%d/%d" % (acc, len(errors), len(rows)))
for (g, p), n in by_pair.most_common():
    print("  %-12s -> %-12s  %d" % (g, p, n))
