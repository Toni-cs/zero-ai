# -*- coding: utf-8 -*-
"""在最终测试集上对比旧系统与新系统 —— **只允许执行一次，调优已完成**。

会先校验 routing_test.jsonl 的 sha256 与 lock 文件一致，确保测试集
在调优期间未被改动；不一致直接拒绝执行。

输出 evals/results/test_final.md
"""
import hashlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.route_variants import load, metrics  # noqa: E402

TEST = "evals/routing_test.jsonl"
LOCK = "evals/results/test_set_lock.json"
OUT = "evals/results/test_final.md"


def main():
    lock = json.load(io.open(LOCK, encoding="utf-8"))
    actual = hashlib.sha256(io.open(TEST, "rb").read()).hexdigest()
    assert actual == lock["sha256"], (
        "测试集哈希不符，拒绝执行：\n  lock=%s\n  实际=%s"
        % (lock["sha256"], actual))

    rows = load([TEST])
    res = {}
    lines = [
        "# 最终测试集结果（routing_test.jsonl，n=%d）" % len(rows),
        "",
        "- sha256: `%s`（与 lock 一致，调优期间未改动）" % actual,
        "- 调优仅使用 dev 池（routing_eval + routing_holdout，495 条）；",
        "  本集合的错判明细**不导出**，以保其在未来仍可用于复核。",
        "",
        "## 旧系统 vs 新系统",
        "",
        "| 系统 | l1_acc | defer_rate | **unsafe_rate** | 短路到错专家 | 短句defer |",
        "|---|---|---|---|---|---|",
    ]
    for name, v in [
        ("旧系统 V0（首命中+固定优先级+旧词表）", "V0"),
        ("新系统 V6live（竞争式评分+新词表）", "V6live"),
    ]:
        r = metrics(rows, v)
        res[v] = r
        lines.append("| %s | %.4f | %.4f | **%.4f** | %d | %d |"
                     % (name, r["l1_acc"], r["defer_rate"], r["unsafe_rate"],
                        r["wrong_shortcircuit"], r["short_defer_err"]))

    b, a = res["V0"], res["V6live"]
    lines += [
        "",
        "## 相对旧系统的变化",
        "",
        "| 指标 | Δ |",
        "|---|---|",
        "| l1_acc | **%+.4f** |" % (a["l1_acc"] - b["l1_acc"]),
        "| unsafe_rate（用户实际收到错答案） | **%+.4f** |"
        % (a["unsafe_rate"] - b["unsafe_rate"]),
        "| 短路到错专家 | **%+d** |"
        % (a["wrong_shortcircuit"] - b["wrong_shortcircuit"]),
        "",
        "## 指标定义",
        "",
        "- `l1_acc` L1 关键词路由单独的准确率，与历史报告的 79.7% 同口径",
        "- `defer_rate` L1 判为 knowledge、交给 L2 GLM 的比例",
        "  （= 额外的 1-2 秒延迟与一次 API 调用）",
        "- `unsafe_rate` **用户实际收到错误答案**的比例。长输入下 defer",
        "  到 knowledge 会被 L2 纠正，不算错误；短句 defer 与短路到错",
        "  专家都算",
        "",
        "## 已知前提（不可忽略）",
        "",
        "unsafe_rate 的口径**依赖「L2 GLM 能纠正 defer」这一假设**，",
        "而本环境没有 OpenRouter/GLM 的可用 key，**该假设未经实测**。",
        "l1_acc 不依赖该假设，是无条件成立的数字。",
    ]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
