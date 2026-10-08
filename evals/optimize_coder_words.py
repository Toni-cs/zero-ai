# -*- coding: utf-8 -*-
"""coder 泛词的贪心后向剔除（greedy backward elimination）

LOO 显示 18 个词里只有少数有害，且存在交互效应（单词 Δ 累加 +4.6pp ≠ 全删 +3.2pp）。
本脚本从基线 A 出发，每轮剔除"当前最有害"的那个词，直到准确率不再上升，
得到数据驱动的最优剔除集合，而不是拍脑袋全删。

用法：
    python evals/optimize_coder_words.py
输出：
    evals/results/opt_coder.json
    evals/results/opt_coder.md
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core.constants import EXPERT_TEAM  # noqa: E402

EVAL_PATH = os.path.join("evals", "routing_eval.jsonl")
OUT_DIR = os.path.join("evals", "results")

ORDER = ["vision", "coder", "security", "devops", "data",
         "reasoner", "academic", "chinese", "pm"]

CANDIDATES = [
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


def evaluate(table, rows):
    ok = 0
    wrong = []
    for r in rows:
        p = route(r["text"], table)
        if p == r["gold"]:
            ok += 1
        else:
            wrong.append({"text": r["text"], "gold": r["gold"], "pred": p})
    return ok / len(rows), wrong


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

    removed = []
    cur_acc, cur_wrong = evaluate(BASE, rows)
    trail = [{"round": 0, "removed": [], "accuracy": round(cur_acc, 4),
              "n_wrong": len(cur_wrong)}]
    history = [dict(BASE)]

    while True:
        best_word, best_acc, best_wrong = None, cur_acc, None
        cur_kw = BASE["coder"]
        for w in CANDIDATES:
            if w in removed:
                continue
            t = dict(BASE)
            t["coder"] = [x for x in cur_kw if x not in removed and x != w]
            a, wrong = evaluate(t, rows)
            if a > best_acc + 1e-12:
                best_word, best_acc, best_wrong = w, a, wrong
        if best_word is None:
            break
        removed.append(best_word)
        cur_acc, cur_wrong = best_acc, best_wrong
        trail.append({"round": len(removed), "removed": list(removed),
                      "accuracy": round(cur_acc, 4), "n_wrong": len(cur_wrong)})
        history.append({**BASE,
                        "coder": [x for x in BASE["coder"] if x not in removed]})

    # 全删 18 个作对照
    full = dict(BASE)
    full["coder"] = [x for x in BASE["coder"] if x not in CANDIDATES]
    full_acc, full_wrong = evaluate(full, rows)

    # 最终表
    final = dict(BASE)
    final["coder"] = [x for x in BASE["coder"] if x not in removed]
    final_acc, final_wrong = evaluate(final, rows)

    base_acc = trail[0]["accuracy"]

    lines = [
        "# coder 泛词贪心后向剔除", "",
        "- 评测集: `%s`（%d 条）" % (EVAL_PATH, len(rows)),
        "- 基线（不删任何词）: **%.1f%%**" % (base_acc * 100),
        "- 最优剔除集合: **%.1f%%**（%+.1fpp）" % (
            final_acc * 100, (final_acc - base_acc) * 100),
        "- 全删 18 个（对照）: **%.1f%%**（%+.1fpp）" % (
            full_acc * 100, (full_acc - base_acc) * 100), "",
        "## 贪心过程", "",
        "| 轮次 | 剔除的词 | 准确率 | 错判数 |",
        "|---|---|---|---|",
    ]
    for t in trail:
        lines.append("| %d | %s | %.1f%% | %d |" % (
            t["round"], "、".join(t["removed"]) or "（基线）",
            t["accuracy"] * 100, t["n_wrong"]))

    lines += ["", "## 决策", "",
              "| 方案 | 剔除词数 | 准确率 | Δ |",
              "|---|---|---|---|",
              "| 什么都不删（现状） | 0 | %.1f%% | — |" % (base_acc * 100),
              "| **贪心最优** | %d | **%.1f%%** | **%+.1fpp** |" % (
                  len(removed), final_acc * 100,
                  (final_acc - base_acc) * 100),
              "| 全删 18 个 | 18 | %.1f%% | %+.1fpp |" % (
                  full_acc * 100, (full_acc - base_acc) * 100), "",
              "**最终剔除的词**：%s" % ("、".join(removed) or "（无）"), "",
              "**保留的词**：%s" % "、".join(
                  w for w in CANDIDATES if w not in removed), "",
              "## 最终方案下的剩余错判（Top 15）", ""]
    fc = {}
    for w in final_wrong:
        k = "%s -> %s" % (w["gold"], w["pred"])
        fc[k] = fc.get(k, 0) + 1
    for k, v in sorted(fc.items(), key=lambda kv: -kv[1])[:15]:
        lines.append("- `%s`  ×%d" % (k, v))

    lines += ["", "## 错判样本全量", ""]
    for w in final_wrong:
        lines.append("- [%s→%s] %s" % (w["gold"], w["pred"], w["text"]))

    result = {
        "n_eval": len(rows),
        "baseline_accuracy": round(base_acc, 4),
        "greedy_removed": removed,
        "greedy_accuracy": round(final_acc, 4),
        "greedy_delta": round(final_acc - base_acc, 4),
        "remove_all_18_accuracy": round(full_acc, 4),
        "remove_all_18_delta": round(full_acc - base_acc, 4),
        "kept": [w for w in CANDIDATES if w not in removed],
        "trail": trail,
        "final_wrong": final_wrong,
        "final_coder_keywords": final["coder"],
    }
    with io.open(os.path.join(OUT_DIR, "opt_coder.json"), "w",
                 encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with io.open(os.path.join(OUT_DIR, "opt_coder.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("OK -> %s" % os.path.join(OUT_DIR, "opt_coder.md"))


if __name__ == "__main__":
    main()
