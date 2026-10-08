# -*- coding: utf-8 -*-
"""coder 18 个泛词的 leave-one-out 分析

背景：消融实验显示"constants 去掉 coder 18 个泛词"使准确率 75.1% -> 78.3%（+3.2pp）。
本脚本逐词拆解这 +3.2pp 到底由几个词贡献，避免为了 1 个坏词删掉 17 个好词。

两个方向：
  remove_one   从基线 A 出发，每次只删 1 个词 -> 准确率上升说明该词有害
  add_back     从最优 D 出发，每次只加回 1 个词 -> 准确率下降说明该词有害

用法：
    python evals/loo_coder_words.py
输出：
    evals/results/loo_coder.json
    evals/results/loo_coder.md
"""
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core.constants import EXPERT_TEAM  # noqa: E402

EVAL_PATH = os.path.join("evals", "routing_eval.jsonl")
OUT_DIR = os.path.join("evals", "results")

ORDER = ["vision", "coder", "security", "devops", "data",
         "reasoner", "academic", "chinese", "pm"]

GENERIC_CODER = [
    "文件", "查看", "看看", "读取", "读文件", "目录", "打开", "浏览",
    "修改", "编辑", "写入", "创建", "删除", "搜索", "查找", "项目", "仓库", "配置",
]

BASE = {k: list(v["keywords"]) for k, v in EXPERT_TEAM.items()}


def route(text, table):
    low = text.lower()
    for kw in table.get("vision", []):
        k = kw.lower()
        if k in low:
            if k.isascii() and k.isalpha() and len(k) > 4:
                if re.search(r"\b" + re.escape(k) + r"\b", low):
                    return "vision"
            else:
                return "vision"
    for exp in ORDER:
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


def preds(table, rows):
    return [route(r["text"], table) for r in rows]


def accuracy(ps, rows):
    return sum(1 for p, r in zip(ps, rows) if p == r["gold"]) / len(rows)


def load_eval():
    rows = []
    with io.open(EVAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    rows = load_eval()
    os.makedirs(OUT_DIR, exist_ok=True)

    A = dict(BASE)
    A_pred = preds(A, rows)
    a_acc = accuracy(A_pred, rows)

    D = dict(BASE)
    D["coder"] = [w for w in BASE["coder"] if w not in GENERIC_CODER]
    D_pred = preds(D, rows)
    d_acc = accuracy(D_pred, rows)

    results = {
        "n_eval": len(rows),
        "baseline_A_accuracy": round(a_acc, 4),
        "trimmed_D_accuracy": round(d_acc, 4),
        "delta_all_18": round(d_acc - a_acc, 4),
        "remove_one": [],
        "add_back": [],
    }

    lines = [
        "# coder 18 个泛词 leave-one-out", "",
        "- 评测集: `%s`（%d 条）" % (EVAL_PATH, len(rows)),
        "- 基线 A（含全部词）: **%.1f%%**" % (a_acc * 100),
        "- 全删 D（18 个词都删）: **%.1f%%**（%+.1fpp）" % (
            d_acc * 100, (d_acc - a_acc) * 100), "",
        "## 方向一：从 A 出发，每次只删 1 个词", "",
        "Δ > 0 表示这个词**单独有害**（删掉它准确率上升）", "",
        "| 词 | 准确率 | Δ vs A | 受影响样本 |",
        "|---|---|---|---|",
    ]

    for w in GENERIC_CODER:
        t = dict(BASE)
        t["coder"] = [x for x in BASE["coder"] if x != w]
        p = preds(t, rows)
        a = accuracy(p, rows)
        diff = [r["text"] for x, y, r in zip(A_pred, p, rows) if x != y]
        results["remove_one"].append(
            {"word": w, "accuracy": round(a, 4),
             "delta_vs_A": round(a - a_acc, 4), "n_changed": len(diff),
             "changed": diff}
        )
        lines.append("| %s | %.1f%% | %+.1fpp | %d |" % (
            w, a * 100, (a - a_acc) * 100, len(diff)))

    lines += ["", "## 方向二：从 D 出发，每次只加回 1 个词", "",
              "Δ < 0 表示这个词**在上下文中有害**（加回来准确率下降）", "",
              "| 词 | 准确率 | Δ vs D | 受影响样本 |",
              "|---|---|---|---|"]

    for w in GENERIC_CODER:
        t = dict(D)
        t["coder"] = list(D["coder"]) + [w]
        p = preds(t, rows)
        a = accuracy(p, rows)
        diff = [r["text"] for x, y, r in zip(D_pred, p, rows) if x != y]
        results["add_back"].append(
            {"word": w, "accuracy": round(a, 4),
             "delta_vs_D": round(a - d_acc, 4), "n_changed": len(diff),
             "changed": diff}
        )
        lines.append("| %s | %.1f%% | %+.1fpp | %d |" % (
            w, a * 100, (a - d_acc) * 100, len(diff)))

    # 结论：哪些词该删
    harmful_alone = sorted(
        [x for x in results["remove_one"] if x["delta_vs_A"] > 0],
        key=lambda x: -x["delta_vs_A"])
    harmful_ctx = sorted(
        [x for x in results["add_back"] if x["delta_vs_D"] < 0],
        key=lambda x: x["delta_vs_D"])
    safe_keep = [x["word"] for x in results["add_back"]
                 if x["delta_vs_D"] >= 0]

    lines += [
        "", "## 结论", "",
        "### 单独即有害（删掉它准确率就上升）",
        ("- " + "、".join("%s(%+.1fpp)" % (x["word"], x["delta_vs_A"] * 100)
                        for x in harmful_alone)
         if harmful_alone else "- （无）"), "",
        "### 加回来就掉分（在上下文中有害）",
        ("- " + "、".join("%s(%+.1fpp)" % (x["word"], x["delta_vs_D"] * 100)
                        for x in harmful_ctx)
         if harmful_ctx else "- （无）"), "",
        "### 加回来不掉分（可以保留）",
        ("- " + "、".join(safe_keep) if safe_keep else "- （无）"), "",
        "### 词级 Δ 之和 vs 全删的实际 Δ",
        "- 单词 Δ 累加: %+.1fpp" % (
            sum(x["delta_vs_A"] for x in results["remove_one"]) * 100),
        "- 全删 18 个实际: %+.1fpp" % ((d_acc - a_acc) * 100),
        "- 差值说明是否存在**交互效应**（词之间互相掩盖/叠加）",
    ]

    results["conclusion"] = {
        "harmful_alone": [x["word"] for x in harmful_alone],
        "harmful_in_context": [x["word"] for x in harmful_ctx],
        "safe_to_keep": safe_keep,
        "sum_of_individual_deltas": round(
            sum(x["delta_vs_A"] for x in results["remove_one"]), 4),
        "actual_delta_remove_all": round(d_acc - a_acc, 4),
    }

    with io.open(os.path.join(OUT_DIR, "loo_coder.json"), "w",
                 encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with io.open(os.path.join(OUT_DIR, "loo_coder.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("OK -> %s" % os.path.join(OUT_DIR, "loo_coder.md"))


if __name__ == "__main__":
    main()
