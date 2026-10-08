# -*- coding: utf-8 -*-
"""评测集完整性守卫。

背景：路由准确率曾出现 25pp 的泛化缺口 —— dev 集 79.7%，而在全新
文本的留出集上只有 54.7%。原因是 dev 集在多轮关键词调优中已经与
关键词表高度耦合，等于既当训练集又当测试集。

为杜绝这种情况再次发生，评测被拆成三级：

  routing_eval.jsonl     (217)  dev      可看错判明细、可用于调优
  routing_holdout.jsonl  (278)  dev 扩充 已看过明细，视为开发集
  routing_test.jsonl     (284)  最终测试 **禁止看明细，只跑一次**

本测试保证：
1. routing_test.jsonl 的哈希与 lock 文件一致 —— 想改测试集必须
   先改 lock 文件，那会在 git diff 里留下痕迹
2. 三个集合两两无文本重叠 —— 防止"测试题其实练过"
3. 每个集合内部无重复文本
"""
import hashlib
import io
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = os.path.join(ROOT, "evals", "routing_eval.jsonl")
HOLD = os.path.join(ROOT, "evals", "routing_holdout.jsonl")
TEST = os.path.join(ROOT, "evals", "routing_test.jsonl")
LOCK = os.path.join(ROOT, "evals", "results", "test_set_lock.json")


def _load(path):
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _sha(path):
    with io.open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_test_set_exists_and_is_locked():
    assert os.path.exists(TEST), "最终测试集不存在"
    assert os.path.exists(LOCK), (
        "缺 evals/results/test_set_lock.json —— 测试集未锁，"
        "任何结论都不可信")
    lock = json.load(io.open(LOCK, encoding="utf-8"))
    actual = _sha(TEST)
    assert actual == lock["sha256"], (
        "最终测试集 routing_test.jsonl 的哈希与 lock 文件不一致。\n"
        "  lock : %s\n"
        "  实际 : %s\n"
        "测试集一旦在调优过程中被改动，它就不再是测试集。"
        "若确为有意重建，请同步更新 lock 文件并在 commit message "
        "里说明理由。" % (lock["sha256"], actual))
    rows = _load(TEST)
    assert len(rows) == lock["n"], "测试集条数与 lock 不一致"
    # 基本格式校验
    for r in rows:
        assert set(r) >= {"text", "gold"}, "样本缺字段: %r" % r
        assert r["gold"], "gold 为空: %r" % r


def test_three_sets_are_pairwise_disjoint():
    """三个集合两两无重叠 —— 测试题不能是练过的题。"""
    sets = {}
    for name, path in (("dev", DEV), ("holdout", HOLD), ("test", TEST)):
        if not os.path.exists(path):
            pytest.skip("%s 不存在" % path)
        sets[name] = {r["text"] for r in _load(path)}

    for a in sets:
        for b in sets:
            if a >= b:
                continue
            overlap = sets[a] & sets[b]
            assert not overlap, (
                "%s 与 %s 存在 %d 条文本重叠，例如：%s"
                % (a, b, len(overlap), list(overlap)[:3]))


@pytest.mark.parametrize("path", [DEV, HOLD, TEST])
def test_each_set_has_no_internal_duplicates(path):
    if not os.path.exists(path):
        pytest.skip("%s 不存在" % path)
    texts = [r["text"] for r in _load(path)]
    dup = {t for t in texts if texts.count(t) > 1}
    assert not dup, "%s 内部重复: %s" % (os.path.basename(path), list(dup)[:3])


@pytest.mark.parametrize("path", [DEV, HOLD, TEST])
def test_gold_labels_are_known_experts(path):
    if not os.path.exists(path):
        pytest.skip("%s 不存在" % path)
    import sys
    sys.path.insert(0, ROOT)
    from zeroai.core.constants import EXPERT_TEAM
    known = set(EXPERT_TEAM)
    bad = [(r["text"], r["gold"]) for r in _load(path)
           if r["gold"] not in known]
    assert not bad, "存在未知专家标签: %s" % bad[:5]
