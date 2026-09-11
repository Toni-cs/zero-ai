"""CostTracker 单元测试

测试 zeroai/core/cost_tracker.py 的核心功能：
- record_call 记录调用并计算 token 与成本
- 前缀匹配（gpt-4o-mini-2024-07-18 匹配 gpt-4o-mini 而非 gpt-4o）
- format_report 生成报告
- get_session_summary 会话汇总
- CostRecord.from_dict 反序列化（含未知字段忽略与字符串时间戳）
"""
import pytest

from zeroai.core.cost_tracker import (
    CostTracker,
    CostRecord,
    SessionCostSummary,
    _get_pricing,
    _compute_cost,
)


# ============================================================================
# 测试用例
# ============================================================================

def test_record_call():
    """记录一次调用，验证 total_tokens 和 cost_usd 正确计算"""
    tracker = CostTracker()
    record = tracker.record_call("gpt-4o", prompt_tokens=1000, completion_tokens=500)

    # total_tokens = prompt + completion
    assert record.total_tokens == 1500, f"total_tokens 应为 1500，实际 {record.total_tokens}"

    # gpt-4o 定价：prompt $2.50/M, completion $10.00/M
    # cost = 1000 * 2.50 / 1_000_000 + 500 * 10.00 / 1_000_000
    #      = 0.0025 + 0.005 = 0.0075
    expected_cost = 1000 * 2.50 / 1_000_000 + 500 * 10.00 / 1_000_000
    assert record.cost_usd == pytest.approx(expected_cost, rel=1e-6), (
        f"cost_usd 应为 {expected_cost}，实际 {record.cost_usd}"
    )

    # 验证记录已被保存
    assert len(tracker) == 1


def test_prefix_matching():
    """验证 gpt-4o-mini-2024-07-18 匹配 gpt-4o-mini 的价格，而非 gpt-4o 的价格"""
    # 直接测试 _get_pricing
    pricing = _get_pricing("gpt-4o-mini-2024-07-18")
    assert pricing["prompt"] == 0.15, (
        f"gpt-4o-mini-2024-07-18 应匹配 gpt-4o-mini（$0.15/M），"
        f"实际 prompt 价格 {pricing['prompt']}"
    )
    assert pricing["prompt"] != 2.50, "不应匹配 gpt-4o（$2.50/M）"

    # 通过 record_call 间接验证
    tracker = CostTracker()
    # 1M prompt tokens，0 completion tokens
    record = tracker.record_call(
        "gpt-4o-mini-2024-07-18", prompt_tokens=1_000_000, completion_tokens=0
    )
    # 若匹配 gpt-4o-mini：cost = 1M * 0.15 / 1M = 0.15
    # 若错误匹配 gpt-4o：cost = 1M * 2.50 / 1M = 2.50
    assert record.cost_usd == pytest.approx(0.15, rel=1e-6), (
        f"应按 gpt-4o-mini 价格计费（$0.15），实际 cost_usd={record.cost_usd}"
    )
    assert record.cost_usd != pytest.approx(2.50, rel=1e-6), "不应按 gpt-4o 价格计费"


def test_format_report():
    """记录多次调用后，验证 format_report 返回非空字符串"""
    tracker = CostTracker()
    tracker.record_call("gpt-4o", prompt_tokens=100, completion_tokens=50)
    tracker.record_call("gpt-4o-mini", prompt_tokens=200, completion_tokens=100)
    tracker.record_call("claude-3-5-sonnet", prompt_tokens=300, completion_tokens=150)

    report = tracker.format_report()
    assert isinstance(report, str), "format_report 应返回字符串"
    assert len(report) > 0, "报告不应为空"
    # 报告应包含关键信息
    assert "Token" in report or "token" in report, "报告应包含 Token 相关信息"
    assert "gpt-4o" in report, "报告应包含模型名称"


def test_session_summary():
    """验证 get_session_summary 返回正确的总 token 数和总成本"""
    tracker = CostTracker()
    tracker.record_call("gpt-4o", prompt_tokens=1000, completion_tokens=500)
    tracker.record_call("gpt-4o-mini", prompt_tokens=2000, completion_tokens=1000)

    summary = tracker.get_session_summary()
    assert isinstance(summary, SessionCostSummary)

    assert summary.total_calls == 2, f"总调用次数应为 2，实际 {summary.total_calls}"
    # total_tokens = (1000+500) + (2000+1000) = 4500
    assert summary.total_tokens == 4500, f"总 token 应为 4500，实际 {summary.total_tokens}"
    # prompt_tokens = 1000 + 2000 = 3000
    assert summary.total_prompt_tokens == 3000
    # completion_tokens = 500 + 1000 = 1500
    assert summary.total_completion_tokens == 1500
    # 总成本应大于 0
    assert summary.total_cost_usd > 0, "总成本应大于 0"


def test_from_dict():
    """验证 CostRecord.from_dict 正确处理已知字段和忽略未知字段"""
    data = {
        "timestamp": 1234567890.0,
        "model": "gpt-4o",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cached_tokens": 0,
        "total_tokens": 150,
        "cost_usd": 0.001,
        "unknown_field": "should be ignored",
        "another_unknown": 42,
    }
    record = CostRecord.from_dict(data)
    assert record.timestamp == 1234567890.0
    assert record.model == "gpt-4o"
    assert record.prompt_tokens == 100
    assert record.completion_tokens == 50
    assert record.cached_tokens == 0
    assert record.total_tokens == 150
    assert record.cost_usd == 0.001
    # 未知字段被忽略，不抛异常


def test_from_dict_timestamp():
    """验证 CostRecord.from_dict 正确处理字符串时间戳"""
    # 字符串形式的 epoch 时间戳
    data = {
        "timestamp": "1234567890.0",
        "model": "gpt-4o",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cached_tokens": 0,
        "total_tokens": 150,
        "cost_usd": 0.001,
    }
    record = CostRecord.from_dict(data)
    assert record.timestamp == 1234567890.0, (
        f"字符串时间戳应转为 float，实际 {record.timestamp}"
    )
    assert isinstance(record.timestamp, float), "timestamp 应为 float 类型"
