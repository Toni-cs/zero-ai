# -*- coding: utf-8 -*-
"""关键词表消融实验

目的：在同一批 217 条金标样本上，回答"统一两套路由时，关键词表该以谁为准"。

所有变体共用 expert_route.route_expert 的匹配算法（固定顺序首命中 + vision 优先），
只改关键词表本身，保证唯一变量。

用法：
    python evals/ablate_keywords.py
输出：
    evals/results/ablation.json
    evals/results/ablation.md
"""
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from zeroai.core.constants import EXPERT_TEAM  # noqa: E402

EVAL_PATH = os.path.join("evals", "routing_eval.jsonl")
YAML_PATH = os.path.join("zeroai", "config.yaml")
OUT_DIR = os.path.join("evals", "results")

# 固定匹配顺序：与 expert_route.route_expert 保持一致
ORDER = ["vision", "coder", "security", "devops", "data",
         "reasoner", "academic", "chinese", "pm"]

# coder 中语义过泛、会被 devops/security/data/chinese 抢走的词
GENERIC_CODER = {
    "文件", "查看", "看看", "读取", "读文件", "目录", "打开", "浏览",
    "修改", "编辑", "写入", "创建", "删除", "搜索", "查找", "项目", "仓库", "配置",
}


def route(text, table, order=None):
    """与 expert_route.route_expert 同算法，仅替换关键词表"""
    order = order or ORDER
    low = text.lower()
    for kw in table.get("vision", []):
        k = kw.lower()
        if k in low:
            if k.isascii() and k.isalpha() and len(k) > 4:
                if re.search(r"\b" + re.escape(k) + r"\b", low):
                    return "vision"
            else:
                return "vision"
    for exp in order:
        if exp == "vision":
            continue
        for kw in table.get(exp, []):
            k = kw.lower()
            if k in low:
                if k.isascii() and k.isalpha() and len(k) > 4:
                    if re.search(r"\b" + re.escape(k) + r"\b", low):
                        return exp
                else:
                    return exp
    return "knowledge"


def load_eval():
    rows = []
    with io.open(EVAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def acc(table, rows, order=None):
    ok = 0
    per = defaultdict(lambda: [0, 0])
    conf = Counter()
    for r in rows:
        p = route(r["text"], table, order)
        per[r["gold"]][1] += 1
        if p == r["gold"]:
            ok += 1
            per[r["gold"]][0] += 1
        else:
            conf[(r["gold"], p)] += 1
    recall = {g: c / n for g, (c, n) in sorted(per.items())}
    return ok / len(rows), recall, conf


def main():
    rows = load_eval()
    os.makedirs(OUT_DIR, exist_ok=True)

    const_tbl = {k: list(v["keywords"]) for k, v in EXPERT_TEAM.items()}
    yaml_tbl = {k: list((v or {}).get("keywords") or [])
                for k, v in yaml.safe_load(io.open(YAML_PATH, encoding="utf-8"))["experts"].items()}

    # union：两者并集（constants 为基准 + yaml 独有项）
    union_tbl = {}
    for k in set(const_tbl) | set(yaml_tbl):
        seen, merged = set(), []
        for src in (const_tbl.get(k, []), yaml_tbl.get(k, [])):
            for w in src:
                if w not in seen:
                    seen.add(w)
                    merged.append(w)
        union_tbl[k] = merged

    # 去泛词：constants 去掉 coder 的通用词
    trimmed_tbl = {k: list(v) for k, v in const_tbl.items()}
    trimmed_tbl["coder"] = [w for w in const_tbl["coder"] if w not in GENERIC_CODER]

    # 补 knowledge：constants 去泛词 + yaml 的 knowledge 词
    patched_tbl = {k: list(v) for k, v in trimmed_tbl.items()}
    patched_tbl["knowledge"] = list(yaml_tbl.get("knowledge", []))

    variants = {
        "A_const": ("constants.EXPERT_TEAM（现状·TUI 用）", const_tbl),
        "B_yaml": ("zeroai/config.yaml（现状·包导出用）", yaml_tbl),
        "C_union": ("两者并集", union_tbl),
        "D_const_trim": ("constants 去掉 coder 18 个泛词", trimmed_tbl),
        "E_trim_plus_knowledge": ("D + 补 knowledge 11 词", patched_tbl),
    }

    results = {}
    lines = ["# 关键词表消融实验", "",
             "- 评测集: `%s`（%d 条）" % (EVAL_PATH, len(rows)),
             "- 算法固定：vision 优先 + 固定顺序首命中（与 `expert_route.route_expert` 一致）",
             "- 唯一变量：关键词表", "",
             "| 变体 | 准确率 | Δ vs A | knowledge召回 | pm召回 | coder召回 |",
             "|---|---|---|---|---|---|"]

    base = None
    for name, (desc, tbl) in variants.items():
        a, recall, conf = acc(tbl, rows)
        if base is None:
            base = a
        results[name] = {
            "desc": desc,
            "accuracy": round(a, 4),
            "delta_vs_A": round(a - base, 4),
            "recall_by_gold": {k: round(v, 4) for k, v in recall.items()},
            "top_confusions": [{"gold": g, "pred": p, "n": n}
                               for (g, p), n in conf.most_common(10)],
            "n_keywords": sum(len(v) for v in tbl.values()),
        }
        lines.append("| %s | %.1f%% | %+.1fpp | %.0f%% | %.0f%% | %.0f%% |" % (
            name, a * 100, (a - base) * 100,
            recall.get("knowledge", 0) * 100,
            recall.get("pm", 0) * 100,
            recall.get("coder", 0) * 100,
        ))

    lines += ["", "## 各变体描述", ""]
    for name, (desc, tbl) in variants.items():
        lines.append("- **%s**: %s（%d 词）" % (name, desc,
                                          sum(len(v) for v in tbl.values())))

    lines += ["", "## 最优变体的混淆对 Top 10", ""]
    best = max(results.items(), key=lambda kv: kv[1]["accuracy"])
    lines.append("**%s**（%.1f%%）" % (best[0], best[1]["accuracy"] * 100))
    for c in best[1]["top_confusions"]:
        lines.append("- `%s` → `%s`  ×%d" % (c["gold"], c["pred"], c["n"]))

    with io.open(os.path.join(OUT_DIR, "ablation.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with io.open(os.path.join(OUT_DIR, "ablation.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("OK -> %s" % os.path.join(OUT_DIR, "ablation.md"))


if __name__ == "__main__":
    main()
