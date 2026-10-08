# -*- coding: utf-8 -*-
"""专家路由回归测试

锁定本轮修复的三处缺陷，防止回退：

1. 同名路由函数在 TUI（expert_route）与 headless（expert）两条路径上
   使用不同的数据源与算法，准确率相差 26.2pp（48.9% vs 75.1%）。
2. route_by_glm 解析 LLM 回复时遍历 set，顺序受 PYTHONHASHSEED 影响，
   多命中时同一输入在不同进程会路由到不同专家。
3. 同处用裸子串匹配，"npm" 命中 "pm"、"dataviz" 命中 "data"。

证据：evals/results/summary.md、evals/results/ablation.md
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core.constants import EXPERT_TEAM  # noqa: E402
from zeroai.core.expert import (  # noqa: E402
    _ROUTE_KEY_ORDER,
    ExpertRouter,
    get_expert_router,
)
from zeroai.core import expert_route  # noqa: E402


# 经评测集覆盖的代表性输入
SAMPLE_INPUTS = [
    "帮我写一个 Python 函数，把列表去重",
    "帮我推导这个公式的证明",
    "这张图片里是什么",
    "帮我做数据分析，看看销售趋势",
    "帮我部署这个服务到服务器",
    "这个系统有什么安全漏洞",
    "帮我推理一下这个问题的逻辑",
    "帮我分析一下这个任务",
    "地球到月球有多远",
    "帮我写一篇关于春天的作文",
    "看看这张截图里的代码哪里错了",
    "帮我总结这篇文献的主要观点",
    "你好",
    "写一个数据清洗的脚本",
]


class TestSingleSourceOfTruth:
    """数据源统一：expert.ExpertRouter 不再读 config.yaml"""

    def test_expert_team_is_single_source(self):
        router = get_expert_router()
        assert router._expert_team is EXPERT_TEAM, (
            "ExpertRouter._expert_team 必须直接引用 constants.EXPERT_TEAM，"
            "读 config.yaml 会造成关键词与 system_prompt 双重漂移"
        )

    def test_routing_sources_agree(self):
        """两条路径对同一输入必须给出同一专家"""
        router = get_expert_router()
        mismatched = []
        for text in SAMPLE_INPUTS:
            a = router.route_by_keywords(text)
            b = expert_route.route_expert(text)
            if a != b:
                mismatched.append((text, a, b))
        assert not mismatched, (
            "两套路由结果不一致（TUI vs headless）：%s" % mismatched
        )

    def test_result_is_deterministic(self):
        """重复调用必须稳定返回同一结果"""
        router = get_expert_router()
        for text in SAMPLE_INPUTS:
            first = router.route_by_keywords(text)
            for _ in range(5):
                assert router.route_by_keywords(text) == first


class TestRouteKeyParsing:
    """route_by_glm 的 LLM 回复解析"""

    def test_exact_match(self):
        for key in EXPERT_TEAM:
            assert ExpertRouter._parse_route_key(key) == key

    def test_no_substring_false_positive(self):
        """裸子串匹配会让 npm 命中 pm、dataviz 命中 data"""
        assert ExpertRouter._parse_route_key("npm") is None, (
            "'npm' 不应被解析为 'pm'"
        )
        assert ExpertRouter._parse_route_key("dataviz") is None, (
            "'dataviz' 不应被解析为 'data'"
        )
        assert ExpertRouter._parse_route_key("coderlang") is None

    def test_multi_match_is_order_deterministic(self):
        """多标识同现时，结果必须由固定顺序决定而非 set 随机顺序"""
        # _ROUTE_KEY_ORDER 中 coder 排在 pm 之前
        assert _ROUTE_KEY_ORDER.index("coder") < _ROUTE_KEY_ORDER.index("pm")
        results = {ExpertRouter._parse_route_key("coder pm") for _ in range(20)}
        assert len(results) == 1, (
            "同一输入解析出多个结果：%s（set 迭代顺序未固定）" % results
        )
        assert results.pop() == "coder"

    def test_order_is_fixed_tuple_not_set(self):
        assert isinstance(_ROUTE_KEY_ORDER, tuple)
        assert len(set(_ROUTE_KEY_ORDER)) == len(_ROUTE_KEY_ORDER)

    def test_strips_punctuation_and_case(self):
        assert ExpertRouter._parse_route_key("Coder.") == "coder"
        assert ExpertRouter._parse_route_key("  REASONER  ") == "reasoner"

    def test_unknown_returns_none(self):
        assert ExpertRouter._parse_route_key("") is None
        assert ExpertRouter._parse_route_key("   ") is None
        assert ExpertRouter._parse_route_key("nonexistent") is None


class TestCoderKeywordsTuned:
    """关键词经贪心后向剔除调优（evals/results/opt_coder.md）"""

    REMOVED = ["配置", "看看", "查看", "读取", "项目"]

    def test_harmful_words_removed(self):
        kws = EXPERT_TEAM["coder"]["keywords"]
        for w in self.REMOVED:
            assert w not in kws, (
                "'%s' 经 leave-one-out 证明有害，删掉可提升准确率" % w
            )

    def test_beneficial_words_kept(self):
        kws = EXPERT_TEAM["coder"]["keywords"]
        for w in ["文件", "目录", "打开", "删除", "搜索", "仓库"]:
            assert w in kws, (
                "'%s' 经 leave-one-out 证明有益（删掉会掉分），不应被误删" % w
            )


class TestExpertTeamIntegrity:
    """配置完整性"""

    def test_all_experts_have_required_fields(self):
        for key, cfg in EXPERT_TEAM.items():
            assert cfg.get("label"), "%s 缺 label" % key
            assert cfg.get("model"), "%s 缺 model" % key
            assert cfg.get("system_prompt"), "%s 缺 system_prompt" % key
            assert isinstance(cfg.get("keywords"), list), (
                "%s 的 keywords 不是 list" % key
            )

    def test_route_order_covers_every_expert(self):
        assert set(_ROUTE_KEY_ORDER) == set(EXPERT_TEAM.keys()), (
            "_ROUTE_KEY_ORDER 与 EXPERT_TEAM 键集合不一致"
        )
