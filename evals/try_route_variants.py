# -*- coding: utf-8 -*-
"""在 **dev 池**上跑全部路由变体，输出对比表。

dev 池 = routing_eval.jsonl + routing_holdout.jsonl（995->495 条）
routing_test.jsonl 在此处被硬性排除，且带断言防止误用。

输出 evals/results/route_variants.md
"""
import io
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.route_variants import load, metrics, route_variant  # noqa: E402

DEV_POOL = [os.path.join("evals", "routing_eval.jsonl"),
            os.path.join("evals", "routing_holdout.jsonl")]
LOCKED = os.path.join("evals", "routing_test.jsonl")
OUT = os.path.join("evals", "results", "route_variants.md")

VARIANTS = ["V0", "V1", "V2", "V3", "V4", "V5a", "V5b", "V5c", "V6", "V6live"]


def main():
    for p in DEV_POOL:
        assert os.path.exists(p), "缺 %s" % p
    # 硬性排除测试集
    assert LOCKED not in DEV_POOL
    if os.path.exists(LOCKED):
        import hashlib
        lock = json.load(io.open(
            os.path.join("evals", "results", "test_set_lock.json"),
            encoding="utf-8"))
        h = hashlib.sha256(io.open(LOCKED, "rb").read()).hexdigest()
        assert h == lock["sha256"], "测试集哈希不符，调优前不得接触测试集"

    rows = load(DEV_POOL)
    print("dev pool n =", len(rows))

    res = {v: metrics(rows, v) for v in VARIANTS}

    L = ["# 路由变体对比（dev 池 = dev + holdout，%d 条）" % len(rows), "",
         "> **routing_test.jsonl 未参与本表任何计算**（哈希已校验）。", "",
         "## 指标含义", "",
         "- `l1_acc`      L1 关键词路由单独的准确率，与历史 79.7% 可比",
         "- `defer_rate`  L1 判为 knowledge、交给 L2 GLM 的比例（= 延迟与 API 成本）",
         "- `unsafe_rate` **用户实际收到错误答案**的比例。"
         "长输入下 defer 到 knowledge 会被 L2 纠正，不算错误；"
         "短句 defer 与短路到错专家都算",
         "- `wrong_shortcircuit` 短路到错误专家的条数（最严重的一类）",
         "", "## 结果", "",
         "| 变体 | l1_acc | defer_rate | **unsafe_rate** | 短路到错专家 | 短句defer无纠正 |",
         "|---|---|---|---|---|---|"]
    for v in VARIANTS:
        r = res[v]
        L.append("| %s | %.4f | %.4f | **%.4f** | %d | %d |"
                 % (v, r["l1_acc"], r["defer_rate"], r["unsafe_rate"],
                    r["wrong_shortcircuit"], r["short_defer_err"]))

    base = res["V0"]
    L += ["", "## 相对 V0（现状）的变化", "",
          "| 变体 | Δl1_acc | Δunsafe_rate | Δ短路到错专家 |",
          "|---|---|---|---|"]
    for v in VARIANTS:
        r = res[v]
        L.append("| %s | %+.4f | %+.4f | %+d |"
                 % (v, r["l1_acc"] - base["l1_acc"],
                    r["unsafe_rate"] - base["unsafe_rate"],
                    r["wrong_shortcircuit"] - base["wrong_shortcircuit"]))

    # 分集合看，防止只在其中一个集合上变好
    L += ["", "## 分集合（检查是否只在某一个集合上过拟合）", "",
          "| 变体 | dev(217) l1_acc | holdout(278) l1_acc | 两集合差 |",
          "|---|---|---|---|"]
    sets = {p: load([p]) for p in DEV_POOL}
    for v in VARIANTS:
        a = metrics(sets[DEV_POOL[0]], v)["l1_acc"]
        b = metrics(sets[DEV_POOL[1]], v)["l1_acc"]
        L.append("| %s | %.4f | %.4f | %+.4f |" % (v, a, b, a - b))

    # 最优变体的错判混淆对
    best_v = min(VARIANTS, key=lambda v: res[v]["unsafe_rate"])
    L += ["", "## unsafe_rate 最低的变体：%s" % best_v, "",
          "### 混淆对", "", "| gold | pred | n |", "|---|---|---|"]
    pairs = Counter((g, p) for g, p, _ in res[best_v]["errors"])
    for (g, p), n in pairs.most_common(20):
        L.append("| `%s` | `%s` | %d |" % (g, p, n))
    L += ["", "### 仍短路到错专家的样本（前 40 条）", ""]
    shown = 0
    for g, p, t in res[best_v]["errors"]:
        if p == "knowledge":
            continue
        L.append("- [`%s`→`%s`] %s" % (g, p, t))
        shown += 1
        if shown >= 40:
            break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("OK ->", OUT)
    print()
    print("%-4s %8s %8s %8s %8s %8s"
          % ("var", "l1_acc", "defer", "unsafe", "wrongSC", "shortDef"))
    for v in VARIANTS:
        r = res[v]
        print("%-4s %8.4f %8.4f %8.4f %8d %8d"
              % (v, r["l1_acc"], r["defer_rate"], r["unsafe_rate"],
                 r["wrong_shortcircuit"], r["short_defer_err"]))


if __name__ == "__main__":
    main()
