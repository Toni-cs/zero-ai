"""ReAct Agent 核心 - 观察-思考-行动 循环

将 ZeroAI 从"工具调用循环"升级为真正的 Agent Loop：
    观察（Observation）→ 思考（Thought）→ 行动（Action）→ 再观察 → ...

核心设计：
1. ReActPlanner：让 LLM 先思考下一步做什么，输出结构化 JSON
2. AgentLoop：驱动 观察→思考→行动 循环，支持自我纠错
3. 完全复用现有 TOOLS / TOOL_MAP 工具体系，无需改造工具层
4. 可选开关：通过 ZeroAI.react_enabled 启用，不破坏原有 _run_turn_impl

依赖：
- zeroai.core.llm.LLMClient：调用 LLM
- zeroai.tools.registry.TOOL_MAP：工具函数映射

参考论文：ReAct: Synergizing Reasoning and Acting in Language Models (Yao et al., 2022)
参考实现：OpenHands / SWE-agent / smolagents
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import inspect
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .llm import LLMClient
from .context import cleanup_and_compress, get_model_context_limit

logger = logging.getLogger(__name__)

# 子 Agent 协作消息总线（P1-5）
try:
    from .agent_bus import MessageBus, AgentMessage, get_message_bus
    _MESSAGE_BUS_AVAILABLE = True
except Exception:
    _MESSAGE_BUS_AVAILABLE = False

# OpenCode 对标：checkpoint / cost / session / task / diff 审批
try:
    from .checkpoint import CheckpointManager
    _CHECKPOINT_AVAILABLE = True
except Exception:
    _CHECKPOINT_AVAILABLE = False

try:
    from .cost_tracker import CostTracker
    _COST_TRACKER_AVAILABLE = True
except Exception:
    _COST_TRACKER_AVAILABLE = False

try:
    from .session import SessionManager
    _SESSION_AVAILABLE = True
except Exception:
    _SESSION_AVAILABLE = False

try:
    from .task_manager import TaskManager, TaskStatus, TaskPriority
    _TASK_MANAGER_AVAILABLE = True
except Exception:
    _TASK_MANAGER_AVAILABLE = False


# ============================================================================
# ReAct Planner - 让 LLM 先思考再行动
# ============================================================================


def smart_truncate(content: str, max_chars: int) -> str:
    """智能截断：优先保留代码块、错误信息、关键结论。

    与 context_compress.smart_truncate 同语义，此处独立实现以避免循环依赖。
    """
    if not isinstance(content, str):
        content = str(content) if content is not None else ""
    if len(content) <= max_chars:
        return content
    if max_chars <= 0:
        return ""

    lines = content.split("\n")
    key_patterns = (
        "error", "exception", "traceback", "错误", "失败", "警告",
        "```", "def ", "class ", "function ", "return ", "raise ",
        "结果", "结论", "完成", "成功", "assert",
    )

    selected: list = []
    selected_set: set = set()

    def _add_line(idx: int) -> None:
        if 0 <= idx < len(lines) and idx not in selected_set:
            selected_set.add(idx)
            selected.append((idx, lines[idx]))

    # 代码块起止行
    in_code = False
    code_start = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_start = i
                _add_line(i)
            else:
                in_code = False
                _add_line(i)
                if code_start >= 0:
                    for j in range(code_start + 1, min(code_start + 3, i)):
                        _add_line(j)
                    for j in range(max(i - 2, code_start + 1), i):
                        _add_line(j)

    # 关键行
    for i, line in enumerate(lines):
        low = line.lower()
        if any(p in low for p in key_patterns):
            _add_line(i)

    # 最后 N 行（结论）
    tail_n = min(8, len(lines))
    for i in range(len(lines) - tail_n, len(lines)):
        _add_line(i)

    # 开头前 2 行
    for i in range(min(2, len(lines))):
        _add_line(i)

    selected.sort(key=lambda x: x[0])
    omitted_marker = f"\n... [已智能截断，原 {len(content)} 字符] ...\n"
    omitted_len = len(omitted_marker)

    result_lines = []
    total = 0
    prev_idx = -1
    for idx, line in selected:
        if prev_idx >= 0 and idx > prev_idx + 1:
            if total + omitted_len <= max_chars:
                result_lines.append(omitted_marker.strip())
                total += omitted_len
        if total + len(line) + 1 > max_chars:
            break
        result_lines.append(line)
        total += len(line) + 1
        prev_idx = idx

    return "\n".join(result_lines)


def build_system_prompt(
    project_context: Optional[Dict[str, Any]] = None,
    task_type: str = "general",
) -> str:
    """动态构建系统提示词（P1-4）。

    Args:
        project_context: 项目上下文字典，可含 name/tech_stack/cwd 等键
        task_type: 任务类型（coding/writing/analysis/general）

    Returns:
        拼接后的系统提示词
    """
    base = PLANNER_SYSTEM_PROMPT
    if project_context:
        base += "\n\n## 项目上下文\n"
        base += f"- 项目名称: {project_context.get('name', '未知')}\n"
        base += f"- 技术栈: {project_context.get('tech_stack', '未知')}\n"
        base += f"- 工作目录: {project_context.get('cwd', '未知')}\n"
    task_prompts = {
        "coding": "\n\n## 编程任务指南\n优先使用 read_file 了解代码结构，再逐步修改，每次修改后验证语法。",
        "writing": "\n\n## 写作任务指南\n优先规划文章结构，再逐段撰写，保持逻辑连贯。",
        "analysis": "\n\n## 分析任务指南\n优先收集数据，再分析归纳，给出有依据的结论。",
    }
    base += task_prompts.get(task_type, "")
    return base


PLANNER_SYSTEM_PROMPT = """你是 ZeroAI 的任务规划器（ReAct Planner）。

你的职责是分析当前状态，决定下一步行动。严格输出 JSON，格式如下：

```json
{
  "thought": "简短说明你的思考过程（1-2句话）",
  "need_more_info": false,
  "next_action": {
    "type": "tool_call" | "final_answer" | "ask_user",
    "tool": "工具名（仅 type=tool_call 时需要）",
    "args": {"参数名": "参数值"},
    "answer": "最终回答（仅 type=final_answer 时需要）",
    "question": "向用户提问（仅 type=ask_user 时需要）"
  },
  "task_complete": false
}
```

决策规则：
1. 如果用户问题可以直接回答（无需外部信息），选择 final_answer
2. 如果需要读取文件/执行命令/检查系统等，选择 tool_call
3. 如果信息不足无法继续，选择 ask_user
4. 工具调用后，根据结果决定继续调用工具还是给出最终答案
5. 任务完成后设置 task_complete=true

重要：只输出 JSON，不要输出其他任何内容。不要用 markdown 代码块包裹。"""


PLANNER_USER_TEMPLATE = """## 用户请求
{user_input}

## 当前观察
{observation}

## 可用工具
{tools_summary}

## 已执行步骤
{history}

## 任务状态
请决定下一步行动。"""


class ReActPlanner:
    """ReAct 规划器：让 LLM 思考下一步做什么

    输出结构化 JSON，包含：
    - thought：思考过程
    - next_action：下一步行动（tool_call / final_answer / ask_user）
    - task_complete：任务是否完成
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        model_key: str = "glm",
        temperature: float = 0.2,
        max_tokens: int = 800,
        system_prompt: Optional[str] = None,
        context_builder: Optional[Callable[..., str]] = None,
    ):
        """初始化规划器

        Args:
            llm: LLM 客户端，为 None 时用 model_key 创建
            model_key: 模型标识（llm 为 None 时生效）
            temperature: 低温度保证规划稳定性
            max_tokens: 规划输出 token 上限
            system_prompt: 自定义系统提示词（阶段 K.4）
                           为 None 时使用默认 PLANNER_SYSTEM_PROMPT
                           MultiAgentCollaborator 可为不同角色注入不同 prompt
            context_builder: 动态系统提示词构建函数（P1-4）。
                             签名 (project_context, task_type) -> str。
                             提供时优先于 system_prompt 使用 build_system_prompt 逻辑。
        """
        self.llm = llm or LLMClient(model_key)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.context_builder = context_builder
        self._tools_summary_cache: Optional[str] = None
        self._tools_cache_key: Optional[str] = None

    def _build_tools_summary(self, tools: List[Dict[str, Any]]) -> str:
        """构建工具摘要供规划器参考

        只提取 name 和 description 的第一行，避免 token 暴涨。
        """
        cache_key = str(hash((t.get("function", {}).get("name", "") for t in tools)))
        if self._tools_cache_key == cache_key and self._tools_summary_cache:
            return self._tools_summary_cache

        lines = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "").split("\n")[0][:100]
            lines.append(f"- {name}: {desc}")
        self._tools_summary_cache = "\n".join(lines)
        self._tools_cache_key = cache_key
        return self._tools_summary_cache

    def _build_observation(
        self,
        messages: List[Dict[str, Any]],
        retriever: Optional[Callable[[str], List[str]]] = None,
        user_input: str = "",
    ) -> str:
        """构建当前观察：最近对话 + 工具结果 + RAG 检索

        Args:
            messages: 当前对话历史
            retriever: RAG 检索函数，输入查询返回相关文档片段
            user_input: 用户原始输入（用于 RAG 检索）

        Returns:
            观察文本
        """
        parts = []

        # 1. RAG 检索项目上下文
        if retriever and user_input:
            try:
                docs = retriever(user_input)
                if docs:
                    parts.append("### 项目上下文（RAG 检索）")
                    for i, doc in enumerate(docs[:3], 1):
                        parts.append(f"[{i}] {doc[:300]}")
                    parts.append("")
            except Exception as e:
                logger.debug("RAG 检索失败(_build_observation): %s", e, exc_info=True)

        # 2. 最近对话历史（基于 token 预算动态裁剪，P1-2）
        token_budget = 2000
        recent = messages[-10:] if len(messages) > 10 else messages
        parts.append("### 最近对话")
        used = 0
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            content_str = str(content)
            # 粗略估算 token：4 字符 ≈ 1 token
            msg_tokens = len(content_str) // 4
            if used + msg_tokens > token_budget:
                remaining = token_budget - used
                if remaining > 0:
                    content_str = smart_truncate(content_str, remaining * 4)
                    parts.append(f"[{role}] {content_str}")
                break
            used += msg_tokens
            parts.append(f"[{role}] {content_str}")
        parts.append("")

        return "\n".join(parts)

    def _build_history(self, executed_steps: List[Dict[str, Any]]) -> str:
        """构建已执行步骤摘要"""
        if not executed_steps:
            return "（尚无）"
        lines = []
        for i, step in enumerate(executed_steps, 1):
            thought = smart_truncate(step.get("thought", ""), 200)
            action_type = step.get("action_type", "?")
            if action_type == "tool_call":
                tool_name = step.get("tool_name", "?")
                result_preview = smart_truncate(str(step.get("result", "")), 300)
                lines.append(f"{i}. 思考: {thought}")
                lines.append(f"   行动: 调用 {tool_name}")
                lines.append(f"   结果: {result_preview}")
            elif action_type == "final_answer":
                lines.append(f"{i}. 思考: {thought}")
                lines.append(f"   行动: 给出最终答案")
            else:
                lines.append(f"{i}. {thought}")
        return "\n".join(lines)

    async def plan_next(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        executed_steps: List[Dict[str, Any]],
        retriever: Optional[Callable[[str], List[str]]] = None,
    ) -> Dict[str, Any]:
        """规划下一步行动

        Returns:
            {
                "thought": "思考过程",
                "next_action": {
                    "type": "tool_call" | "final_answer" | "ask_user",
                    "tool": "...", "args": {...},
                    "answer": "...", "question": "..."
                },
                "task_complete": bool
            }
        """
        observation = self._build_observation(messages, retriever, user_input)
        tools_summary = self._build_tools_summary(tools)
        history = self._build_history(executed_steps)

        user_prompt = PLANNER_USER_TEMPLATE.format(
            user_input=user_input[:500],
            observation=observation,
            tools_summary=tools_summary,
            history=history,
        )

        try:
            # P1-4: 优先使用 context_builder 动态构建系统提示词
            if self.context_builder is not None:
                sys_prompt = self.context_builder()
            else:
                sys_prompt = self.system_prompt or PLANNER_SYSTEM_PROMPT
            response = await self.llm.chat(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=False,
                timeout=30,
            )
        except Exception as e:
            return {
                "thought": f"规划器调用失败: {e}",
                "next_action": {"type": "final_answer", "answer": f"规划失败: {e}"},
                "task_complete": True,
            }

        if response is None:
            return {
                "thought": "规划器无响应",
                "next_action": {"type": "final_answer", "answer": "规划器无响应"},
                "task_complete": True,
            }

        return self._parse_plan(response)

    def _parse_plan(self, response: str) -> Dict[str, Any]:
        """解析规划器输出为结构化 JSON

        兼容多种输出格式：
        1. 纯 JSON
        2. JSON 外包裹 ```json ... ```
        3. JSON 前后有多余文字
        """
        text = response.strip()

        # 去除 markdown 代码块
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        # 尝试直接解析
        try:
            return self._validate_plan(json.loads(text))
        except json.JSONDecodeError:
            pass

        # 尝试提取第一个 JSON 对象
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return self._validate_plan(json.loads(match.group(0)))
            except json.JSONDecodeError:
                pass

        # 解析失败，作为最终答案返回原文
        return {
            "thought": "规划器输出解析失败，直接使用原文作为回答",
            "next_action": {"type": "final_answer", "answer": response},
            "task_complete": True,
        }

    def _validate_plan(self, data: Any) -> Dict[str, Any]:
        """校验并规范化规划输出"""
        if not isinstance(data, dict):
            return {
                "thought": "规划输出非 dict",
                "next_action": {"type": "final_answer", "answer": str(data)},
                "task_complete": True,
            }

        thought = data.get("thought", "")
        task_complete = bool(data.get("task_complete", False))
        next_action = data.get("next_action", {})

        if not isinstance(next_action, dict):
            next_action = {"type": "final_answer", "answer": str(next_action)}

        action_type = next_action.get("type", "final_answer")
        if action_type not in ("tool_call", "final_answer", "ask_user"):
            action_type = "final_answer"
            next_action["type"] = action_type

        # tool_call 必须有 tool 名
        if action_type == "tool_call" and not next_action.get("tool"):
            return {
                "thought": "规划器要求 tool_call 但未提供工具名，转为最终答案",
                "next_action": {"type": "final_answer", "answer": thought or "无法确定要调用的工具"},
                "task_complete": True,
            }

        return {
            "thought": thought,
            "next_action": next_action,
            "task_complete": task_complete,
        }

    async def _plan_next_at_temp(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        executed_steps: List[Dict[str, Any]],
        retriever: Optional[Callable[[str], List[str]]],
        temp: float,
    ) -> Dict[str, Any]:
        """用指定温度采样一次规划（不修改共享状态）"""
        observation = self._build_observation(messages, retriever, user_input)
        tools_summary = self._build_tools_summary(tools)
        history = self._build_history(executed_steps)

        user_prompt = PLANNER_USER_TEMPLATE.format(
            user_input=user_input[:500],
            observation=observation,
            tools_summary=tools_summary,
            history=history,
        )

        try:
            response = await self.llm.chat(
                system_prompt=self.system_prompt or PLANNER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=temp,
                max_tokens=self.max_tokens,
                stream=False,
                timeout=30,
            )
        except Exception as e:
            return {
                "thought": f"规划器调用失败: {e}",
                "next_action": {"type": "final_answer", "answer": f"规划失败: {e}"},
                "task_complete": True,
            }

        if response is None:
            return {
                "thought": "规划器无响应",
                "next_action": {"type": "final_answer", "answer": "规划器无响应"},
                "task_complete": True,
            }

        return self._parse_plan(response)

    async def plan_next_with_self_consistency(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        executed_steps: List[Dict[str, Any]],
        retriever: Optional[Callable[[str], List[str]]] = None,
        n_samples: int = 3,
    ) -> Dict[str, Any]:
        """自洽性规划：多次采样后投票选出最一致的方案

        参考：Self-Consistency Improves Chain of Thought Reasoning (Wang et al., 2022)

        策略：
        1. 用不同温度采样 n_samples 次
        2. 如果多数方案一致（同 action_type + 同 tool），直接返回
        3. 否则用一次 LLM 投票选出最佳方案
        """
        import asyncio as _aio

        temperatures = [0.1, 0.3, 0.5, 0.2, 0.4][:n_samples]
        # 并行采样（用临时温度，不修改共享状态）
        tasks = [
            self._plan_next_at_temp(
                user_input, messages, tools, executed_steps, retriever, temp
            )
            for temp in temperatures
        ]

        samples = await _aio.gather(*tasks, return_exceptions=True)

        valid_samples = []
        for s in samples:
            if isinstance(s, Exception) or not s:
                continue
            valid_samples.append(s)

        if not valid_samples:
            return {
                "thought": "自洽性采样全部失败",
                "next_action": {"type": "final_answer", "answer": "规划失败"},
                "task_complete": True,
            }

        if len(valid_samples) == 1:
            return valid_samples[0]

        # 检查一致性：同 action_type + 同 tool 视为同一方案
        def action_key(plan: Dict[str, Any]) -> str:
            na = plan.get("next_action", {})
            return f"{na.get('type', '')}|{na.get('tool', '')}"

        from collections import Counter
        key_counts = Counter(action_key(s) for s in valid_samples)
        most_common_key, most_common_count = key_counts.most_common(1)[0]

        # 如果多数一致（>50%），返回该组中 task_complete=True 的优先
        if most_common_count > len(valid_samples) / 2:
            candidates = [s for s in valid_samples if action_key(s) == most_common_key]
            # 优先选 task_complete=True 的
            for c in candidates:
                if c.get("task_complete"):
                    return c
            return candidates[0]

        # 不一致：用 LLM 投票
        return await self._vote_on_plans(user_input, valid_samples)

    async def _vote_on_plans(
        self, user_input: str, plans: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """让 LLM 对多个规划方案投票选出最佳"""
        plans_desc = "\n\n".join(
            f"=== 方案 {i+1} ===\n"
            f"思考: {p.get('thought', '')[:200]}\n"
            f"行动: {json.dumps(p.get('next_action', {}), ensure_ascii=False)[:300]}\n"
            f"完成: {p.get('task_complete', False)}"
            for i, p in enumerate(plans)
        )

        prompt = f"""用户请求：{user_input[:300]}

以下是多个独立规划方案，请选出最可靠的一个：

{plans_desc}

输出 JSON：{{"best": <方案编号1-N>, "reason": "选择原因"}}"""

        try:
            resp = await self.llm.chat(
                system_prompt="你是规划评审专家，选出最可靠的方案。",
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=200,
                stream=False,
                timeout=15,
            )
            if resp:
                match = re.search(r'\{[^}]+\}', resp)
                if match:
                    data = json.loads(match.group())
                    idx = int(data.get("best", 1)) - 1
                    if 0 <= idx < len(plans):
                        return plans[idx]
        except Exception as e:
            logger.debug("自我一致性选择失败，回退到第一个计划: %s", e, exc_info=True)

        return plans[0]


# ============================================================================
# Agent Loop - 驱动 观察→思考→行动 循环
# ============================================================================

class UserMessageQueue:
    """用户消息队列（P2-2）：支持用户在 Agent 执行期间排队插入消息。

    每步开始前 drain 队列，将排队消息加入 messages，实现细粒度中断/插话。
    """

    def __init__(self):
        self._queue: "asyncio.Queue[str]" = asyncio.Queue()

    async def put(self, message: str) -> None:
        """入队一条用户消息"""
        await self._queue.put(message)

    def put_nowait(self, message: str) -> None:
        """非异步上下文下入队"""
        self._queue.put_nowait(message)

    async def drain(self) -> List[str]:
        """排空队列，返回所有排队消息（按入队顺序）"""
        messages = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    def empty(self) -> bool:
        return self._queue.empty()


class PersistentDecisionCache:
    """跨会话决策缓存（P2-3）：缓解决策疲劳，避免重复规划。

    缓存路由决策（input_hash -> expert），可持久化到 JSON 文件。
    """

    def __init__(self, cache_file: Optional[str] = None):
        self._cache: Dict[str, Any] = {}
        self._cache_file = cache_file
        if cache_file:
            try:
                import os
                if os.path.exists(cache_file):
                    with open(cache_file, "r", encoding="utf-8") as f:
                        self._cache = json.load(f)
            except Exception as e:
                logger.debug("决策缓存加载失败，使用空缓存: %s", e, exc_info=True)

    def get_routing(self, input_hash: str) -> Optional[str]:
        """查询路由决策缓存"""
        return self._cache.get(f"route:{input_hash}")

    def set_routing(self, input_hash: str, expert: str) -> None:
        """记录路由决策"""
        self._cache[f"route:{input_hash}"] = expert

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = value

    def save(self) -> None:
        """持久化到文件"""
        if not self._cache_file:
            return
        try:
            import os
            os.makedirs(os.path.dirname(self._cache_file) or ".", exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("决策缓存保存失败: %s", e, exc_info=True)


class AgentLoop:
    """ReAct Agent 循环驱动器

    流程：
        1. 观察：收集当前状态（对话历史 + RAG 检索）
        2. 思考：ReActPlanner 决定下一步
        3. 行动：执行工具调用 / 给出最终答案 / 向用户提问
        4. 回到 1，直到任务完成或达到最大步数

    特性：
    - 自我纠错：工具调用失败时，把错误反馈给规划器，让它换方案
    - 步数限制：防止无限循环
    - 回调机制：每一步都通知 UI 层更新显示
    - 完全复用现有 TOOL_MAP，无需改造工具
    """

    def __init__(
        self,
        planner: Optional[ReActPlanner] = None,
        tool_map: Optional[Dict[str, Callable]] = None,
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        max_steps: int = 8,
        retriever: Optional[Callable[[str], List[str]]] = None,
        use_mcp: bool = False,
        enable_audit: bool = True,
        enable_mcp_health_check: bool = False,
        enable_progress_tracker: bool = False,
        enable_streaming_thought: bool = False,
        enable_parallel_tools: bool = False,
        max_concurrency: int = 4,
        # OpenCode 对标特性
        enable_checkpoint: bool = True,
        workspace_root: str = ".",
        enable_cost_tracking: bool = True,
        enable_session: bool = True,
        enable_task_manager: bool = True,
        enable_diff_review: bool = False,
        diff_review_callback: Optional[Callable] = None,
        enable_action_fusion: bool = False,
        enable_observation_pack: bool = False,
        observation_pack_threshold: int = 2000,
    ):
        """初始化 Agent Loop

        Args:
            planner: ReAct 规划器，为 None 时用默认 GLM
            tool_map: 工具名->函数映射，为 None 时从 registry 导入
            tools_schema: 工具 schema 列表，为 None 时从 registry 导入
            max_steps: 单轮对话最大步数
            retriever: RAG 检索函数
            use_mcp: 是否启用 MCP 生态统一调度（阶段 J.1）
                     启用后工具调用走 MCPEcosystemManager.call_tool，
                     自动获得冲突重命名解析与 MCP 优先调度能力
            enable_audit: 是否启用工具调用审计日志（阶段 J.2）
                          启用后所有工具调用自动记录到 MCPAuditLogger
            enable_mcp_health_check: 是否启用 MCP 健康检查（阶段 J.3）
                                     启用后 MCP 工具调用前先检查服务器健康状态
            enable_progress_tracker: 是否启用工具调用进度跟踪（阶段 P.2）
                                     启用后工具调用自动注册到 ProgressTracker，
                                     UI 层可实时渲染进度条
            enable_streaming_thought: 是否启用流式思维链输出（阶段 P.2）
                                      启用后 on_thought 回调会被流式发射器包装，
                                      支持增量输出
            enable_parallel_tools: 是否启用工具并行调用（阶段 S）
                                   启用后支持单步多工具并行执行
            max_concurrency: 最大并发工具数（阶段 S，默认 4）
        """
        self.planner = planner or ReActPlanner()
        if tool_map is None or tools_schema is None:
            from zeroai.tools.registry import TOOL_MAP, TOOLS
            self.tool_map = tool_map or TOOL_MAP
            self.tools_schema = tools_schema or TOOLS
        else:
            self.tool_map = tool_map
            self.tools_schema = tools_schema
        self.max_steps = max_steps
        self.retriever = retriever

        # P2-2: 用户消息队列（支持执行期间插话/中断）
        self._user_queue = UserMessageQueue()
        # P2-3: 决策缓存（跨会话路由决策复用）
        self._decision_cache = PersistentDecisionCache()

        # 阶段 J：MCP 生态集成开关
        self.use_mcp = use_mcp
        self.enable_audit = enable_audit
        self.enable_mcp_health_check = enable_mcp_health_check

        # 阶段 P.2：流式输出与进度跟踪
        self.enable_progress_tracker = enable_progress_tracker
        self.enable_streaming_thought = enable_streaming_thought
        self._progress_tracker = None
        self._streaming_emitter = None
        self._interrupt_handler = None
        if enable_progress_tracker:
            try:
                from .streaming import get_progress_tracker
                self._progress_tracker = get_progress_tracker()
            except Exception as e:
                logger.debug("进度跟踪器初始化失败: %s", e, exc_info=True)
                self._progress_tracker = None
        if enable_streaming_thought:
            try:
                from .streaming import get_streaming_emitter, get_interrupt_handler
                self._streaming_emitter = get_streaming_emitter()
                self._interrupt_handler = get_interrupt_handler()
            except Exception as e:
                logger.debug("流式输出初始化失败: %s", e, exc_info=True)
                self._streaming_emitter = None
                self._interrupt_handler = None

        # 阶段 S：并行工具调度
        self.enable_parallel_tools = enable_parallel_tools
        self.max_concurrency = max_concurrency
        self._parallel_scheduler = None
        if enable_parallel_tools:
            try:
                from .parallel_tools import ParallelToolScheduler
                self._parallel_scheduler = ParallelToolScheduler(
                    tool_map=self.tool_map,
                    max_concurrency=max_concurrency,
                )
            except Exception as e:
                logger.debug("并行工具调度器初始化失败: %s", e, exc_info=True)
                self._parallel_scheduler = None

        # 回调钩子（UI 层注册）
        self.on_thought: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_tool_call: Optional[Callable[[str, Dict], Awaitable[None]]] = None
        self.on_tool_result: Optional[Callable[[str, str], Awaitable[None]]] = None
        self.on_final_answer: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_error: Optional[Callable[[str], Awaitable[None]]] = None
        self.is_stopped: Optional[Callable[[], bool]] = None

        # OpenCode 对标：checkpoint / cost / session / task / diff 审批
        self.enable_checkpoint = enable_checkpoint and _CHECKPOINT_AVAILABLE
        self._checkpoint_mgr = None
        if self.enable_checkpoint:
            try:
                self._checkpoint_mgr = CheckpointManager(workspace_root=workspace_root)
            except Exception as e:
                logger.debug("Checkpoint 管理器初始化失败: %s", e, exc_info=True)
                self._checkpoint_mgr = None
                self.enable_checkpoint = False

        self.enable_cost_tracking = enable_cost_tracking
        self._cost_tracker = None
        if self.enable_cost_tracking and _COST_TRACKER_AVAILABLE:
            # 优先复用 LLMClient 内置的 cost_tracker
            llm = getattr(self.planner, 'llm', None)
            if llm and getattr(llm, 'cost_tracker', None) is not None:
                self._cost_tracker = llm.cost_tracker
            else:
                self._cost_tracker = CostTracker()

        self.enable_session = enable_session and _SESSION_AVAILABLE
        self._session_mgr = None
        if self.enable_session:
            try:
                self._session_mgr = SessionManager()
            except Exception as e:
                logger.debug("Session 管理器初始化失败: %s", e, exc_info=True)
                self._session_mgr = None
                self.enable_session = False

        self.enable_task_manager = enable_task_manager and _TASK_MANAGER_AVAILABLE
        self._task_mgr = None
        if self.enable_task_manager:
            try:
                self._task_mgr = TaskManager()
            except Exception as e:
                logger.debug("Task 管理器初始化失败: %s", e, exc_info=True)
                self._task_mgr = None
                self.enable_task_manager = False

        self.enable_diff_review = enable_diff_review
        self._diff_review_callback = diff_review_callback
        # Action Fusion（SoL-Pi 对标，2026-09）：opt-in 默认关闭。
        # 启用后，编辑/写入类工具若在参数中显式携带 follow_up_command，
        # 则在本步内立即执行该验证命令并把结果合并进同一次 observation，
        # 省掉"编辑 → 验证"之间的一个完整模型往返。
        self.enable_action_fusion = enable_action_fusion
        # ObservationPack（SoL-Pi 对标，2026-09）：opt-in 默认关闭。
        # 工具输出超过 observation_pack_threshold 字符时，完整结果落盘归档，
        # observation 替换为"头部预览 + 归档路径 + 分页召回说明"。
        # 红线（照抄 SoL-Pi）：原始 observation 完整保留本地，可精确召回。
        self.enable_observation_pack = enable_observation_pack
        self.observation_pack_threshold = max(200, int(observation_pack_threshold))
        self._observation_seq = 0

    def _check_stopped(self) -> bool:
        """检查是否被用户中断

        阶段 P.2：同时检查流式中断处理器（InterruptionHandler）
        """
        if self.is_stopped:
            try:
                if bool(self.is_stopped()):
                    return True
            except Exception as e:
                logger.debug("is_stopped 回调检查失败: %s", e, exc_info=True)
        # 阶段 P.2：流式中断处理器检查
        if self._interrupt_handler is not None:
            try:
                if self._interrupt_handler.check():
                    return True
            except Exception as e:
                logger.debug("流式中断处理器检查失败: %s", e, exc_info=True)
        return False

    async def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """执行工具调用，返回结果字符串

        自动过滤模型幻觉的无效参数。
        阶段 J：支持 MCP 生态统一调度、审计日志、健康检查。
        """
        import time as _time

        start_ts = _time.time()
        success = False
        error_msg = ""
        result_str = ""

        # 阶段 J.1：MCP 生态统一调度
        if self.use_mcp:
            try:
                from zeroai.mcp.ecosystem import get_ecosystem_manager
                from zeroai.mcp.registry import parse_mcp_tool_name

                eco = get_ecosystem_manager()

                # 阶段 J.3：MCP 工具健康检查
                if self.enable_mcp_health_check:
                    mcp_info = parse_mcp_tool_name(name)
                    if mcp_info:
                        server_name, _ = mcp_info
                        try:
                            from zeroai.mcp.health import get_health_monitor
                            monitor = get_health_monitor()
                            record = monitor.get_record(server_name)
                            if record.is_degraded:
                                result_str = f"[降级] MCP 服务器 {server_name} 当前处于降级状态，跳过调用"
                                success = False
                                error_msg = result_str
                                # 审计记录
                                if self.enable_audit:
                                    self._record_audit(
                                        server_name=server_name,
                                        tool_name=name,
                                        arguments=args,
                                        success=False,
                                        duration=_time.time() - start_ts,
                                        error_message=error_msg,
                                    )
                                return result_str
                        except Exception as e:
                            logger.debug("MCP 健康检查失败，不阻断主流程: %s", e, exc_info=True)  # 健康检查失败不阻断主流程

                # 通过生态管理器调度
                result_str = await eco.call_tool(name, args)
                success = not result_str.startswith("[错误]") and not result_str.startswith("[降级]")

                # 审计记录
                if self.enable_audit:
                    mcp_info = parse_mcp_tool_name(name)
                    server_name = mcp_info[0] if mcp_info else "builtin"
                    self._record_audit(
                        server_name=server_name,
                        tool_name=name,
                        arguments=args,
                        success=success,
                        duration=_time.time() - start_ts,
                        error_message="" if success else result_str,
                        result_preview=result_str[:200] if result_str else "",
                    )

                return result_str
            except Exception as e:
                # MCP 调度失败，回退到本地 tool_map
                result_str = f"[MCP 调度错误] {type(e).__name__}: {e}"
                error_msg = result_str
                # 不直接返回，继续走本地 tool_map 作为兜底

        # 本地 tool_map 调用（原有逻辑，保持兼容）
        fn = self.tool_map.get(name)
        if fn is None:
            # 如果 MCP 调度已产生错误信息，附加返回
            if result_str.startswith("[MCP 调度错误]"):
                return f"{result_str}\n[错误] 本地工具也未找到: {name}"
            return f"[错误] 未知工具: {name}"

        # 过滤无效参数
        try:
            valid_params = set(inspect.signature(fn).parameters)
            safe_args = {k: v for k, v in args.items() if k in valid_params}
            extra = set(args.keys()) - valid_params
        except (ValueError, TypeError):
            safe_args = args
            extra = set()

        # 执行（支持同步和异步函数）
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**safe_args)
            else:
                # 同步工具必须放到线程池执行。
                # 原因：像 command_exec 这类工具内部用的是阻塞式 subprocess.run()，
                # 若直接在事件循环线程里调用，会把这个循环彻底堵死，
                # 导致上层 asyncio.wait_for(timeout=...) 的超时回调永远无法触发
                # （即 "设了超时却照样卡死"）。改走线程池后，取消/超时能立即生效。
                result = await asyncio.to_thread(fn, **safe_args)
            result_str = str(result)
            if extra:
                result_str += f"\n[提示：忽略多余参数 {extra}]"
            success = True

            # 阶段 J.2：审计日志记录
            if self.enable_audit:
                self._record_audit(
                    server_name="builtin",
                    tool_name=name,
                    arguments=args,
                    success=True,
                    duration=_time.time() - start_ts,
                    result_preview=result_str[:200] if result_str else "",
                )

            return result_str
        except TypeError as e:
            error_msg = f"[参数错误] {e}"
            if self.enable_audit:
                self._record_audit(
                    server_name="builtin",
                    tool_name=name,
                    arguments=args,
                    success=False,
                    duration=_time.time() - start_ts,
                    error_message=error_msg,
                )
            return error_msg
        except Exception as e:
            error_msg = f"[执行错误] {type(e).__name__}: {e}"
            if self.enable_audit:
                self._record_audit(
                    server_name="builtin",
                    tool_name=name,
                    arguments=args,
                    success=False,
                    duration=_time.time() - start_ts,
                    error_message=error_msg,
                )
            return error_msg

    def _record_audit(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        success: bool,
        duration: float,
        error_message: str = "",
        result_preview: str = "",
    ) -> None:
        """记录工具调用审计日志（阶段 J.2）

        延迟导入避免循环依赖，审计失败不阻断主流程。
        """
        try:
            from zeroai.mcp.audit import get_audit_logger
            logger = get_audit_logger()
            logger.record(
                server_name=server_name,
                tool_name=tool_name,
                full_tool_name=tool_name,
                arguments=arguments,
                success=success,
                duration=duration,
                result_length=len(result_preview) if result_preview else 0,
                error_message=error_message,
                result_preview=result_preview,
                caller="agent_loop",
            )
        except Exception as e:
            logger.debug("审计记录失败，不阻断主流程: %s", e, exc_info=True)  # 审计记录失败不阻断主流程

    def _pack_observation(self, tool_name: str, result: str) -> str:
        """ObservationPack（SoL-Pi 对标）：大输出落盘归档，observation 换成
        "头部预览 + 归档路径 + 分页召回说明"。

        红线（照抄 SoL-Pi）：
        - 原始 observation **完整**写入本地归档，永不截断，可精确召回；
        - 归档失败时回退为原始 result（宁可超上下文也不丢证据）；
        - opt-in 默认关闭。

        召回方式：模型用现有 read_file 工具读归档路径即可全量或分页召回，
        无需新增工具、无需修改 TOOLS schema。
        """
        try:
            if not isinstance(result, str) or len(result) <= self.observation_pack_threshold:
                return result

            self._observation_seq += 1
            import datetime as _dt

            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_tool = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)[:40]
            archive_dir = Path(self.workspace_root) / ".zeroai" / "observation_pack"
            archive_dir.mkdir(parents=True, exist_ok=True)
            path = archive_dir / f"obs_{self._observation_seq:03d}_{safe_tool}_{ts}.txt"
            path.write_text(result, encoding="utf-8")  # 完整原始结果，零截断

            head = result[:1200]
            total_lines = result.count("\n") + 1
            packed = (
                f"{head}\n"
                f"\n[ObservationPack] 输出过长（{len(result)} 字符 / {total_lines} 行），"
                f"已完整归档: {path}\n"
                f"召回方式: read_file(path) 全量读取；或 read_file(path, offset=N, limit=M) 分页。"
                f"归档文件为完整原文，无任何截断。"
            )
            logger.info("ObservationPack: %s -> %s (%d chars)", tool_name, path, len(result))
            return packed
        except Exception as e:
            # 归档失败 → 回退原始结果（证据优先于 token 经济学）
            logger.debug("ObservationPack 归档失败，回退原始结果: %s", e, exc_info=True)
            return result

    async def run(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """运行 Agent 循环

        Args:
            user_input: 用户输入
            messages: 当前对话历史（会被修改）

        Returns:
            (final_answer, executed_steps)

        阶段 P.2 增强：
        - 流式思维链：通过 StreamingThoughtEmitter 实时输出思考过程
        - 进度跟踪：通过 ProgressTracker 跟踪工具调用进度
        - 中断响应：通过 InterruptionHandler 支持用户中断
        """
        executed_steps: List[Dict[str, Any]] = []
        final_answer = ""

        # 阶段 V：进入循环前先压缩上下文，防止上下文爆炸
        try:
            context_limit = get_model_context_limit(self.planner.llm.model) or 8000
            messages = await cleanup_and_compress(messages, context_limit)
        except Exception as e:
            logger.warning("循环前上下文压缩失败，使用原始消息: %s", e, exc_info=True)

        # 阶段 P.2：流式思维链开始
        if self._streaming_emitter is not None:
            try:
                self._streaming_emitter.start_thought(f"任务: {user_input[:50]}")
            except Exception as e:
                logger.debug("流式思维链启动失败: %s", e, exc_info=True)

        for step in range(1, self.max_steps + 1):
            if self._check_stopped():
                break

            # P2-2: 每步开始前排空用户消息队列，将插话加入 messages
            try:
                queued = await self._user_queue.drain()
                for qmsg in queued:
                    messages.append({"role": "user", "content": qmsg})
            except Exception as e:
                logger.debug("用户队列 drain 失败: %s", e, exc_info=True)

            # 1. 思考：规划下一步
            plan = await self.planner.plan_next(
                user_input=user_input,
                messages=messages,
                tools=self.tools_schema,
                executed_steps=executed_steps,
                retriever=self.retriever,
            )

            thought = plan.get("thought", "")
            action = plan.get("next_action", {})
            task_complete = plan.get("task_complete", False)

            # 阶段 P.2：流式输出思考内容
            if self._streaming_emitter is not None:
                try:
                    self._streaming_emitter.append_chunk(f"[步 {step}] {thought}")
                except Exception as e:
                    logger.debug("流式思维链追加失败: %s", e, exc_info=True)

            if self.on_thought:
                try:
                    await self.on_thought(f"[步 {step}] {thought}")
                except Exception as e:
                    logger.debug("on_thought 回调失败: %s", e, exc_info=True)

            action_type = action.get("type", "final_answer")

            # 2. 行动
            if action_type == "tool_call":
                tool_name = action.get("tool", "")
                tool_args = action.get("args", {})
                if not isinstance(tool_args, dict):
                    tool_args = {}

                if self.on_tool_call:
                    try:
                        await self.on_tool_call(tool_name, tool_args)
                    except Exception as e:
                        logger.debug("on_tool_call 回调失败: %s", e, exc_info=True)

                # 阶段 P.2：进度跟踪 - 开始
                call_id = None
                if self._progress_tracker is not None:
                    try:
                        call_id = self._progress_tracker.start(tool_name, tool_args)
                        self._progress_tracker.update(call_id, progress=0.1, message="启动工具")
                    except Exception as e:
                        logger.debug("进度跟踪启动失败: %s", e, exc_info=True)
                        call_id = None

                # Diff 审批：文件修改类工具执行前调用审批回调（OpenCode 对标）
                if self.enable_diff_review and self._diff_review_callback and tool_name in {"write_file", "edit_file", "file_write", "file_edit", "apply_patch"}:
                    try:
                        # 简单审批回调：传入工具名和参数，回调返回 True（批准）或 False（拒绝）
                        approved = self._diff_review_callback(tool_name, tool_args)
                        if not approved:
                            result = "[用户拒绝] 操作未执行"
                            # 跳过工具执行，记录拒绝
                            step_record = {
                                "thought": thought,
                                "action_type": "tool_call",
                                "tool_name": tool_name,
                                "args": tool_args,
                                "result": result,
                            }
                            executed_steps.append(step_record)
                            messages.append({"role": "assistant", "content": f"[调用工具 {tool_name}] {thought}"})
                            messages.append({"role": "user", "content": f"[工具结果 {tool_name}] {result}"})
                            continue
                    except Exception as e:
                        logger.debug("Diff 审批回调失败，不阻塞执行: %s", e, exc_info=True)  # 审批失败不阻塞执行

                # 有副作用的工具调用前创建检查点（OpenCode 对标）
                SIDE_EFFECT_TOOLS = {"write_file", "edit_file", "delete_file", "execute_command", "apply_patch", "run_command", "file_write", "file_edit"}
                if tool_name in SIDE_EFFECT_TOOLS:
                    self._maybe_create_checkpoint(label=f"before_{tool_name}")

                result = await self._execute_tool(tool_name, tool_args)

                # Action Fusion（SoL-Pi 对标）：编辑/写入类工具执行后，若本次
                # 调用参数中显式携带 follow_up_command，则立即执行该验证命令，
                # 把结果合并进同一次 observation —— 省一个完整模型往返。
                # 红线（照抄 SoL-Pi）：
                #   1. 全局开关 opt-in，默认关闭；
                #   2. 命令由模型在本次调用中显式给出，harness 不猜测；
                #   3. 跟进命令失败不推翻主结果，只追加标记，原始结果保留。
                if (self.enable_action_fusion
                        and tool_name in {"write_file", "edit_file", "file_write", "file_edit", "apply_patch"}):
                    follow_up = tool_args.get("follow_up_command")
                    if isinstance(follow_up, str) and follow_up.strip():
                        if "run_command" in self.tool_map:
                            try:
                                fu_cmd = follow_up.strip()
                                if self.on_tool_call:
                                    try:
                                        await self.on_tool_call("action_fusion:run_command", {"command": fu_cmd})
                                    except Exception as e:
                                        logger.debug("on_tool_call 回调失败(fusion): %s", e)
                                fu_result = await self._execute_tool("run_command", {"command": fu_cmd})
                                ok = not (fu_result.startswith("[错误]")
                                          or fu_result.startswith("[执行错误]")
                                          or fu_result.startswith("[已拦截"))
                                marker = "通过" if ok else "失败"
                                result = (f"{result}\n\n[Action Fusion 跟进验证-{marker}] "
                                          f"$ {fu_cmd}\n{fu_result}")
                                logger.info("Action Fusion: %s -> %s", fu_cmd, marker)
                            except Exception as e:
                                logger.debug("Action Fusion 跟进命令执行失败: %s", e, exc_info=True)
                                result = (f"{result}\n\n[Action Fusion 跟进验证-异常] "
                                          f"{type(e).__name__}: {e}")
                        else:
                            result = (f"{result}\n\n[Action Fusion] 工具集中无 run_command，"
                                      f"跟进命令未执行: {follow_up.strip()[:200]}")

                # ObservationPack（SoL-Pi 对标）：大输出落盘归档 + 句柄召回，
                # 原始结果完整保留本地。opt-in，默认关闭。
                if self.enable_observation_pack:
                    result = self._pack_observation(tool_name, result)

                # 阶段 P.2：进度跟踪 - 完成/失败
                if self._progress_tracker is not None and call_id is not None:
                    try:
                        if result.startswith("[错误]") or result.startswith("[MCP 调度错误]"):
                            self._progress_tracker.fail(call_id, error=result[:200])
                        else:
                            self._progress_tracker.complete(call_id, result=result[:200])
                    except Exception as e:
                        logger.debug("进度跟踪完成/失败更新失败: %s", e, exc_info=True)

                if self.on_tool_result:
                    try:
                        await self.on_tool_result(tool_name, result)
                    except Exception as e:
                        logger.debug("on_tool_result 回调失败: %s", e, exc_info=True)

                step_record = {
                    "thought": thought,
                    "action_type": "tool_call",
                    "tool_name": tool_name,
                    "args": tool_args,
                    "result": result,
                }
                executed_steps.append(step_record)

                # 把工具结果加入对话历史，让规划器下一轮能看到
                messages.append({
                    "role": "assistant",
                    "content": f"[调用工具 {tool_name}] {thought}",
                })
                messages.append({
                    "role": "user",
                    "content": f"[工具结果 {tool_name}] {smart_truncate(result, 1500)}",
                })

                # 阶段 P.2：流式追加工具调用结果
                if self._streaming_emitter is not None:
                    try:
                        self._streaming_emitter.append_chunk(f" → {tool_name} 完成")
                    except Exception as e:
                        logger.debug("流式追加工具结果失败: %s", e, exc_info=True)

            elif action_type == "ask_user":
                question = action.get("question", "需要更多信息")
                final_answer = question
                if self.on_final_answer:
                    try:
                        await self.on_final_answer(question)
                    except Exception as e:
                        logger.warning("on_final_answer 回调失败(ask_user): %s", e, exc_info=True)
                break

            else:  # final_answer
                final_answer = action.get("answer", thought or "")
                step_record = {
                    "thought": thought,
                    "action_type": "final_answer",
                    "answer": final_answer,
                }
                executed_steps.append(step_record)

                if self.on_final_answer:
                    try:
                        await self.on_final_answer(final_answer)
                    except Exception as e:
                        logger.warning("on_final_answer 回调失败(final_answer): %s", e, exc_info=True)
                break

            if task_complete:
                break

        if not final_answer:
            final_answer = "已达到最大步数，未能完成任务。"
            if self.on_final_answer:
                try:
                    await self.on_final_answer(final_answer)
                except Exception as e:
                    logger.warning("on_final_answer 回调失败(max_steps): %s", e, exc_info=True)

        # 阶段 P.2：流式思维链结束
        if self._streaming_emitter is not None:
            try:
                self._streaming_emitter.end_thought()
            except Exception as e:
                logger.debug("流式思维链结束失败: %s", e, exc_info=True)

        # OpenCode 对标：保存会话
        self._maybe_save_session(messages, user_input, final_answer)

        return final_answer, executed_steps

    async def run_with_tree_search(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
        max_backtracks: int = 3,
        branch_factor: int = 2,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """带回溯的树搜索 Agent 循环

        当工具调用失败时，不是线性继续，而是回退到上一个决策点，
        尝试不同的行动路径。通过维护状态栈实现 DFS 回溯。

        Args:
            user_input: 用户输入
            messages: 当前对话历史
            max_backtracks: 最大回溯次数（防止指数爆炸）
            branch_factor: 每个决策点的分支数（尝试几个替代方案）

        Returns:
            (final_answer, executed_steps)
        """
        import copy

        best_answer = ""
        best_steps: List[Dict[str, Any]] = []
        backtrack_count = 0

        # 状态栈：每个元素是 (messages_snapshot, executed_steps_snapshot, step_num)
        state_stack: List[Tuple[List[Dict], List[Dict], int]] = []

        current_messages = list(messages)
        current_steps: List[Dict[str, Any]] = []

        for step in range(1, self.max_steps + 1):
            if self._check_stopped():
                break

            # 规划下一步
            plan = await self.planner.plan_next(
                user_input=user_input,
                messages=current_messages,
                tools=self.tools_schema,
                executed_steps=current_steps,
                retriever=self.retriever,
            )

            thought = plan.get("thought", "")
            action = plan.get("next_action", {})
            task_complete = plan.get("task_complete", False)
            action_type = action.get("type", "final_answer")

            if action_type == "final_answer":
                final_answer = action.get("answer", thought or "")
                if len(final_answer) > len(best_answer):
                    best_answer = final_answer
                    best_steps = list(current_steps)
                best_steps.append({"thought": thought, "action_type": "final_answer", "answer": final_answer})
                return final_answer, best_steps

            if action_type == "ask_user":
                return action.get("question", "需要更多信息"), current_steps

            if action_type != "tool_call":
                continue

            tool_name = action.get("tool", "")
            tool_args = action.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}

            # 保存当前状态（用于回溯）
            if backtrack_count < max_backtracks:
                state_stack.append((
                    copy.deepcopy(current_messages),
                    copy.deepcopy(current_steps),
                    step,
                ))

            # 执行工具
            result = await self._execute_tool(tool_name, tool_args)
            is_error = result.startswith("[错误]") or result.startswith("[MCP 调度错误]")

            if is_error and state_stack and backtrack_count < max_backtracks:
                # 工具失败 → 回溯到上一个决策点
                backtrack_count += 1
                prev_msgs, prev_steps, prev_step = state_stack.pop()
                # 恢复到之前的状态，并注入失败信息让规划器避开相同路径
                current_messages = prev_msgs + [{
                    "role": "user",
                    "content": f"[避免] 之前在步骤 {prev_step} 调用 {tool_name} 失败: {result[:300]}。请用不同工具或参数。",
                }]
                current_steps = prev_steps
                continue
            else:
                # 成功或无法回溯 → 正常记录
                step_record = {
                    "thought": thought,
                    "action_type": "tool_call",
                    "tool_name": tool_name,
                    "args": tool_args,
                    "result": result,
                }
                current_steps.append(step_record)
                current_messages.append({
                    "role": "assistant",
                    "content": f"[调用工具 {tool_name}] {thought}",
                })
                current_messages.append({
                    "role": "user",
                    "content": f"[工具结果 {tool_name}] {smart_truncate(result, 1500)}",
                })

            if task_complete:
                break

        if not best_answer:
            best_answer = "已达到最大步数，未能完成任务。"
        return best_answer, current_steps

    def get_progress_summary(self) -> str:
        """获取工具调用进度摘要（阶段 P.2）

        Returns:
            进度摘要字符串，未启用时返回空字符串
        """
        if self._progress_tracker is None:
            return ""
        try:
            return self._progress_tracker.render_summary()
        except Exception as e:
            logger.debug("进度摘要渲染失败: %s", e, exc_info=True)
            return ""

    def interrupt(self, reason: str = "用户中断") -> None:
        """触发中断（阶段 P.2）

        Args:
            reason: 中断原因
        """
        if self._interrupt_handler is not None:
            try:
                self._interrupt_handler.interrupt(reason)
            except Exception as e:
                logger.debug("流式中断触发失败: %s", e, exc_info=True)

    def get_progress_stats(self) -> Dict[str, Any]:
        """获取工具调用统计（阶段 P.2）

        Returns:
            统计字典，未启用时返回空字典
        """
        if self._progress_tracker is None:
            return {}
        try:
            return self._progress_tracker.get_stats()
        except Exception as e:
            logger.debug("进度统计获取失败: %s", e, exc_info=True)
            return {}

    def get_cost_report(self) -> str:
        """获取成本报告（OpenCode 对标）"""
        if self._cost_tracker is not None:
            return self._cost_tracker.format_report()
        return "成本追踪未启用"

    def get_task_status(self) -> str:
        """获取任务状态（OpenCode 对标）"""
        if self._task_mgr is not None:
            return self._task_mgr.format_status()
        return "任务管理未启用"

    def list_sessions(self):
        """列出所有会话（OpenCode 对标）"""
        if self._session_mgr is not None:
            return self._session_mgr.list_sessions()
        return []

    def load_session(self, session_id: str):
        """加载会话（OpenCode 对标）"""
        if self._session_mgr is not None:
            return self._session_mgr.load_session(session_id)
        return None

    def list_checkpoints(self):
        """列出所有检查点（OpenCode 对标）"""
        if self._checkpoint_mgr is not None:
            return self._checkpoint_mgr.list_checkpoints()
        return []

    def rollback_checkpoint(self, checkpoint_id: str):
        """回滚到检查点（OpenCode 对标）"""
        if self._checkpoint_mgr is not None:
            return self._checkpoint_mgr.rollback(checkpoint_id)
        return None

    def _maybe_create_checkpoint(self, label: str = "before_tool"):
        """在有副作用的工具调用前创建检查点"""
        if self._checkpoint_mgr is not None:
            try:
                return self._checkpoint_mgr.create_checkpoint(label=label)
            except Exception as e:
                logger.debug("Checkpoint 创建失败: %s", e, exc_info=True)
                return None
        return None

    def _maybe_save_session(self, messages: List[Dict], user_input: str, response: str, model_key: str = "glm"):
        """保存会话到磁盘"""
        if self._session_mgr is not None:
            try:
                session_id = self._session_mgr.get_or_create_current()
                self._session_mgr.save_session(
                    session_id=session_id,
                    messages=messages,
                    metadata={
                        "user_input": user_input,
                        "agent_response": response,
                        "model_key": model_key,
                    },
                )
            except Exception as e:
                logger.debug("会话保存失败: %s", e, exc_info=True)

    async def execute_tools_parallel(
        self,
        tool_calls: List[Dict[str, Any]],
        merge_strategy: str = "concat",
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """并行执行多个工具调用（阶段 S）

        Args:
            tool_calls: 工具调用列表，每个元素是 {"name": ..., "args": ...}
            merge_strategy: 结果合并策略（concat/dict/list/priority）

        Returns:
            (合并后的结果字符串, 详细结果列表)
        """
        if not self._parallel_scheduler:
            # 未启用并行调度器，回退到串行执行
            results = []
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {})
                result = await self._execute_tool(name, args)
                results.append({
                    "name": name,
                    "args": args,
                    "result": result,
                    "success": not result.startswith("[错误]"),
                })
            merged = "\n\n".join(f"[{r['name']}] {r['result']}" for r in results)
            return merged, results

        from .parallel_tools import ToolCallRequest
        requests = [
            ToolCallRequest(
                name=tc.get("name", ""),
                args=tc.get("args", {}),
                timeout=tc.get("timeout"),
            )
            for tc in tool_calls
        ]
        results = await self._parallel_scheduler.execute_parallel(requests)
        merged = self._parallel_scheduler.merge_results(results, strategy=merge_strategy)
        return merged, [r.to_dict() for r in results]


# ============================================================================
# 便捷工厂函数
# ============================================================================

_agent_loop_instance: Optional[AgentLoop] = None


def get_agent_loop(
    model_key: str = "glm",
    max_steps: int = 8,
    retriever: Optional[Callable[[str], List[str]]] = None,
) -> AgentLoop:
    """获取 AgentLoop 单例

    Args:
        model_key: 规划器使用的模型
        max_steps: 最大步数
        retriever: RAG 检索函数

    Returns:
        AgentLoop 实例
    """
    global _agent_loop_instance
    if _agent_loop_instance is None or retriever is not None:
        planner = ReActPlanner(model_key=model_key)
        _agent_loop_instance = AgentLoop(
            planner=planner,
            max_steps=max_steps,
            retriever=retriever,
        )
    return _agent_loop_instance


def reset_agent_loop() -> None:
    """重置 AgentLoop 单例（配置变更后调用）"""
    global _agent_loop_instance
    _agent_loop_instance = None


# ============================================================================
# 阶段 1 增强：思维链 / 多步规划 / 反思 / 并行 / 摘要
# 以下代码为增量追加，不修改上方任何既有类与函数，保证向后兼容
# ============================================================================


# ----------------------------------------------------------------------------
# 1.1 思维链数据结构（Thought + Plan）—— 让 Agent 推理过程可追溯、可持久化
# ----------------------------------------------------------------------------

@dataclass
class Thought:
    """单步思考记录

    Agent Loop 每一步都会生成一个 Thought，完整记录：
    - 当前思考内容
    - 采取的行动类型（工具调用 / 最终回答 / 向用户提问 / 反思 / 计划）
    - 工具名、参数、结果
    - 反思内容（如果发生错误并触发 Reflexion）
    - 时间戳

    设计目的：让 Agent 的推理链可追溯、可可视化、可持久化，
    而非黑盒。TUI 层可通过 on_thought_chain 回调实时流式展示。
    """

    step: int
    thought: str
    action_type: str  # tool_call / final_answer / ask_user / reflect / plan
    tool_name: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    reflection: Optional[str] = None
    success: bool = True
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """转为字典（用于持久化/JSON 序列化）"""
        return asdict(self)

    def brief(self) -> str:
        """生成简短摘要（用于 TUI 显示）"""
        parts = [f"[步{self.step}] {self.thought[:100]}"]
        if self.action_type == "tool_call" and self.tool_name:
            parts.append(f"  → 调用 {self.tool_name}({self.args})")
            if self.result:
                parts.append(f"  ← {self.result[:80]}")
        elif self.action_type == "reflect":
            parts.append(f"  ⟳ 反思: {self.reflection[:100] if self.reflection else ''}")
        elif self.action_type == "final_answer":
            parts.append(f"  ✓ 完成")
        return "\n".join(parts)


@dataclass
class Plan:
    """多步执行计划（Plan-and-Execute 模式）

    由 PlanAndExecutePlanner.create_plan() 生成，包含：
    - goal: 任务目标
    - steps: 有序步骤列表，每个步骤含 tool/args/reason/depends_on
    - expected_output: 预期输出描述
    - 支持依赖关系：depends_on 指向前面步骤的索引列表

    执行时按顺序进行，某步依赖前序步骤的结果时，
    会把前序结果注入该步的 args（通过 {prev_result_N} 占位符）。
    """

    goal: str
    steps: List[Dict[str, Any]]  # [{tool, args, reason, depends_on: [int]}, ...]
    expected_output: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def step_count(self) -> int:
        return len(self.steps)


# ----------------------------------------------------------------------------
# 1.3 反思引擎（Reflexion）—— 工具失败时让 LLM 复盘并换方案
# ----------------------------------------------------------------------------

REFLECTION_SYSTEM_PROMPT = """你是 ZeroAI 的反思引擎。

当一个工具调用失败时，你需要：
1. 分析失败原因（参数错误？工具选择错误？环境问题？）
2. 给出改进建议（换工具？修参数？换方案？）

严格输出 JSON：
```json
{
  "failure_reason": "失败原因分析（1-2句）",
  "suggestion_type": "retry" | "change_args" | "change_tool" | "give_up",
  "new_tool": "新工具名（仅 change_tool 时需要）",
  "new_args": {"参数名": "新参数值"},
  "explanation": "改进方案说明"
}
```

只输出 JSON，不要输出其他内容。"""


class ReflexionEngine:
    """反思引擎：工具调用失败时让 LLM 复盘原因并给出改进方案

    参考：Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al., 2023)

    使用方式：
        engine = ReflexionEngine(llm)
        reflection = await engine.reflect(tool_name, args, error, history)
        if reflection["suggestion_type"] == "change_args":
            new_args = reflection["new_args"]
            # 用新参数重试
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        model_key: str = "glm",
        max_reflections: int = 4,
        experience_db_path: Optional[str] = None,
    ):
        """初始化反思引擎

        Args:
            llm: LLM 客户端
            model_key: 模型标识
            max_reflections: 单个工具最大反思次数（避免无限重试）
            experience_db_path: 经验库 JSON 路径，跨会话积累失败经验
        """
        self.llm = llm or LLMClient(model_key)
        self.max_reflections = max_reflections
        self._reflection_count: Dict[str, int] = {}  # tool_name -> count
        # 经验库：{tool_name: [{error_sig, failure_reason, suggestion, success}]}
        self._experience_db: Dict[str, List[Dict[str, Any]]] = {}
        self._experience_db_path = experience_db_path
        self._load_experience()

    def _load_experience(self) -> None:
        """从磁盘加载历史经验"""
        if not self._experience_db_path:
            return
        try:
            import os
            if os.path.exists(self._experience_db_path):
                with open(self._experience_db_path, "r", encoding="utf-8") as f:
                    self._experience_db = json.load(f)
        except Exception as e:
            logger.debug("经验库加载失败，使用空库: %s", e, exc_info=True)
            self._experience_db = {}

    def _save_experience(self) -> None:
        """持久化经验库"""
        if not self._experience_db_path:
            return
        try:
            import os
            os.makedirs(os.path.dirname(self._experience_db_path) or ".", exist_ok=True)
            with open(self._experience_db_path, "w", encoding="utf-8") as f:
                json.dump(self._experience_db, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("经验库持久化失败: %s", e, exc_info=True)

    def _error_signature(self, error: str) -> str:
        """提取错误签名（归一化，用于匹配相似错误）"""
        # 取错误前 200 字符，去除具体路径/数字
        sig = error[:200]
        sig = re.sub(r'File "[^"]*"', 'File "..."', sig)
        sig = re.sub(r'line \d+', 'line N', sig)
        sig = re.sub(r'\d+', 'N', sig)
        return sig

    def _retrieve_similar_experience(
        self, tool_name: str, error: str
    ) -> Optional[Dict[str, Any]]:
        """检索相似失败的经验"""
        entries = self._experience_db.get(tool_name, [])
        if not entries:
            return None
        target_sig = self._error_signature(error)
        # 简单匹配：错误签名相似度
        best = None
        best_score = 0
        for entry in entries:
            # 字符级 Jaccard 相似度
            s1 = set(target_sig[i:i+3] for i in range(len(target_sig) - 2))
            s2 = set(entry["error_sig"][i:i+3] for i in range(len(entry["error_sig"]) - 2))
            if not s1 or not s2:
                continue
            score = len(s1 & s2) / len(s1 | s2)
            if score > best_score and score > 0.3:
                best_score = score
                best = entry
        return best

    def _record_experience(
        self,
        tool_name: str,
        error: str,
        failure_reason: str,
        suggestion: Dict[str, Any],
        success: bool,
    ) -> None:
        """记录一条经验"""
        entry = {
            "error_sig": self._error_signature(error),
            "failure_reason": failure_reason,
            "suggestion": suggestion,
            "success": success,
        }
        if tool_name not in self._experience_db:
            self._experience_db[tool_name] = []
        # 保留最近 50 条
        self._experience_db[tool_name].append(entry)
        if len(self._experience_db[tool_name]) > 50:
            self._experience_db[tool_name] = self._experience_db[tool_name][-50:]
        self._save_experience()

    async def reflect(
        self,
        tool_name: str,
        args: Dict[str, Any],
        error: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """对一次失败的工具调用进行反思

        Args:
            tool_name: 失败的工具名
            args: 调用参数
            error: 错误信息
            history: 之前执行过的步骤

        Returns:
            {
                "failure_reason": "...",
                "suggestion_type": "retry" | "change_args" | "change_tool" | "give_up",
                "new_tool": "...",
                "new_args": {...},
                "explanation": "..."
            }
        """
        # 检查反思次数
        count = self._reflection_count.get(tool_name, 0)
        if count >= self.max_reflections:
            return {
                "failure_reason": f"已达到最大反思次数 {self.max_reflections}",
                "suggestion_type": "give_up",
                "new_tool": None,
                "new_args": {},
                "explanation": "放弃重试，转入最终答案",
            }
        self._reflection_count[tool_name] = count + 1

        # 检索历史经验
        past_exp = self._retrieve_similar_experience(tool_name, error)
        exp_text = ""
        if past_exp:
            exp_text = (
                f"\n\n## 历史相似经验\n"
                f"过去遇到过类似错误：{past_exp.get('failure_reason', '')}\n"
                f"当时建议：{json.dumps(past_exp.get('suggestion', {}), ensure_ascii=False)}\n"
                f"结果：{'成功' if past_exp.get('success') else '失败'}\n"
                f"请参考但不要盲目照搬。"
            )

        # 构建反思 prompt
        history_text = "\n".join(
            f"  {i+1}. {h.get('action_type','?')}: {h.get('thought','')[:80]}"
            for i, h in enumerate(history[-5:])
        )
        user_prompt = (
            f"## 失败的工具调用\n"
            f"工具: {tool_name}\n"
            f"参数: {json.dumps(args, ensure_ascii=False)}\n"
            f"错误: {error[:500]}\n\n"
            f"## 之前的执行历史\n{history_text or '（无）'}\n"
            f"{exp_text}\n\n"
            f"## 任务\n分析失败原因，给出改进建议。"
        )

        try:
            response = await self.llm.chat(
                system_prompt=REFLECTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=500,
                stream=False,
                timeout=20,
            )
        except Exception as e:
            return {
                "failure_reason": f"反思引擎调用失败: {e}",
                "suggestion_type": "give_up",
                "new_tool": None,
                "new_args": {},
                "explanation": "反思失败，放弃重试",
            }

        if response is None:
            return {
                "failure_reason": "反思引擎无响应",
                "suggestion_type": "give_up",
                "new_tool": None,
                "new_args": {},
                "explanation": "无响应",
            }

        result = self._parse_reflection(response)
        # 记录经验（success 字段在重试后由调用方更新，此处先记 False）
        self._record_experience(
            tool_name, error,
            result.get("failure_reason", ""),
            {
                "suggestion_type": result.get("suggestion_type"),
                "new_tool": result.get("new_tool"),
                "new_args": result.get("new_args"),
            },
            success=False,
        )
        return result

    def mark_experience_success(self, tool_name: str, error: str) -> None:
        """重试成功后标记最近一条经验为成功"""
        entries = self._experience_db.get(tool_name, [])
        target_sig = self._error_signature(error)
        for entry in reversed(entries):
            if entry["error_sig"] == target_sig and not entry["success"]:
                entry["success"] = True
                self._save_experience()
                break

    def _parse_reflection(self, response: str) -> Dict[str, Any]:
        """解析反思输出"""
        text = response.strip()
        # 去除 markdown 代码块
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

        suggestion = data.get("suggestion_type", "give_up")
        if suggestion not in ("retry", "change_args", "change_tool", "give_up"):
            suggestion = "give_up"

        return {
            "failure_reason": data.get("failure_reason", "未知原因"),
            "suggestion_type": suggestion,
            "new_tool": data.get("new_tool"),
            "new_args": data.get("new_args", {}),
            "explanation": data.get("explanation", ""),
        }

    def reset(self) -> None:
        """重置反思计数（新一轮对话开始时调用）"""
        self._reflection_count.clear()


# ----------------------------------------------------------------------------
# 1.5 工具结果摘要器 —— 长输出用小模型摘要，避免上下文爆炸
# ----------------------------------------------------------------------------

SUMMARIZER_SYSTEM_PROMPT = """你是 ZeroAI 的工具结果摘要器。

将长文本工具输出压缩为简短摘要，保留关键信息：
1. 命令输出：保留关键状态/错误/数据，去掉冗余日志
2. 文件内容：保留核心结构（函数名/类名/关键逻辑），去掉细节
3. 搜索结果：保留标题和摘要，去掉重复内容

输出格式：纯文本摘要，不超过 500 字。不要加 markdown 标题。"""


class ToolResultSummarizer:
    """工具结果摘要器：超长输出用 LLM 摘要后入对话历史

    作用：避免长输出（如 systeminfo、大文件内容）撑爆上下文窗口。
    阈值由 summarize_threshold 控制，默认 1500 字符。
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        model_key: str = "glm",
        summarize_threshold: int = 1500,
        target_length: int = 500,
    ):
        """初始化

        Args:
            llm: LLM 客户端（建议用快速小模型）
            model_key: 模型标识
            summarize_threshold: 触发摘要的最小输出长度（字符数）
            target_length: 摘要目标长度
        """
        self.llm = llm or LLMClient(model_key)
        self.summarize_threshold = summarize_threshold
        self.target_length = target_length

    async def maybe_summarize(
        self,
        result: str,
        tool_name: str,
        query: str = "",
    ) -> str:
        """如果结果过长，用 LLM 摘要；否则原样返回

        Args:
            result: 工具返回的原始结果
            tool_name: 工具名（用于上下文）
            query: 用户原始查询（帮助摘要聚焦）

        Returns:
            摘要后的结果（或原始结果）
        """
        if not result or len(result) <= self.summarize_threshold:
            return result

        try:
            user_prompt = (
                f"## 工具: {tool_name}\n"
                f"## 用户意图: {query[:200]}\n"
                f"## 原始输出（{len(result)} 字符）\n"
                f"{result[:4000]}"  # 截断，避免摘要本身超长
            )
            summary = await self.llm.chat(
                system_prompt=SUMMARIZER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=self.target_length * 2,
                stream=False,
                timeout=15,
            )
            if summary and summary.strip():
                return f"[摘要] {summary.strip()}"
        except Exception as e:
            logger.debug("LLM 摘要生成失败，回退到截断: %s", e, exc_info=True)

        # 摘要失败，截断原结果
        return result[:self.summarize_threshold] + f"\n...[已截断，共 {len(result)} 字符]"


# ----------------------------------------------------------------------------
# 1.2 多步规划器（Plan-and-Execute）—— 先制定完整计划再执行
# ----------------------------------------------------------------------------

PLANNER_PLAN_SYSTEM_PROMPT = """你是 ZeroAI 的任务规划器（Plan-and-Execute 模式）。

你的职责是把用户的复杂任务拆解为有序的执行计划。严格输出 JSON：

```json
{
  "goal": "任务目标简述",
  "steps": [
    {
      "tool": "工具名",
      "args": {"参数名": "参数值"},
      "reason": "为什么这一步",
      "depends_on": []
    }
  ],
  "expected_output": "预期最终输出"
}
```

规则：
1. steps 必须是有序数组，按执行顺序排列
2. depends_on 是数组，元素为前序步骤的索引（从0开始），表示依赖关系
    - 例如 "depends_on": [0] 表示这一步需要用到第0步的结果
    - 无依赖则留空数组 []
3. args 中可用占位符 {prev_result_0}、{prev_result_1} 引用前序步骤结果
    - 例如 "args": {"path": "{prev_result_0}"} 表示路径来自第0步输出
4. 只输出 JSON，不要其他内容。"""


class PlanAndExecutePlanner:
    """多步规划器：先制定完整计划，再逐步执行

    与 ReActPlanner（逐步反应）互补：
    - ReActPlanner：每步都问 LLM 下一步做什么，灵活但慢
    - PlanAndExecutePlanner：先一次性制定完整计划，再执行，快但需要 replan

    参考：Plan-and-Solve Prompting (Wang et al., 2023)

    用法：
        planner = PlanAndExecutePlanner(llm)
        plan = await planner.create_plan(user_input, tools, retriever)
        # 执行 plan.steps...
        # 如果某步失败，调用 planner.replan() 重新规划剩余步骤
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        model_key: str = "glm",
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ):
        self.llm = llm or LLMClient(model_key)
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _build_tools_summary(self, tools: List[Dict[str, Any]]) -> str:
        """构建工具摘要"""
        lines = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "").split("\n")[0][:100]
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _build_context(
        self,
        retriever: Optional[Callable[[str], List[str]]],
        user_input: str,
    ) -> str:
        """构建 RAG 上下文"""
        if not retriever or not user_input:
            return "（无）"
        try:
            docs = retriever(user_input)
            if docs:
                return "\n".join(d[:300] for d in docs[:3])
        except Exception as e:
            logger.debug("RAG 上下文构建失败: %s", e, exc_info=True)
        return "（无）"

    async def create_plan(
        self,
        user_input: str,
        tools: List[Dict[str, Any]],
        retriever: Optional[Callable[[str], List[str]]] = None,
    ) -> Plan:
        """制定完整执行计划

        Args:
            user_input: 用户请求
            tools: 可用工具 schema
            retriever: RAG 检索函数

        Returns:
            Plan 对象
        """
        tools_summary = self._build_tools_summary(tools)
        context = self._build_context(retriever, user_input)

        user_prompt = (
            f"## 用户请求\n{user_input[:800]}\n\n"
            f"## 可用工具\n{tools_summary}\n\n"
            f"## 项目上下文\n{context}\n\n"
            f"## 任务\n制定完整的执行计划。"
        )

        try:
            response = await self.llm.chat(
                system_prompt=PLANNER_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=False,
                timeout=30,
            )
        except Exception as e:
            return Plan(goal=user_input, steps=[], expected_output=f"规划失败: {e}")

        if response is None:
            return Plan(goal=user_input, steps=[], expected_output="规划器无响应")

        return self._parse_plan(response, user_input)

    def _parse_plan(self, response: str, user_input: str) -> Plan:
        """解析规划输出"""
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

        steps = data.get("steps", [])
        if not isinstance(steps, list):
            steps = []

        # 校验每个 step
        valid_steps = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            tool = s.get("tool", "")
            if not tool:
                continue
            valid_steps.append({
                "tool": tool,
                "args": s.get("args", {}) if isinstance(s.get("args"), dict) else {},
                "reason": s.get("reason", ""),
                "depends_on": [
                    int(d) for d in s.get("depends_on", [])
                    if isinstance(d, (int, str)) and str(d).isdigit()
                ],
            })

        return Plan(
            goal=data.get("goal", user_input),
            steps=valid_steps,
            expected_output=data.get("expected_output", ""),
        )

    async def replan(
        self,
        original_plan: Plan,
        executed_steps: List[Dict[str, Any]],
        failure: str,
        tools: List[Dict[str, Any]],
    ) -> Plan:
        """根据失败情况重新规划剩余步骤

        Args:
            original_plan: 原计划
            executed_steps: 已执行的步骤（含结果）
            failure: 失败原因
            tools: 可用工具

        Returns:
            新的 Plan（只含剩余步骤）
        """
        executed_text = "\n".join(
            f"  {i+1}. {s.get('tool','?')} → {str(s.get('result',''))[:100]}"
            for i, s in enumerate(executed_steps)
        )
        tools_summary = self._build_tools_summary(tools)

        user_prompt = (
            f"## 原始目标\n{original_plan.goal}\n\n"
            f"## 已执行步骤\n{executed_text or '（无）'}\n\n"
            f"## 失败原因\n{failure[:300]}\n\n"
            f"## 可用工具\n{tools_summary}\n\n"
            f"## 任务\n根据失败情况，重新规划剩余步骤。"
        )

        try:
            response = await self.llm.chat(
                system_prompt=PLANNER_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=False,
                timeout=30,
            )
        except Exception as e:
            logger.warning("重规划 LLM 调用失败: %s", e, exc_info=True)
            return Plan(goal=original_plan.goal, steps=[], expected_output="重规划失败")

        if response is None:
            return Plan(goal=original_plan.goal, steps=[], expected_output="重规划无响应")

        return self._parse_plan(response, original_plan.goal)

    def build_dependency_graph(self, plan: Plan) -> Dict[str, Any]:
        """构建任务依赖图（DAG）并做拓扑排序

        将 plan.steps 中的 depends_on 关系建模为 DAG，
        识别可并行的步骤组，返回分层执行计划。

        Returns:
            {
                "layers": [[step_indices], ...],  # 每层可并行执行
                "has_cycle": bool,
                "order": [step_indices],          # 拓扑序
            }
        """
        n = len(plan.steps)
        # 邻接表 + 入度
        adj: Dict[int, List[int]] = {i: [] for i in range(n)}
        indeg: Dict[int, int] = {i: 0 for i in range(n)}

        for i, step in enumerate(plan.steps):
            for dep in step.get("depends_on", []):
                if isinstance(dep, int) and 0 <= dep < n and dep != i:
                    adj[dep].append(i)
                    indeg[i] += 1

        # Kahn 拓扑排序 + 分层
        layers: List[List[int]] = []
        queue = [i for i in range(n) if indeg[i] == 0]
        visited = 0

        while queue:
            # 当前层：所有入度为 0 的节点
            layers.append(sorted(queue))
            next_queue: List[int] = []
            for node in queue:
                visited += 1
                for neighbor in adj[node]:
                    indeg[neighbor] -= 1
                    if indeg[neighbor] == 0:
                        next_queue.append(neighbor)
            queue = next_queue

        has_cycle = visited < n

        # 如果有环，把未访问的节点追加到最后一层
        if has_cycle:
            unvisited = [i for i in range(n) if indeg[i] > 0]
            layers.append(unvisited)

        # 展平的拓扑序
        order = [idx for layer in layers for idx in layer]

        return {
            "layers": layers,
            "has_cycle": has_cycle,
            "order": order,
        }

    def inject_dependencies(
        self,
        step: Dict[str, Any],
        step_results: Dict[int, str],
    ) -> Dict[str, Any]:
        """将依赖步骤的结果注入当前步骤的 args

        支持 {prev_result_N} 占位符替换。
        """
        args = dict(step.get("args", {}))
        for dep_idx in step.get("depends_on", []):
            if dep_idx in step_results:
                placeholder = f"{{prev_result_{dep_idx}}}"
                result_val = step_results[dep_idx]
                # 递归替换 args 中所有字符串值
                def replace_in_obj(obj: Any) -> Any:
                    if isinstance(obj, str):
                        return obj.replace(placeholder, result_val)
                    elif isinstance(obj, dict):
                        return {k: replace_in_obj(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [replace_in_obj(x) for x in obj]
                    return obj
                args = replace_in_obj(args)
        return {"tool": step.get("tool", ""), "args": args, "reason": step.get("reason", "")}


# ----------------------------------------------------------------------------
# 1.4 + 全部增强集成：AdvancedAgentLoop
# ----------------------------------------------------------------------------

class AdvancedAgentLoop(AgentLoop):
    """增强版 Agent Loop

    在基础 AgentLoop 之上集成：
    1. 思维链可视化：thought_chain 完整记录每步思考，on_thought_chain 实时回调
    2. 多步规划：支持 Plan-and-Execute 模式（先制定计划再执行）
    3. 自我反思：工具失败时 ReflexionEngine 复盘并换方案重试
    4. 并行工具调用：规划器返回多个无依赖工具时用 asyncio.gather 并行
    5. 工具结果摘要：长输出自动用小模型摘要

    用法：
        loop = AdvancedAgentLoop(
            enable_plan=True,        # 启用多步规划
            enable_reflexion=True,   # 启用反思
            enable_parallel=True,    # 启用并行
            enable_summarize=True,   # 启用摘要
        )
        loop.on_thought_chain = my_callback  # 思维链回调
        final_answer, steps, chain = await loop.run_with_chain(user_input, messages)

    向后兼容：
        - 不传任何增强参数时，行为与基础 AgentLoop 一致
        - run() 方法保持原有签名，返回 (answer, steps)
        - 新功能通过 run_with_chain() 暴露
    """

    def __init__(
        self,
        planner: Optional[ReActPlanner] = None,
        tool_map: Optional[Dict[str, Callable]] = None,
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        max_steps: int = 8,
        retriever: Optional[Callable[[str], List[str]]] = None,
        # 增强参数
        enable_plan: bool = False,
        enable_reflexion: bool = True,
        enable_parallel: bool = True,
        enable_summarize: bool = True,
        reflexion_engine: Optional[ReflexionEngine] = None,
        summarizer: Optional[ToolResultSummarizer] = None,
        plan_planner: Optional[PlanAndExecutePlanner] = None,
        # 阶段 J：MCP 生态集成参数
        use_mcp: bool = False,
        enable_audit: bool = True,
        enable_mcp_health_check: bool = False,
        # OpenCode 对标特性透传
        enable_checkpoint: bool = True,
        workspace_root: str = ".",
        enable_cost_tracking: bool = True,
        enable_session: bool = True,
        enable_task_manager: bool = True,
        enable_diff_review: bool = False,
        diff_review_callback: Optional[Callable] = None,
        enable_action_fusion: bool = False,
        enable_observation_pack: bool = False,
        observation_pack_threshold: int = 2000,
    ):
        """初始化增强版 Agent Loop

        Args:
            planner: 基础 ReAct 规划器
            tool_map / tools_schema / max_steps / retriever: 同 AgentLoop
            enable_plan: 启用 Plan-and-Execute 模式
            enable_reflexion: 启用反思重试
            enable_parallel: 启用并行工具调用
            enable_summarize: 启用结果摘要
            reflexion_engine: 自定义反思引擎
            summarizer: 自定义摘要器
            plan_planner: 自定义多步规划器
            use_mcp: 启用 MCP 生态统一调度（阶段 J.1）
            enable_audit: 启用工具调用审计日志（阶段 J.2）
            enable_mcp_health_check: 启用 MCP 健康检查（阶段 J.3）
        """
        super().__init__(
            planner=planner,
            tool_map=tool_map,
            tools_schema=tools_schema,
            max_steps=max_steps,
            retriever=retriever,
            use_mcp=use_mcp,
            enable_audit=enable_audit,
            enable_mcp_health_check=enable_mcp_health_check,
            enable_checkpoint=enable_checkpoint,
            workspace_root=workspace_root,
            enable_cost_tracking=enable_cost_tracking,
            enable_session=enable_session,
            enable_task_manager=enable_task_manager,
            enable_diff_review=enable_diff_review,
            diff_review_callback=diff_review_callback,
            enable_action_fusion=enable_action_fusion,
            enable_observation_pack=enable_observation_pack,
            observation_pack_threshold=observation_pack_threshold,
        )
        self.enable_plan = enable_plan
        self.enable_reflexion = enable_reflexion
        self.enable_parallel = enable_parallel
        self.enable_summarize = enable_summarize

        # 延迟初始化（只在启用时创建，避免浪费 API 资源）
        self.reflexion_engine = reflexion_engine or (
            ReflexionEngine() if enable_reflexion else None
        )
        self.summarizer = summarizer or (
            ToolResultSummarizer() if enable_summarize else None
        )
        self.plan_planner = plan_planner or (
            PlanAndExecutePlanner() if enable_plan else None
        )

        # 思维链（每轮 run 清空）
        self.thought_chain: List[Thought] = []

        # 新增回调：思维链更新
        self.on_thought_chain: Optional[Callable[[Thought], Awaitable[None]]] = None

    async def _emit_thought(self, thought: Thought) -> None:
        """推送思维链更新"""
        self.thought_chain.append(thought)
        if self.on_thought_chain:
            try:
                await self.on_thought_chain(thought)
            except Exception as e:
                logger.debug("on_thought_chain 回调失败: %s", e, exc_info=True)
        # 同时触发基础 on_thought 回调
        if self.on_thought:
            try:
                await self.on_thought(thought.brief())
            except Exception as e:
                logger.debug("on_thought 回调失败(_emit_thought): %s", e, exc_info=True)

    async def _execute_tool_with_enhancements(
        self,
        name: str,
        args: Dict[str, Any],
        user_input: str = "",
    ) -> Tuple[str, bool]:
        """增强版工具执行：含反思重试 + 结果摘要

        Returns:
            (result, success)
        """
        max_attempts = (self.reflexion_engine.max_reflections + 1) if self.reflexion_engine else 1
        current_name = name
        current_args = args

        for attempt in range(max_attempts):
            # 执行工具
            result = await self._execute_tool(current_name, current_args)
            success = not result.startswith("[错误]") and not result.startswith("[参数错误]") and not result.startswith("[执行错误]")

            if success:
                # 成功：摘要压缩
                if self.summarizer and self.enable_summarize:
                    result = await self.summarizer.maybe_summarize(
                        result, current_name, user_input
                    )
                return result, True

            # 失败：如果不启用反思，直接返回
            if not self.reflexion_engine or not self.enable_reflexion:
                return result, False

            # 触发反思
            reflection = await self.reflexion_engine.reflect(
                tool_name=current_name,
                args=current_args,
                error=result,
                history=[t.to_dict() for t in self.thought_chain],
            )

            thought = Thought(
                step=len(self.thought_chain) + 1,
                thought=f"工具 {current_name} 失败，反思中...",
                action_type="reflect",
                tool_name=current_name,
                args=current_args,
                result=result,
                reflection=reflection.get("failure_reason", ""),
                success=False,
            )
            await self._emit_thought(thought)

            suggestion = reflection.get("suggestion_type", "give_up")
            if suggestion == "give_up":
                return result, False
            elif suggestion == "retry":
                continue  # 用相同参数重试
            elif suggestion == "change_args":
                new_args = reflection.get("new_args", {})
                if isinstance(new_args, dict):
                    current_args = {**current_args, **new_args}
            elif suggestion == "change_tool":
                new_tool = reflection.get("new_tool", "")
                if new_tool and new_tool in self.tool_map:
                    current_name = new_tool

        return result, False

    async def _execute_tools_parallel(
        self,
        tool_calls: List[Dict[str, Any]],
        user_input: str = "",
    ) -> List[Tuple[str, str, bool]]:
        """并行执行多个无依赖的工具调用

        Args:
            tool_calls: [{"tool": "...", "args": {...}}, ...]

        Returns:
            [(tool_name, result, success), ...]
        """
        async def _run_one(call: Dict[str, Any]) -> Tuple[str, str, bool]:
            name = call.get("tool", "")
            args = call.get("args", {})
            result, success = await self._execute_tool_with_enhancements(
                name, args, user_input
            )
            return name, result, success

        results = await asyncio.gather(*[_run_one(c) for c in tool_calls])
        return list(results)

    async def run_with_chain(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]], List[Thought]]:
        """运行 Agent 循环（增强版），返回完整思维链

        Args:
            user_input: 用户输入
            messages: 对话历史

        Returns:
            (final_answer, executed_steps, thought_chain)
        """
        self.thought_chain = []
        if self.reflexion_engine:
            self.reflexion_engine.reset()

        # 阶段 V：进入循环前先压缩上下文，防止上下文爆炸
        try:
            context_limit = get_model_context_limit(self.planner.llm.model) or 8000
            messages = await cleanup_and_compress(messages, context_limit)
        except Exception as e:
            logger.debug("进入循环前上下文压缩失败: %s", e, exc_info=True)

        # 分支：Plan-and-Execute 模式
        if self.enable_plan and self.plan_planner:
            return await self._run_with_plan(user_input, messages)

        # 默认：ReAct 模式（增强版）
        return await self._run_react_enhanced(user_input, messages)

    async def _run_react_enhanced(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]], List[Thought]]:
        """增强版 ReAct 循环"""
        executed_steps: List[Dict[str, Any]] = []
        final_answer = ""

        for step in range(1, self.max_steps + 1):
            if self._check_stopped():
                break

            # 1. 思考
            plan = await self.planner.plan_next(
                user_input=user_input,
                messages=messages,
                tools=self.tools_schema,
                executed_steps=executed_steps,
                retriever=self.retriever,
            )

            thought_text = plan.get("thought", "")
            action = plan.get("next_action", {})
            task_complete = plan.get("task_complete", False)
            action_type = action.get("type", "final_answer")

            # 检查是否为并行工具调用（规划器返回 next_action.type=parallel_tool_calls）
            is_parallel = action_type == "parallel_tool_calls"
            tool_calls_list = action.get("tool_calls", []) if is_parallel else []

            thought = Thought(
                step=step,
                thought=thought_text,
                action_type="parallel_tool_calls" if is_parallel else action_type,
                tool_name=action.get("tool") if not is_parallel else None,
                args=action.get("args", {}) if not is_parallel else {},
            )
            await self._emit_thought(thought)

            # 2. 行动
            if action_type == "tool_call":
                tool_name = action.get("tool", "")
                tool_args = action.get("args", {})
                if not isinstance(tool_args, dict):
                    tool_args = {}

                if self.on_tool_call:
                    try:
                        await self.on_tool_call(tool_name, tool_args)
                    except Exception as e:
                        logger.debug("on_tool_call 回调失败(react_enhanced): %s", e, exc_info=True)

                result, success = await self._execute_tool_with_enhancements(
                    tool_name, tool_args, user_input
                )

                if self.on_tool_result:
                    try:
                        await self.on_tool_result(tool_name, result)
                    except Exception as e:
                        logger.debug("on_tool_result 回调失败(react_enhanced): %s", e, exc_info=True)

                # 更新 thought
                thought.result = result
                thought.success = success

                executed_steps.append({
                    "thought": thought_text,
                    "action_type": "tool_call",
                    "tool_name": tool_name,
                    "args": tool_args,
                    "result": result,
                    "success": success,
                })

                messages.append({
                    "role": "assistant",
                    "content": f"[调用工具 {tool_name}] {thought_text}",
                })
                messages.append({
                    "role": "user",
                    "content": f"[工具结果 {tool_name}] {smart_truncate(result, 1500)}",
                })

            elif is_parallel and self.enable_parallel:
                # 并行执行多个工具
                if self.on_tool_call:
                    for tc in tool_calls_list:
                        try:
                            await self.on_tool_call(tc.get("tool", ""), tc.get("args", {}))
                        except Exception as e:
                            logger.debug("on_tool_call 回调失败(parallel): %s", e, exc_info=True)

                results = await self._execute_tools_parallel(tool_calls_list, user_input)

                for name, result, success in results:
                    if self.on_tool_result:
                        try:
                            await self.on_tool_result(name, result)
                        except Exception as e:
                            logger.debug("on_tool_result 回调失败(parallel): %s", e, exc_info=True)
                    executed_steps.append({
                        "thought": thought_text,
                        "action_type": "tool_call",
                        "tool_name": name,
                        "args": next((tc.get("args", {}) for tc in tool_calls_list if tc.get("tool") == name), {}),
                        "result": result,
                        "success": success,
                    })
                    messages.append({
                        "role": "assistant",
                        "content": f"[并行调用 {name}]",
                    })
                    messages.append({
                        "role": "user",
                        "content": f"[工具结果 {name}] {smart_truncate(result, 1500)}",
                    })

            elif action_type == "ask_user":
                question = action.get("question", "需要更多信息")
                final_answer = question
                thought.action_type = "ask_user"
                if self.on_final_answer:
                    try:
                        await self.on_final_answer(question)
                    except Exception as e:
                        logger.warning("on_final_answer 回调失败(react_enhanced ask_user): %s", e, exc_info=True)
                break

            else:  # final_answer
                final_answer = action.get("answer", thought_text or "")
                thought.action_type = "final_answer"
                executed_steps.append({
                    "thought": thought_text,
                    "action_type": "final_answer",
                    "answer": final_answer,
                })
                if self.on_final_answer:
                    try:
                        await self.on_final_answer(final_answer)
                    except Exception as e:
                        logger.warning("on_final_answer 回调失败(react_enhanced final): %s", e, exc_info=True)
                break

            if task_complete:
                break

        if not final_answer:
            final_answer = "已达到最大步数，未能完成任务。"
            if self.on_final_answer:
                try:
                    await self.on_final_answer(final_answer)
                except Exception as e:
                    logger.warning("on_final_answer 回调失败(react_enhanced max_steps): %s", e, exc_info=True)

        return final_answer, executed_steps, self.thought_chain

    async def _run_with_plan(
        self,
        user_input: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]], List[Thought]]:
        """Plan-and-Execute 模式：先制定计划，再逐步执行

        流程：
        1. PlanAndExecutePlanner.create_plan() 制定完整计划
        2. 按顺序执行每个 step
        3. 某步失败时，调用 replan() 重新规划剩余步骤
        4. 全部完成后，让 LLM 基于所有结果生成最终答案
        """
        # 1. 制定计划
        plan = await self.plan_planner.create_plan(
            user_input=user_input,
            tools=self.tools_schema,
            retriever=self.retriever,
        )

        thought = Thought(
            step=1,
            thought=f"已制定计划：{plan.goal}（{plan.step_count()} 步）",
            action_type="plan",
        )
        await self._emit_thought(thought)

        if plan.step_count() == 0:
            # 规划失败，回退到 ReAct
            return await self._run_react_enhanced(user_input, messages)

        # 2. 逐步执行
        executed_steps: List[Dict[str, Any]] = []
        step_results: List[str] = []  # 每步的结果，供后续步骤引用
        final_answer = ""

        for idx, step_def in enumerate(plan.steps):
            if self._check_stopped():
                break

            tool_name = step_def.get("tool", "")
            raw_args = step_def.get("args", {})
            reason = step_def.get("reason", "")
            depends_on = step_def.get("depends_on", [])

            # 替换占位符 {prev_result_N}
            args = {}
            for k, v in raw_args.items():
                if isinstance(v, str):
                    replaced = v
                    for dep_idx in depends_on:
                        placeholder = f"{{prev_result_{dep_idx}}}"
                        if placeholder in replaced and dep_idx < len(step_results):
                            replaced = replaced.replace(placeholder, step_results[dep_idx][:500])
                    args[k] = replaced
                else:
                    args[k] = v

            thought = Thought(
                step=idx + 2,  # 第1步是 plan
                thought=f"执行步骤 {idx+1}/{plan.step_count()}: {reason}",
                action_type="tool_call",
                tool_name=tool_name,
                args=args,
            )
            await self._emit_thought(thought)

            if self.on_tool_call:
                try:
                    await self.on_tool_call(tool_name, args)
                except Exception as e:
                    logger.debug("on_tool_call 回调失败(plan_execute): %s", e, exc_info=True)

            result, success = await self._execute_tool_with_enhancements(
                tool_name, args, user_input
            )

            thought.result = result
            thought.success = success

            if self.on_tool_result:
                try:
                    await self.on_tool_result(tool_name, result)
                except Exception as e:
                    logger.debug("on_tool_result 回调失败(plan_execute): %s", e, exc_info=True)

            executed_steps.append({
                "thought": reason,
                "action_type": "tool_call",
                "tool_name": tool_name,
                "args": args,
                "result": result,
                "success": success,
            })
            step_results.append(result)

            messages.append({
                "role": "assistant",
                "content": f"[执行计划步骤 {idx+1}: {tool_name}] {reason}",
            })
            messages.append({
                "role": "user",
                "content": f"[工具结果 {tool_name}] {smart_truncate(result, 1500)}",
            })

            # 失败时重规划
            if not success and self.reflexion_engine and self.enable_reflexion:
                new_plan = await self.plan_planner.replan(
                    original_plan=plan,
                    executed_steps=executed_steps,
                    failure=result,
                    tools=self.tools_schema,
                )
                if new_plan.step_count() > 0:
                    # 用新计划的剩余步骤替换未执行部分
                    plan.steps = new_plan.steps
                    thought = Thought(
                        step=idx + 3,
                        thought=f"重规划成功，剩余 {plan.step_count()} 步",
                        action_type="plan",
                    )
                    await self._emit_thought(thought)

        # 3. 让 LLM 基于所有结果生成最终答案
        if executed_steps:
            summary_parts = []
            for i, s in enumerate(executed_steps):
                summary_parts.append(f"步骤{i+1} ({s['tool_name']}): {str(s['result'])[:300]}")
            summary_text = "\n".join(summary_parts)

            try:
                final_answer = await self.planner.llm.chat(
                    system_prompt="你是 ZeroAI。根据工具执行结果，回答用户问题。简明扼要。",
                    user_prompt=f"## 用户问题\n{user_input}\n\n## 执行结果\n{summary_text}\n\n## 请给出最终答案",
                    temperature=0.5,
                    max_tokens=1000,
                    stream=False,
                    timeout=30,
                ) or "执行完成，但无法生成最终答案"
            except Exception as e:
                final_answer = f"执行完成，但生成答案失败: {e}"

            thought = Thought(
                step=len(self.thought_chain) + 1,
                thought="生成最终答案",
                action_type="final_answer",
                result=final_answer,
            )
            await self._emit_thought(thought)

            if self.on_final_answer:
                try:
                    await self.on_final_answer(final_answer)
                except Exception as e:
                    logger.warning("on_final_answer 回调失败(plan_execute final): %s", e, exc_info=True)

        return final_answer, executed_steps, self.thought_chain


# ----------------------------------------------------------------------------
# 工厂函数（增强版）
# ----------------------------------------------------------------------------

_advanced_agent_loop_instance: Optional[AdvancedAgentLoop] = None


def get_advanced_agent_loop(
    model_key: str = "glm",
    max_steps: int = 8,
    retriever: Optional[Callable[[str], List[str]]] = None,
    enable_plan: bool = False,
    enable_reflexion: bool = True,
    enable_parallel: bool = True,
    enable_summarize: bool = True,
) -> AdvancedAgentLoop:
    """获取 AdvancedAgentLoop 单例

    Args:
        model_key: 模型标识
        max_steps: 最大步数
        retriever: RAG 检索函数
        enable_plan: 启用 Plan-and-Execute
        enable_reflexion: 启用反思
        enable_parallel: 启用并行
        enable_summarize: 启用摘要

    Returns:
        AdvancedAgentLoop 实例
    """
    global _advanced_agent_loop_instance
    if _advanced_agent_loop_instance is None or retriever is not None:
        planner = ReActPlanner(model_key=model_key)
        _advanced_agent_loop_instance = AdvancedAgentLoop(
            planner=planner,
            max_steps=max_steps,
            retriever=retriever,
            enable_plan=enable_plan,
            enable_reflexion=enable_reflexion,
            enable_parallel=enable_parallel,
            enable_summarize=enable_summarize,
        )
    return _advanced_agent_loop_instance


def reset_advanced_agent_loop() -> None:
    """重置 AdvancedAgentLoop 单例"""
    global _advanced_agent_loop_instance
    _advanced_agent_loop_instance = None


# ============================================================================
# 多 Agent 协作机制（阶段 B.4）
# ============================================================================

@dataclass
class AgentRole:
    """Agent 角色定义"""
    name: str
    specialty: str  # 专长描述
    system_prompt: str
    model_key: str = "glm"
    tools_whitelist: Optional[List[str]] = None  # None=全部工具，列表=仅允许这些工具


def _parse_subtasks_robust(resp: str, valid_roles: set) -> Optional[List[Dict[str, Any]]]:
    """健壮解析子任务 JSON（P1-5）：多种策略，解析失败返回 None 而非崩溃。

    策略：
    1. 提取 ```json 代码块
    2. 尝试整个响应
    3. 正则匹配第一个 JSON 数组
    """
    if not resp or not isinstance(resp, str):
        return None

    def _validate(data: Any) -> Optional[List[Dict[str, Any]]]:
        """校验 schema 并过滤无效角色"""
        if not isinstance(data, list):
            return None
        valid = [
            t for t in data
            if isinstance(t, dict)
            and t.get("role") in valid_roles
            and t.get("subtask")
        ]
        return valid if valid else None

    # 策略1: 提取 ```json 代码块
    m = re.search(r'```json\s*(.*?)\s*```', resp, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            validated = _validate(data)
            if validated:
                return validated
        except (json.JSONDecodeError, ValueError):
            pass

    # 策略2: 尝试整个响应
    try:
        data = json.loads(resp)
        validated = _validate(data)
        if validated:
            return validated
    except (json.JSONDecodeError, ValueError):
        pass

    # 策略3: 正则匹配第一个 JSON 数组（最后手段）
    try:
        json_str = re.search(r'\[[\s\S]*?\]', resp)
        if json_str:
            data = json.loads(json_str.group())
            validated = _validate(data)
            if validated:
                return validated
    except (json.JSONDecodeError, ValueError):
        pass

    return None


class MultiAgentCollaborator:
    """多 Agent 协作器 - 多个 Agent 分工合作完成复杂任务

    工作模式：
    1. 协调者（Orchestrator）分析任务，分配子任务给专家 Agent
    2. 各专家 Agent 独立完成子任务（并行）
    3. 协调者汇总各专家结果，生成最终答案

    应用场景：
    - 代码审查：coder 写代码 → reasoner 审查逻辑 → security 检查漏洞
    - 文档生成：knowledge 收集资料 → chinese 撰写 → academic 校对引用
    - 复杂调试：coder 复现 → reasoner 分析根因 → coder 修复

    使用示例：
        collab = MultiAgentCollaborator()
        collab.add_role(AgentRole(
            name="coder",
            specialty="代码编写",
            system_prompt="你是代码专家",
            tools_whitelist=["read_file", "write_file", "run_command"],
        ))
        collab.add_role(AgentRole(
            name="reviewer",
            specialty="代码审查",
            system_prompt="你是审查专家",
            tools_whitelist=["read_file"],
        ))
        result = await collab.run("实现并审查一个排序算法", messages)
    """

    def __init__(
        self,
        orchestrator_model: str = "glm",
        max_steps_per_agent: int = 5,
        agent_timeout: float = 120.0,
        max_retries: int = 2,
    ):
        self.orchestrator_model = orchestrator_model
        self.max_steps_per_agent = max_steps_per_agent
        self.roles: Dict[str, AgentRole] = {}
        # P1-5: 子 Agent 超时与重试
        self.agent_timeout = agent_timeout
        self.max_retries = max_retries

        # P1-5: 集成消息总线（子 Agent 间通信）
        self._bus = get_message_bus() if _MESSAGE_BUS_AVAILABLE else None

        # 回调
        self.on_agent_start: Optional[Callable[[str, str], Awaitable[None]]] = None
        self.on_agent_done: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self.on_orchestrator_thought: Optional[Callable[[str], Awaitable[None]]] = None

    def add_role(self, role: AgentRole) -> None:
        """添加一个 Agent 角色"""
        self.roles[role.name] = role

    def remove_role(self, name: str) -> None:
        """移除一个角色"""
        self.roles.pop(name, None)

    async def _decompose_task(
        self,
        task: str,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """协调者分解任务为子任务

        Returns:
            [{"role": "agent_name", "subtask": "子任务描述"}, ...]
        """
        if not self.roles:
            return []

        roles_desc = "\n".join(
            f"- {r.name}: {r.specialty}" for r in self.roles.values()
        )

        prompt = f"""你是任务协调者。请将以下任务分解为子任务，分配给合适的专家 Agent。

可用专家：
{roles_desc}

任务：{task}

请输出 JSON 数组，每个元素包含 role（专家名）和 subtask（子任务描述）：
```json
[{{"role": "expert_name", "subtask": "子任务描述"}}]
```

规则：
1. 只分配给可用的专家
2. 子任务应具体明确
3. 最多分配 4 个子任务
4. 如果任务简单，可以只分配 1 个专家"""

        client = LLMClient(model_key=self.orchestrator_model)
        msgs = [{"role": "user", "content": prompt}]
        try:
            resp = await client.chat(msgs, temperature=0.3)
        except Exception as e:
            logger.warning("协调者分解任务调用失败: %s", e, exc_info=True)
            resp = ""

        # P1-5: 健壮 JSON 解析（多种策略，失败返回 None 而非崩溃）
        parsed = _parse_subtasks_robust(resp, set(self.roles.keys()))
        if parsed:
            # P1-5: 通过消息总线广播分解结果
            if self._bus is not None:
                try:
                    self._bus.publish_simple(
                        sender="orchestrator",
                        topic="task.decomposed",
                        content=parsed,
                    )
                except Exception as e:
                    logger.debug("消息总线广播失败: %s", e, exc_info=True)
            return parsed[:4]  # 最多 4 个

        # 回退：将整个任务分配给第一个专家
        logger.info("任务分解 JSON 解析失败，回退到单专家模式")
        first_role = next(iter(self.roles), None)
        if first_role:
            return [{"role": first_role, "subtask": task}]
        return []

    async def _run_single_agent(
        self,
        role: AgentRole,
        subtask: str,
        messages: List[Dict[str, Any]],
        shared_context: str = "",
    ) -> str:
        """运行单个专家 Agent 完成子任务（P1-5: 超时 + 重试）"""
        if self.on_agent_start:
            try:
                await self.on_agent_start(role.name, subtask)
            except Exception as e:
                logger.debug("on_agent_start 回调失败: %s", e, exc_info=True)

        # P1-5: 通过消息总线通知子 Agent 启动
        if self._bus is not None:
            try:
                self._bus.publish_simple(
                    sender="orchestrator",
                    topic="agent.start",
                    content={"role": role.name, "subtask": subtask},
                    receiver=role.name,
                )
            except Exception as e:
                logger.debug("消息总线通知启动失败: %s", e, exc_info=True)

        # 构造工具集（按白名单过滤）
        try:
            from zeroai.tools.registry import TOOLS, TOOL_MAP
        except Exception as e:
            logger.debug("工具注册表导入失败: %s", e, exc_info=True)
            TOOLS, TOOL_MAP = [], {}

        if role.tools_whitelist:
            tools_schema = [
                t for t in TOOLS
                if t.get("function", {}).get("name", "") in role.tools_whitelist
            ]
            tool_map = {
                k: v for k, v in TOOL_MAP.items()
                if k in role.tools_whitelist
            }
        else:
            tools_schema = list(TOOLS)
            tool_map = dict(TOOL_MAP)

        # 创建 Agent Loop（阶段 K.4：通过构造函数注入角色 prompt）
        planner = ReActPlanner(
            model_key=role.model_key,
            system_prompt=role.system_prompt,
        )

        loop = AdvancedAgentLoop(
            planner=planner,
            tool_map=tool_map,
            tools_schema=tools_schema,
            max_steps=self.max_steps_per_agent,
            enable_reflexion=True,
            enable_parallel=False,  # 单 Agent 内不并行，避免冲突
            enable_summarize=True,
        )

        # 注入共享上下文
        enhanced_subtask = subtask
        if shared_context:
            enhanced_subtask = f"{subtask}\n\n[其他专家的中间结果]\n{shared_context}"

        # P1-5: 超时 + 重试机制
        final_answer = ""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 2):
            try:
                final_answer, _, _ = await asyncio.wait_for(
                    loop.run_with_chain(
                        user_input=enhanced_subtask,
                        messages=list(messages),  # 副本，避免污染
                    ),
                    timeout=self.agent_timeout,
                )
                last_error = None
                break  # 成功，退出重试
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"子 Agent {role.name} 超时（{self.agent_timeout}s，第 {attempt} 次）"
                )
                logger.warning("%s", last_error)
                if attempt <= self.max_retries:
                    continue
            except Exception as e:
                last_error = e
                logger.warning(
                    "子 Agent %s 第 %d 次执行失败: %s",
                    role.name, attempt, e, exc_info=True,
                )
                if attempt <= self.max_retries:
                    continue

        if last_error is not None:
            final_answer = f"[子 Agent {role.name} 执行失败: {last_error}]"
            # P1-5: 通过消息总线通知失败
            if self._bus is not None:
                try:
                    self._bus.publish_simple(
                        sender=role.name,
                        topic="agent.failed",
                        content=str(last_error),
                    )
                except Exception as e:
                    logger.debug("消息总线通知失败: %s", e, exc_info=True)

        if self.on_agent_done:
            try:
                await self.on_agent_done(role.name, subtask, final_answer)
            except Exception as e:
                logger.debug("on_agent_done 回调失败: %s", e, exc_info=True)

        return final_answer

    async def _synthesize_results(
        self,
        task: str,
        results: Dict[str, str],
        messages: List[Dict[str, Any]],
    ) -> str:
        """协调者汇总各专家结果"""
        results_text = "\n\n".join(
            f"## {role} 的结果\n{result}"
            for role, result in results.items()
            if result
        )

        prompt = f"""你是任务协调者。请汇总以下各专家的工作结果，生成最终答案。

原始任务：{task}

各专家结果：
{results_text}

请综合所有结果，生成完整、连贯的最终答案。如有冲突，请指出并给出最合理的结论。"""

        client = LLMClient(model_key=self.orchestrator_model)
        msgs = [{"role": "user", "content": prompt}]
        try:
            final = await client.chat(msgs, temperature=0.5)
        except Exception as e:
            logger.warning("协调者汇总失败: %s", e, exc_info=True)
            final = results_text  # 降级：直接拼接各专家结果

        if self.on_orchestrator_thought:
            try:
                await self.on_orchestrator_thought("汇总各专家结果")
            except Exception as e:
                logger.debug("on_orchestrator_thought 回调失败: %s", e, exc_info=True)

        return final

    async def run(
        self,
        task: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, Dict[str, str]]:
        """运行多 Agent 协作

        Args:
            task: 用户任务
            messages: 对话历史

        Returns:
            (final_answer, {role_name: subtask_result})
        """
        # 1. 协调者分解任务
        if self.on_orchestrator_thought:
            try:
                await self.on_orchestrator_thought("分析任务并分配子任务")
            except Exception as e:
                logger.debug("on_orchestrator_thought 回调失败: %s", e, exc_info=True)

        subtasks = await self._decompose_task(task, messages)

        if not subtasks:
            # 无法分解，直接用第一个专家处理
            if self.roles:
                first_role = next(iter(self.roles.values()))
                result = await self._run_single_agent(first_role, task, messages)
                return result, {first_role.name: result}
            return "无可用的专家 Agent。", {}

        # 2. 并行执行子任务（无依赖时）
        # 检查是否有依赖（简单策略：如果子任务数<=2，并行；否则串行传递上下文）
        results: Dict[str, str] = {}
        shared_context = ""

        if len(subtasks) <= 2:
            # 并行执行
            async def _run_one(sub: Dict[str, Any]) -> Tuple[str, str]:
                role = self.roles[sub["role"]]
                result = await self._run_single_agent(role, sub["subtask"], messages)
                return sub["role"], result

            tasks_list = [_run_one(s) for s in subtasks]
            done = await asyncio.gather(*tasks_list, return_exceptions=True)
            for i, item in enumerate(done):
                if isinstance(item, Exception):
                    # P1-5/P1-6: 记录子 Agent 异常而非静默吞没
                    logger.error(
                        "子 Agent %s 失败: %s",
                        subtasks[i].get("role", f"#{i}"), item, exc_info=item,
                    )
                elif isinstance(item, tuple) and len(item) == 2:
                    results[item[0]] = item[1]
        else:
            # 串行执行，传递上下文
            for sub in subtasks:
                role = self.roles[sub["role"]]
                result = await self._run_single_agent(
                    role, sub["subtask"], messages, shared_context
                )
                results[role.name] = result
                shared_context += f"\n[{role.name}]: {smart_truncate(result, 500)}\n"

        # 3. 协调者汇总
        final = await self._synthesize_results(task, results, messages)

        return final, results


__all__ = [
    # 基础（向后兼容）
    "ReActPlanner",
    "AgentLoop",
    "PLANNER_SYSTEM_PROMPT",
    "get_agent_loop",
    "reset_agent_loop",
    # 阶段 1 增强
    "Thought",
    "Plan",
    "ReflexionEngine",
    "ToolResultSummarizer",
    "PlanAndExecutePlanner",
    "AdvancedAgentLoop",
    "REFLECTION_SYSTEM_PROMPT",
    "SUMMARIZER_SYSTEM_PROMPT",
    "PLANNER_PLAN_SYSTEM_PROMPT",
    "get_advanced_agent_loop",
    "reset_advanced_agent_loop",
    # 阶段 B.4 多 Agent 协作
    "AgentRole",
    "MultiAgentCollaborator",
    # P1-2/P1-4/P2-2/P2-3 新增
    "smart_truncate",
    "build_system_prompt",
    "UserMessageQueue",
    "PersistentDecisionCache",
]
