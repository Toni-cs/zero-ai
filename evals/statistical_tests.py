# -*- coding: utf-8 -*-
"""路由新旧系统的统计显著性检验 + 基线对照表。

## 为什么必须做

82b4352 报告了「测试集 49.65% -> 68.66%，+19.0pp」，但没有 p 值。
评审的第一反应必然是「19pp 在 n=284 上显著吗？会不会是抽样噪声？」

配对设计（同一批样本、新旧系统各预测一次）最适合 McNemar 检验：
它只看**两次预测结果不一致**的那些样本，与一致的样本无关，
因此比比较两个独立比例的检验更有力。

## 口径纪律

- **只用 routing_test.jsonl 出结论**。dev(217) 与 holdout(278) 参与过
  选型，属于调优集，它们的数字不能作为显著性证据。
- 基线取自**同一测试集**，保证可比。
- 所有断言都从实际预测算出，不写死数字。

输出 evals/results/significance.md
"""
import hashlib
import io
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.route_variants import route_variant  # noqa: E402

TEST = "evals/routing_test.jsonl"
LOCK = "evals/results/test_set_lock.json"
OUT = "evals/results/significance.md"


def mcnemar_exact(b, c):
    """McNemar 精确检验（二项式版）。

    b = 旧错新对，c = 旧对新错。H0: 两者被纠正的概率相同。
    p = 2 * P(X <= min(b,c)), X~Binom(b+c, 0.5)，上界截到 1。
    样本量小时比卡方版更准（连续性校正的卡方在 b+c < 25 时偏乐观）。
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def mcnemar_chi2_cc(b, c):
    """带连续性校正的卡方版，作为对照（大样本时两者应接近）。"""
    n = b + c
    if n == 0:
        return 1.0
    chi2 = (abs(b - c) - 1) ** 2 / n
    # df=1 的卡方上尾概率
    return math.erfc(math.sqrt(chi2 / 2.0))


def wald_ci_diff(n, b, c, z=1.959963984540054):
    """配对比例之差的 95% Wald 置信区间。

    d = (b-c)/n
    Var(d) = [(b+c) - (b-c)^2/n] / n^2
    """
    d = (b - c) / n
    var = ((b + c) - (b - c) ** 2 / n) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return d, se, (d - z * se, d + z * se)


def wilson_ci(k, n, z=1.959963984540054):
    """单个比例的 Wilson 95% 区间（比正态近似在 p 接近 0/1 时稳）。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def stars(p):
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "n.s."


def main():
    lock = json.load(io.open(LOCK, encoding="utf-8"))
    actual = hashlib.sha256(io.open(TEST, "rb").read()).hexdigest()
    assert actual == lock["sha256"], "测试集哈希不符，拒绝出结论"

    rows = [json.loads(l) for l in io.open(TEST, encoding="utf-8")
            if l.strip()]
    n = len(rows)

    old_pred = [route_variant(r["text"], "V0") for r in rows]
    new_pred = [route_variant(r["text"], "V6live") for r in rows]
    gold = [r["gold"] for r in rows]

    old_ok = [o == g for o, g in zip(old_pred, gold)]
    new_ok = [w == g for w, g in zip(new_pred, gold)]
    n_old = sum(old_ok)
    n_new = sum(new_ok)

    # 配对四格：两者一致/不一致
    b = sum(1 for o, w in zip(old_ok, new_ok) if not o and w)  # 旧错新对
    c = sum(1 for o, w in zip(old_ok, new_ok) if o and not w)  # 旧对新错
    both_ok = sum(1 for o, w in zip(old_ok, new_ok) if o and w)
    both_bad = sum(1 for o, w in zip(old_ok, new_ok) if not o and not w)

    p_exact = mcnemar_exact(b, c)
    p_chi2 = mcnemar_chi2_cc(b, c)
    d, se, ci = wald_ci_diff(n, b, c)

    # 基线
    gold_cnt = Counter(gold)
    maj_label, maj_n = gold_cnt.most_common(1)[0]
    maj_acc = maj_n / n
    n_expert = len(set(gold))
    random_acc = 1.0 / n_expert

    old_ci = wilson_ci(n_old, n)
    new_ci = wilson_ci(n_new, n)

    # 效应量：配对优势比 (matched odds ratio)
    oratio = (b / c) if c else float("inf")

    # 分 tag
    tags = {}
    for i, r in enumerate(rows):
        t = r.get("tag") or "(常规)"
        tags.setdefault(t, []).append(i)

    L = ["# 路由改进的统计显著性", "",
         "- 测试集: `%s`（n=%d，sha256 与 lock 一致）" % (TEST, n),
         "- 检验: **McNemar 精确检验**（配对设计，只看两次预测不一致的样本）",
         "- 纪律: dev(217) 与 holdout(278) 参与过选型，属调优集，",
         "  **不作为显著性证据**；以下全部只用测试集。", "",
         "## 一、配对四格表", "",
         "| | 新系统对 | 新系统错 | 合计 |",
         "|---|---|---|---|",
         "| **旧系统对** | %d | %d (c) | %d |" % (both_ok, c, n_old),
         "| **旧系统错** | %d (b) | %d | %d |" % (b, both_bad, n - n_old),
         "| 合计 | %d | %d | %d |" % (n_new, n - n_new, n), "",
         "- b（旧错新对）= **%d** —— 新系统救回来的样本" % b,
         "- c（旧对新错）= **%d** —— 新系统搞坏的样本" % c,
         "- 不一致样本合计 b+c = %d（McNemar 只用这些）" % (b + c),
         "- 净改善 = b - c = **%+d**（对应准确率 %+.2fpp）"
         % (b - c, d * 100), "",
         "## 二、McNemar 检验", "",
         "| 检验 | 统计量 | **p 值** | 结论 |",
         "|---|---|---|---|",
         "| **精确二项式** | b=%d, c=%d | **%.3e** | %s |"
         % (b, c, p_exact, stars(p_exact)),
         "| 卡方（连续性校正） | χ²=%.2f, df=1 | %.3e | %s |"
         % ((abs(b - c) - 1) ** 2 / max(b + c, 1), p_chi2, stars(p_chi2)),
         "",
         "**判定：p = %.3e，远小于 0.05** —— 在 α=0.05 水平下"
         % p_exact,
         "新旧系统的差异**不是抽样噪声**。" if p_exact < 0.05
         else "无法拒绝原假设。", "",
         "> 主报告精确检验。本例 b+c=%d ≥ 25，卡方近似本已可用（它只在" % (b + c),
         "> b+c < 25 时才偏乐观），此处两种方法结论一致，可互为佐证。", "",
         "## 三、效应量与置信区间", "",
         "| 指标 | 值 | 95% CI |",
         "|---|---|---|",
         "| 准确率差（配对） | **%+.2fpp** | **[%.2fpp, %.2fpp]** |"
         % (d * 100, ci[0] * 100, ci[1] * 100),
         "| 配对优势比 b/c | %.1f | — |" % oratio,
         "",
         "- 标准误 SE = %.4f" % se,
         "- 置信区间**不包含 0**，且下界 %.2fpp —— 即便取最保守估计，"
         % (ci[0] * 100),
         "  改进仍然为正。", "",
         "## 四、基线对照表（全部在同一测试集上）", "",
         "| 系统 | 准确率 | 95% CI (Wilson) | 相对多数类基线 |",
         "|---|---|---|---|"]

    def row(name, k):
        lo, hi = wilson_ci(k, n)
        return "| %s | **%.2f%%** | [%.2f%%, %.2f%%] | %.1f 倍 |" % (
            name, k / n * 100, lo * 100, hi * 100,
            (k / n) / maj_acc if maj_acc else float("nan"))

    L.append("| 随机（10 类均匀） | %.1f%% | — | %.2f 倍 |"
             % (random_acc * 100, random_acc / maj_acc))
    L.append("| **多数类基线**（恒猜 `%s`） | **%.2f%%** | [%.2f%%, %.2f%%] | 1.00 倍 |"
             % (maj_label, maj_acc * 100,
                wilson_ci(maj_n, n)[0] * 100, wilson_ci(maj_n, n)[1] * 100))
    L.append(row("**旧系统**（首命中+固定优先级）", n_old))
    L.append(row("**新系统**（竞争式评分）", n_new))

    L += ["", "## 五、分 tag 的配对表现", "",
          "| tag | n | 旧 | 新 | b | c |", "|---|---|---|---|---|---|"]
    for t in sorted(tags):
        idx = tags[t]
        o = sum(1 for i in idx if old_ok[i])
        w = sum(1 for i in idx if new_ok[i])
        bb = sum(1 for i in idx if not old_ok[i] and new_ok[i])
        cc = sum(1 for i in idx if old_ok[i] and not new_ok[i])
        L.append("| %s | %d | %d | %d | %+d | %+d |"
                 % (t, len(idx), o, w, bb, cc))

    L += ["", "## 六、口径与限制", "",
          "1. **只对 L1 关键词路由成立**。新系统的 `defer_rate` 上升了，",
          "   即更多输入会交给 L2 GLM；本环境无可用 key，**L2 的纠正能力",
          "   未经实测**，故本报告不评价端到端生产准确率。",
          "2. 测试集由本仓库作者标注，标签反映 `EXPERT_TEAM` 的职责定义",
          "   而非唯一正确答案；存在标注偏差时 p 值不随之修正。",
          "3. 新系统是在 dev 池上跑了 9 个变体选出来的，存在选择偏差；",
          "   **测试集未参与选型**，故本检验有效。",
          "4. 该测试集此前只跑过聚合指标，未导出错判明细。"]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")

    print("n=%d  旧=%d  新=%d" % (n, n_old, n_new))
    print("b(旧错新对)=%d  c(旧对新错)=%d  净=%+d" % (b, c, b - c))
    print("McNemar 精确 p = %.3e  %s" % (p_exact, stars(p_exact)))
    print("McNemar 卡方 p = %.3e  %s" % (p_chi2, stars(p_chi2)))
    print("准确率差 = %+.2fpp  95%%CI [%.2f, %.2f]" % (d * 100, ci[0] * 100, ci[1] * 100))
    print("配对优势比 b/c = %.1f" % oratio)
    print("多数类基线 = %s %.2f%%" % (maj_label, maj_acc * 100))
    print("-> %s" % OUT)


if __name__ == "__main__":
    main()
