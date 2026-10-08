# -*- coding: utf-8 -*-
"""竞争式评分路由的回归测试（2026-10-08 算法切换后新增）。

## 为什么必须补这组测试

切换算法前，tests/test_expert_routing_regression.py 有 13 例，但**没有
一条断言具体路由结果** —— 全部在测 `_parse_route_key` 的解析与关键词
是否存在。把 route_expert 的算法整个换掉，那 13 例照样全绿，等于没有
回归保护。

本文件钉住的是「改算法修掉的那些结构性问题」，防止有人改回去：
  1. 首命中即胜 + 固定优先级（coder 排第一抢走安全/中文写作）
  2. knowledge 零关键词、结构上不可能胜出
  3. 中英文混排时空格导致 `sql注入` 匹配不上

指标与选型依据见 evals/results/route_variants.md（dev 池 495 条）。
"""
import pytest

from zeroai.core.constants import EXPERT_TEAM
from zeroai.core.expert_route import (
    _PRIORITY_IDX,
    _ROUTE_ORDER,
    _VISION_BONUS,
    _kw_hit,
    route_expert,
)


class TestScoringFixesFirstHitProblems:
    """旧算法「coder 排第一、首命中即返回」造成的错判必须不再复现。"""

    # 原判 coder（gold 是 chinese）：`写一个` 抢在 `公文` 之前
    def test_writing_task_beats_generic_verb(self):
        assert route_expert("写一个通知的公文") == "chinese"
        assert route_expert("帮我写一个自我介绍") == "chinese"

    # 原判 coder（gold 是 security）：`代码` 抢在 `审计` 之前
    def test_security_beats_coder_on_audit(self):
        assert route_expert("帮我做一次代码安全审计") == "security"

    # 原判 data（gold 是 pm）：data 位次靠前
    def test_pm_beats_data_on_task_analysis(self):
        assert route_expert("帮我把这个需求拆成几个任务") == "pm"


class TestSpaceNormalization:
    """中文正文里英文词周围常有空格，导致多词中文关键词匹配不上。"""

    def test_space_between_english_and_chinese(self):
        # 无去空格匹配时 `sql注入` 匹配不上「SQL 注入」，
        # 只剩 coder 的 `sql` 命中，安全问题被判给编程专家
        assert route_expert("SQL 注入的参数化查询怎么写") == "security"


class TestKwHit:
    """_kw_hit 的三种匹配规则。"""

    def test_ascii_long_word_uses_word_boundary(self):
        # 有词边界
        assert _kw_hit("function", "my function here") is True
        # 无词边界：不能把编码里的 code 抠出来
        assert _kw_hit("function", "functioning") is False

    def test_ascii_short_word_uses_substring(self):
        # 长度 <=4 的英文词刻意用子串匹配：中英混排时 \\b 会失效
        # （中文与英文字符同属 \\w）。代价是 `code` 会命中 `encode`，
        # 这是既有权衡，不是本次改动引入的。
        assert _kw_hit("code", "encode the thing") is True
        assert _kw_hit("code", "写 code 吗") is True

    def test_cjk_matches_without_space(self):
        assert _kw_hit("sql注入", "发现sql注入漏洞") is True
        assert _kw_hit("公文", "写一个公文") is True

    def test_cjk_matches_across_space_only_when_normalize(self):
        assert _kw_hit("sql注入", "sql 注入的查询", normalize=False) is False
        assert _kw_hit("sql注入", "sql 注入的查询", normalize=True) is True

    def test_normalize_does_not_break_ascii_word_boundary(self):
        # 英文长词仍走词边界，normalize 不应改变这一点
        assert _kw_hit("function", "my function here",
                       normalize=True) is True
        assert _kw_hit("function", "functioning",
                       normalize=True) is False


class TestKnowledgeCanWin:
    """旧实现里 knowledge 零关键词、不在遍历顺序里，结构上不可能胜出。"""

    def test_knowledge_has_keywords_now(self):
        kws = EXPERT_TEAM["knowledge"]["keywords"]
        assert kws, "knowledge 若无词表就只能当兜底，将重回旧行为"
        assert "为什么" in kws, (
            "「为什么」必须在 knowledge 词表里：否则事实性「为什么…」"
            "会被 reasoner 的同词短路，而短路到错专家是不可恢复的")

    def test_factual_why_routes_to_knowledge(self):
        assert route_expert("为什么天空是蓝色的") == "knowledge"
        assert route_expert("为什么海水是咸的") == "knowledge"

    def test_factual_what_routes_to_knowledge(self):
        assert route_expert("光合作用的产物是什么") == "knowledge"
        assert route_expert("什么时候开始下雪") == "knowledge"

    def test_pure_garbage_falls_back_to_knowledge(self):
        assert route_expert("fjdkslfjdsqweqwe") == "knowledge"

    def test_knowledge_tie_break_precedes_reasoner(self):
        # knowledge 位次必须在 reasoner 之前，否则「为什么」撞分时
        # 必然输给 reasoner，TestKnowledgeCanWin 会失败
        assert _PRIORITY_IDX["knowledge"] < _PRIORITY_IDX["reasoner"]


class TestVisionPriorityPreserved:
    """vision 必须仍然压过 coder 的「看/打开/浏览」类词。"""

    @pytest.mark.parametrize("text", [
        "这张截图里的报错信息是什么",
        "帮我看看这张照片拍得怎么样",
        "识别一下这张图上有几个人",
    ])
    def test_image_requests_go_to_vision(self, text):
        assert route_expert(text) == "vision"

    def test_vision_bonus_is_large_enough(self):
        # 加成必须远大于任何专家可能的命中词数（词表最长约 40）
        maxlen = max(len(v["keywords"]) for v in EXPERT_TEAM.values())
        assert _VISION_BONUS > maxlen * 2, (
            "vision 加成过小会让它在与其他专家撞分时输掉")


class TestDeterminism:
    """评分并列时必须由固定顺序裁决，不能依赖 dict/set 迭代顺序。"""

    def test_route_is_fixed_tuple(self):
        assert isinstance(_ROUTE_ORDER, tuple)
        assert len(set(_ROUTE_ORDER)) == len(_ROUTE_ORDER)

    def test_repeated_calls_identical(self):
        for text in ["写一个通知的公文", "SQL 注入的怎么防",
                     "帮我把这个需求拆成几个任务"]:
            outs = {route_expert(text) for _ in range(30)}
            assert len(outs) == 1, "同输入 30 次结果不一致: %s" % outs

    def test_returns_known_expert_only(self):
        known = set(EXPERT_TEAM)
        for text in ["写代码", "为什么天空是蓝色的", "这张图片", "你好",
                     "帮我规划项目", "查漏洞", "画个图", "写篇论文"]:
            assert route_expert(text) in known


class TestNoUnknownKeyLeak:
    """keywords 里若混入非字符串或空串，会污染计分。"""

    @pytest.mark.parametrize("key", list(EXPERT_TEAM))
    def test_keywords_are_nonempty_strings(self, key):
        kws = EXPERT_TEAM[key]["keywords"]
        assert isinstance(kws, list)
        for kw in kws:
            assert isinstance(kw, str) and kw.strip(), (
                "%s 的关键词含空值: %r" % (key, kw))

    @pytest.mark.parametrize("key", list(EXPERT_TEAM))
    def test_keywords_have_no_duplicates(self, key):
        kws = EXPERT_TEAM[key]["keywords"]
        dup = {k for k in kws if kws.count(k) > 1}
        # security 的「漏洞」是改动前就存在的既有重复，route_expert 里
        # 已做去重，不影响计分；此处只拦新增的重复。
        allowed = {"漏洞"} if key == "security" else set()
        assert dup <= allowed, (
            "%s 关键词有重复（会让命中词数计分虚高）: %s" % (key, dup - allowed))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
