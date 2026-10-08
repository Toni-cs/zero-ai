# -*- coding: utf-8 -*-
"""统计检验函数的正确性 + 路由改进显著性的回归守卫。

两组断言：
1. 数学正确性 —— 用**可手算的**已知用例钉死 McNemar 精确检验、
   配对比例差的 Wald 区间、Wilson 区间。算错任何一步，
   evals/results/significance.md 里的 p 值就不可信。
2. 结论有效性 —— 当前测试集上改进必须仍显著（p<0.05 且 CI 不含 0）。
   若有人改动路由后 p 值变大，说明改进退化了，应当立刻失败。
"""
import io
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.statistical_tests import (  # noqa: E402
    mcnemar_chi2_cc,
    mcnemar_exact,
    stars,
    wilson_ci,
    wald_ci_diff,
)

TEST = os.path.join(ROOT, "evals", "routing_test.jsonl")
LOCK = os.path.join(ROOT, "evals", "results", "test_set_lock.json")


# ────────────────── 1. 数学正确性 ──────────────────

def test_mcnemar_all_discordant_one_direction():
    """b=10, c=0：精确 p = 2 * (1/2)^10 = 0.001953125。"""
    p = mcnemar_exact(10, 0)
    assert abs(p - 2 * (0.5 ** 10)) < 1e-12


def test_mcnemar_is_symmetric_in_b_c():
    """交换 b 与 c 不改变 p（检验的是不一致的方向偏离，对称）。"""
    for b, c in [(3, 10), (7, 7), (0, 0), (25, 4), (1, 100)]:
        assert abs(mcnemar_exact(b, c) - mcnemar_exact(c, b)) < 1e-12, \
            "(%d,%d) 不对称" % (b, c)


def test_mcnemar_perfect_balance_gives_one():
    """b == c 时没有任何方向偏离，p 必须等于 1。"""
    for k in (0, 1, 2, 10, 50):
        assert abs(mcnemar_exact(k, k) - 1.0) < 1e-9, "b=c=%d" % k


def test_mcnemar_extreme_imbalance_is_significant():
    assert mcnemar_exact(30, 0) < 0.001
    assert mcnemar_exact(0, 30) < 0.001


def test_mcnemar_p_bounded():
    for b in range(0, 40):
        for c in range(0, 40):
            p = mcnemar_exact(b, c)
            assert 0.0 <= p <= 1.0, "(%d,%d) -> %s" % (b, c, p)


def test_mcnemar_chi2_agrees_with_exact_on_moderate_sample():
    """b+c 足够大时两种方法应同向（本例 b+c=82）。"""
    for b, c in [(40, 10), (68, 14), (10, 40)]:
        pe, pc = mcnemar_exact(b, c), mcnemar_chi2_cc(b, c)
        assert (pe < 0.05) == (pc < 0.05), "(%d,%d) 两者结论冲突" % (b, c)


def test_stars_thresholds():
    assert stars(0.049) == "*"
    assert stars(0.009) == "**"
    assert stars(0.0009) == "***"
    assert stars(0.06) == "n.s."


def test_wald_diff_matches_point_estimate():
    n, b, c = 284, 68, 14
    d, se, ci = wald_ci_diff(n, b, c)
    assert abs(d - (b - c) / n) < 1e-15
    assert se > 0
    assert ci[0] < d < ci[1], "点估计必须落在区间内部"
    assert abs((ci[1] - ci[0]) / 2 - 1.959963984540054 * se) < 1e-12


def test_wald_ci_excludes_zero_iff_significant():
    """b+c 足够大且 |b-c| 足够大时区间不含 0；b≈c 时必须含 0。"""
    d, se, ci = wald_ci_diff(284, 68, 14)
    assert ci[0] > 0
    d, se, ci = wald_ci_diff(284, 141, 139)
    assert ci[0] < 0 < ci[1], "b≈c 时区间必须跨 0"


def test_wilson_ci_sane():
    lo, hi = wilson_ci(50, 100)
    assert 0.0 <= lo < 0.5 < hi <= 1.0
    assert abs((lo + hi) / 2 - 0.5) < 0.01  # 近似对称
    # 极端比例仍落在 [0,1]（k=0 时下界数学上恰为 0，浮点下是 ~1e-18）
    assert wilson_ci(0, 100)[0] >= 0.0
    assert wilson_ci(0, 100)[0] < 1e-12
    assert wilson_ci(100, 100)[1] <= 1.0
    assert wilson_ci(100, 100)[1] > 1.0 - 1e-12
    assert wilson_ci(0, 0) == (0.0, 0.0)


# ────────────────── 2. 结论有效性（回归守卫） ──────────────────

def _load_test():
    lock = json.load(io.open(LOCK, encoding="utf-8"))
    import hashlib
    actual = hashlib.sha256(io.open(TEST, "rb").read()).hexdigest()
    assert actual == lock["sha256"], "测试集被改动，结论失效"
    return [json.loads(l) for l in io.open(TEST, encoding="utf-8") if l.strip()]


def test_improvement_remains_significant():
    """改动路由后，测试集上的改进必须仍显著。

    这不是"数字必须一模一样"，而是三条底线：
      p < 0.05、95% CI 不含 0、净改善 b-c > 0。
    任何一条被打破都说明改进退化了，应当立刻暴露。
    """
    from evals.route_variants import route_variant
    rows = _load_test()
    old_ok = [route_variant(r["text"], "V0") == r["gold"] for r in rows]
    new_ok = [route_variant(r["text"], "V6live") == r["gold"] for r in rows]
    b = sum(1 for o, w in zip(old_ok, new_ok) if not o and w)
    c = sum(1 for o, w in zip(old_ok, new_ok) if o and not w)

    assert b > c, "净改善不再是正的（b=%d, c=%d）" % (b, c)
    p = mcnemar_exact(b, c)
    assert p < 0.05, "McNemar p=%.4g 不再显著" % p
    d, se, ci = wald_ci_diff(len(rows), b, c)
    assert ci[0] > 0, "95%% CI 下界 %.4f 已不为正" % ci[0]


def test_new_system_beats_baselines():
    from evals.route_variants import route_variant
    rows = _load_test()
    gold = [r["gold"] for r in rows]
    n = len(rows)
    new_acc = sum(1 for r in rows
                  if route_variant(r["text"], "V6live") == r["gold"]) / n
    old_acc = sum(1 for r in rows
                  if route_variant(r["text"], "V0") == r["gold"]) / n
    majority = max(gold.count(g) for g in set(gold)) / n

    assert new_acc > old_acc, "新系统未超过旧系统"
    assert new_acc > majority, "新系统未超过多数类基线"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
