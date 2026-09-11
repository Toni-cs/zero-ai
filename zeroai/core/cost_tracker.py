"""Token 用量与成本追踪系统

参考 OpenCode 的 cost tracking 特性，提供会话级的 token 用量和成本统计：

使用方式：
    tracker = CostTracker()
    tracker.record_call("gpt-4o", prompt_tokens=500, completion_tokens=200)
    print(tracker.format_report())
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List


# ============================================================================
# 定价表：每 1M tokens 的 USD 价格
# ============================================================================
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"prompt": 2.50, "cached": 1.25, "completion": 10.00},
    "gpt-4o-mini": {"prompt": 0.15, "cached": 0.075, "completion": 0.60},
    "gpt-4-turbo": {"prompt": 10.00, "completion": 30.00},
    "claude-3-5-sonnet": {"prompt": 3.00, "completion": 15.00},
    "claude-3-opus": {"prompt": 15.00, "completion": 75.00},
    "glm-4": {"prompt": 0.50, "completion": 0.50},
    "glm-4-flash": {"prompt": 0.10, "completion": 0.10},
    "deepseek-chat": {"prompt": 0.14, "completion": 0.28},
    "default": {"prompt": 1.00, "completion": 2.00},
}


# ============================================================================
# 内部工具函数
# ============================================================================
def _get_pricing(model: str) -> Dict[str, float]:
    """获取指定模型的定价表。

    匹配顺序：
      1. 精确匹配 MODEL_PRICING
      2. 最长前缀优先匹配（例如 ``gpt-4o-mini-2024-07-18`` 命中
         ``gpt-4o-mini`` 而非 ``gpt-4o``）
      3. 回退到 ``default``
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # 按 key 长度降序排序，确保最长前缀优先匹配，
    # 避免 ``gpt-4o`` 抢先匹配 ``gpt-4o-mini-*`` 这类更长前缀。
    sorted_keys = sorted(
        (k for k in MODEL_PRICING if k != "default"),
        key=len,
        reverse=True,
    )
    for key in sorted_keys:
        if model.startswith(key):
            return MODEL_PRICING[key]
    return MODEL_PRICING["default"]


def _compute_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """计算单次调用的 USD 成本。

    - ``cached_tokens`` 部分按 ``cached`` 价格计费；
    - 剩余 ``prompt_tokens - cached_tokens`` 按 ``prompt`` 价格计费；
    - ``completion_tokens`` 按 ``completion`` 价格计费。
    """
    pricing = _get_pricing(model)
    prompt_price = pricing.get("prompt", 1.0)
    completion_price = pricing.get("completion", 2.0)
    cached_price = pricing.get("cached", prompt_price * 0.5)

    # 防止 cached_tokens 超过 prompt_tokens 造成负成本
    effective_cached = max(0, min(cached_tokens, prompt_tokens))
    billable_prompt = prompt_tokens - effective_cached

    cost = (
        billable_prompt * prompt_price / 1_000_000.0
        + effective_cached * cached_price / 1_000_000.0
        + completion_tokens * completion_price / 1_000_000.0
    )
    return round(cost, 6)


# ============================================================================
# 数据结构
# ============================================================================
@dataclass
class CostRecord:
    """单次 LLM 调用的成本记录。"""

    timestamp: float
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    cost_usd: float

    def to_dict(self) -> Dict[str, Any]:
        """序列化到 dict（可 JSON 化）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CostRecord":
        """从 dict 反序列化。

        只取已知字段，避免多余字段导致 ``TypeError``；
        对 ``timestamp`` 做兼容性类型转换（字符串 epoch / ISO 格式 → float）。
        """
        valid_fields = {
            "timestamp",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "total_tokens",
            "cost_usd",
        }
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        # timestamp 类型转换：兼容字符串形式（epoch 字符串或 ISO 8601）
        if "timestamp" in filtered and isinstance(filtered["timestamp"], str):
            try:
                filtered["timestamp"] = float(filtered["timestamp"])
            except (ValueError, TypeError):
                try:
                    from datetime import datetime

                    filtered["timestamp"] = datetime.fromisoformat(
                        filtered["timestamp"]
                    ).timestamp()
                except (ValueError, TypeError):
                    filtered["timestamp"] = time.time()
        return cls(**filtered)


@dataclass
class SessionCostSummary:
    """会话级成本汇总。"""

    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cached_tokens: int
    total_tokens: int
    total_cost_usd: float
    duration_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        """序列化到 dict（可 JSON 化）。"""
        return asdict(self)


@dataclass
class ModelCostSummary:
    """单模型成本汇总。"""

    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float

    def to_dict(self) -> Dict[str, Any]:
        """序列化到 dict（可 JSON 化）。"""
        return asdict(self)


# ============================================================================
# CostTracker
# ============================================================================
class CostTracker:
    """会话级 token 用量与成本追踪器（线程安全）。

    使用方式：
        tracker = CostTracker()
        tracker.record_call("gpt-4o", prompt_tokens=500, completion_tokens=200)
        print(tracker.format_report())

    也可直接从 OpenAI API response 的 ``usage`` 字段记录：
        resp = client.chat.completions.create(...)
        tracker.record_usage("gpt-4o", resp.usage.model_dump())
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: List[CostRecord] = []
        self._start_time: float = time.time()

    # ------------------------------------------------------------------
    # 记录调用
    # ------------------------------------------------------------------
    def record_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> CostRecord:
        """记录一次 LLM 调用并返回对应的 ``CostRecord``。

        参数：
            model: 模型名称（用于查定价表）。
            prompt_tokens: 输入 token 数。
            completion_tokens: 输出 token 数。
            cached_tokens: 命中缓存的 prompt token 数（按 cached 价格计费）。

        异常：
            ValueError: model 为空或 token 数为负。
        """
        if not model:
            raise ValueError("model 不能为空")
        if prompt_tokens < 0 or completion_tokens < 0 or cached_tokens < 0:
            raise ValueError("token 数量不能为负")

        total_tokens = prompt_tokens + completion_tokens
        cost_usd = _compute_cost(model, prompt_tokens, completion_tokens, cached_tokens)
        record = CostRecord(
            timestamp=time.time(),
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
        )
        with self._lock:
            self._records.append(record)
        return record

    def record_usage(self, model: str, usage: Dict[str, Any]) -> CostRecord:
        """从 OpenAI API response 的 ``usage`` 字段直接提取并记录。

        兼容字段：
            - ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``
            - ``prompt_tokens_details.cached_tokens``（OpenAI 新格式）
            - ``cached_tokens``（部分供应商直接放在顶层）

        参数：
            model: 模型名称。
            usage: OpenAI response 的 ``usage`` 字段（dict 或可 ``model_dump`` 的对象）。

        异常：
            TypeError: usage 不是 dict。
        """
        # 兼容 pydantic 对象（如 OpenAI v1 的 CompletionUsage）
        if not isinstance(usage, dict):
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            elif hasattr(usage, "to_dict"):
                usage = usage.to_dict()
            else:
                raise TypeError("usage 必须是 dict 或可序列化对象")

        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        cached_tokens = 0
        # 顶层 cached_tokens
        if "cached_tokens" in usage:
            cached_tokens = int(usage["cached_tokens"])
        # OpenAI 新格式：prompt_tokens_details.cached_tokens
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached_tokens = max(
                cached_tokens, int(prompt_details.get("cached_tokens", 0))
            )

        return self.record_call(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    def get_session_summary(self) -> SessionCostSummary:
        """返回会话级汇总。"""
        with self._lock:
            records = list(self._records)
        duration = max(0.0, time.time() - self._start_time)
        return SessionCostSummary(
            total_calls=len(records),
            total_prompt_tokens=sum(r.prompt_tokens for r in records),
            total_completion_tokens=sum(r.completion_tokens for r in records),
            total_cached_tokens=sum(r.cached_tokens for r in records),
            total_tokens=sum(r.total_tokens for r in records),
            total_cost_usd=round(sum(r.cost_usd for r in records), 6),
            duration_seconds=round(duration, 3),
        )

    def get_model_breakdown(self) -> Dict[str, ModelCostSummary]:
        """按模型分组返回成本汇总。"""
        with self._lock:
            records = list(self._records)

        breakdown: Dict[str, ModelCostSummary] = {}
        for r in records:
            if r.model not in breakdown:
                breakdown[r.model] = ModelCostSummary(
                    model=r.model,
                    calls=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    cost_usd=0.0,
                )
            m = breakdown[r.model]
            m.calls += 1
            m.prompt_tokens += r.prompt_tokens
            m.completion_tokens += r.completion_tokens
            m.total_tokens += r.total_tokens
            m.cost_usd += r.cost_usd
        for m in breakdown.values():
            m.cost_usd = round(m.cost_usd, 6)
        return breakdown

    # ------------------------------------------------------------------
    # 重置 / 序列化
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空所有记录并重置会话起始时间。"""
        with self._lock:
            self._records.clear()
            self._start_time = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """序列化到 dict（可 JSON 化）。"""
        with self._lock:
            records = [r.to_dict() for r in self._records]
            start_time = self._start_time
        return {
            "start_time": start_time,
            "records": records,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CostTracker":
        """从 dict 反序列化。

        参数：
            data: :meth:`to_dict` 产出的 dict。

        返回：
            还原后的 ``CostTracker`` 实例。
        """
        tracker = cls()
        records = data.get("records", [])
        with tracker._lock:
            tracker._start_time = float(data.get("start_time", time.time()))
            tracker._records = [CostRecord.from_dict(r) for r in records]
        return tracker

    # ------------------------------------------------------------------
    # 人类可读报告
    # ------------------------------------------------------------------
    def format_report(self) -> str:
        """生成人类可读的成本报告（表格格式）。

        包含：
          - 会话汇总（调用次数、各类 token 数、总成本、时长）
          - 按模型明细表格（按成本降序）
        """
        summary = self.get_session_summary()
        breakdown = self.get_model_breakdown()

        lines: List[str] = []
        lines.append("=" * 72)
        lines.append("Token 用量与成本报告")
        lines.append("=" * 72)
        lines.append("")
        lines.append("【会话汇总】")
        lines.append(f"  总调用次数       : {summary.total_calls}")
        lines.append(f"  Prompt tokens   : {summary.total_prompt_tokens:,}")
        lines.append(f"  Completion tokens: {summary.total_completion_tokens:,}")
        lines.append(f"  Cached tokens   : {summary.total_cached_tokens:,}")
        lines.append(f"  总 tokens       : {summary.total_tokens:,}")
        lines.append(f"  总成本 (USD)    : ${summary.total_cost_usd:.6f}")
        lines.append(f"  会话时长 (秒)   : {summary.duration_seconds:.3f}")
        lines.append("")

        if not breakdown:
            lines.append("（暂无调用记录）")
            lines.append("=" * 72)
            return "\n".join(lines)

        lines.append("【按模型明细】")
        header = (
            f"  {'Model':<22} {'Calls':>6} {'Prompt':>10} "
            f"{'Compl':>10} {'Total':>10} {'Cost(USD)':>12}"
        )
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for model, m in sorted(breakdown.items(), key=lambda kv: -kv[1].cost_usd):
            lines.append(
                f"  {m.model:<22} {m.calls:>6} {m.prompt_tokens:>10,} "
                f"{m.completion_tokens:>10,} {m.total_tokens:>10,} {m.cost_usd:>12.6f}"
            )
        lines.append("")
        lines.append("=" * 72)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __repr__(self) -> str:
        summary = self.get_session_summary()
        return (
            f"CostTracker(calls={summary.total_calls}, "
            f"tokens={summary.total_tokens}, cost=${summary.total_cost_usd:.6f})"
        )


__all__ = [
    "MODEL_PRICING",
    "CostRecord",
    "SessionCostSummary",
    "ModelCostSummary",
    "CostTracker",
]
