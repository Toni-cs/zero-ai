"""动态角色分配与协作增强（阶段 O.2）

基于 MessageBus 和 Blackboard 的多 Agent 协作增强：

1. 动态角色分配器（DynamicRoleAllocator）
   - 根据任务自动选择专家组合
   - 支持运行时添加/移除角色
   - 角色依赖图（A 的输出是 B 的输入）

2. 协作上下文（CollaborationContext）
   - 基于 Blackboard 的共享状态
   - 任务进度跟踪
   - Agent 产出共享

3. 增强的多 Agent 协作器（EnhancedMultiAgentCollaborator）
   - 继承 MultiAgentCollaborator 的基础能力
   - 集成消息总线和黑板
   - 支持管道式协作（A → B → C）
   - 支持共识投票

使用方式：
    collab = EnhancedMultiAgentCollaborator()
    collab.add_role(role_coder)
    collab.add_role(role_reviewer, depends_on=["coder"])
    result = await collab.run_with_pipeline(task, messages)
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .agent_bus import (
    AgentMessage,
    MessageBus,
    Blackboard,
    get_message_bus,
    get_blackboard,
)
from .agent import AgentRole, MultiAgentCollaborator, LLMClient


# ============================================================================
# 角色依赖图（阶段 O.2.1）
# ============================================================================

@dataclass
class RoleNode:
    """角色节点：描述角色及其依赖关系"""
    role: AgentRole
    depends_on: List[str] = field(default_factory=list)  # 依赖的角色名
    dependents: List[str] = field(default_factory=list)  # 被哪些角色依赖
    status: str = "pending"  # pending / running / done / failed
    result: Optional[str] = None
    error: Optional[str] = None


class RoleDependencyGraph:
    """角色依赖图

    管理角色间的依赖关系，支持拓扑排序。

    使用方式：
        graph = RoleDependencyGraph()
        graph.add_role(role_coder)
        graph.add_role(role_reviewer, depends_on=["coder"])
        # 获取可执行的角色（无依赖或依赖已完成）
        ready = graph.get_ready_roles()
        # 标记完成
        graph.mark_done("coder", "result...")
        # 下次 get_ready_roles 会包含 reviewer
    """

    def __init__(self):
        self._nodes: Dict[str, RoleNode] = {}

    def add_role(
        self,
        role: AgentRole,
        depends_on: Optional[List[str]] = None,
    ) -> None:
        """添加角色节点

        Args:
            role: Agent 角色
            depends_on: 依赖的角色名列表
        """
        deps = depends_on or []
        node = RoleNode(role=role, depends_on=deps)
        self._nodes[role.name] = node

        # 更新被依赖关系
        for dep_name in deps:
            if dep_name in self._nodes:
                if role.name not in self._nodes[dep_name].dependents:
                    self._nodes[dep_name].dependents.append(role.name)

    def remove_role(self, name: str) -> None:
        """移除角色"""
        if name in self._nodes:
            # 清理依赖关系
            for dep in self._nodes[name].depends_on:
                if dep in self._nodes:
                    try:
                        self._nodes[dep].dependents.remove(name)
                    except ValueError:
                        pass
            self._nodes.pop(name)

    def get_ready_roles(self) -> List[AgentRole]:
        """获取可执行的角色（依赖已完成）

        Returns:
            可执行的角色列表
        """
        ready = []
        for name, node in self._nodes.items():
            if node.status != "pending":
                continue
            # 检查所有依赖是否完成
            if all(
                self._nodes.get(dep, RoleNode(role=AgentRole("", "", ""))).status == "done"
                for dep in node.depends_on
            ):
                ready.append(node.role)
        return ready

    def mark_running(self, name: str) -> None:
        """标记角色开始执行"""
        if name in self._nodes:
            self._nodes[name].status = "running"

    def mark_done(self, name: str, result: str) -> None:
        """标记角色完成"""
        if name in self._nodes:
            self._nodes[name].status = "done"
            self._nodes[name].result = result
            self._nodes[name].error = None

    def mark_failed(self, name: str, error: str) -> None:
        """标记角色失败"""
        if name in self._nodes:
            self._nodes[name].status = "failed"
            self._nodes[name].error = error

    def is_all_done(self) -> bool:
        """检查所有角色是否完成"""
        return all(
            node.status in ("done", "failed") for node in self._nodes.values()
        )

    def get_status(self) -> Dict[str, Any]:
        """获取所有角色状态"""
        return {
            name: {
                "status": node.status,
                "depends_on": node.depends_on,
                "dependents": node.dependents,
                "has_result": node.result is not None,
                "error": node.error,
            }
            for name, node in self._nodes.items()
        }

    def topological_sort(self) -> List[str]:
        """拓扑排序：返回执行顺序"""
        visited: Set[str] = set()
        result: List[str] = []

        def _visit(name: str):
            if name in visited:
                return
            visited.add(name)
            node = self._nodes.get(name)
            if node is None:
                return
            for dep in node.depends_on:
                _visit(dep)
            result.append(name)

        for name in self._nodes:
            _visit(name)

        return result


# ============================================================================
# 协作上下文（阶段 O.2.2）
# ============================================================================

class CollaborationContext:
    """协作上下文：基于 Blackboard 的共享状态

    提供任务进度跟踪和 Agent 产出共享。

    使用方式：
        ctx = CollaborationContext(task_id="task_123")
        ctx.set_task("实现排序算法")
        ctx.share_output("coder", "code...", metadata={"lang": "python"})
        code = ctx.get_output("coder")
        progress = ctx.get_progress()
    """

    def __init__(self, task_id: Optional[str] = None):
        """初始化

        Args:
            task_id: 任务 ID，用于黑板命名空间隔离
        """
        self.task_id = task_id or f"task_{int(__import__('time').time())}"
        self._board = get_blackboard()
        self._bus = get_message_bus()
        self._namespace = f"collab_{self.task_id}"

    def set_task(self, task: str) -> None:
        """设置任务描述"""
        self._board.write(self._namespace, "task", task)

    def get_task(self) -> str:
        """获取任务描述"""
        return self._board.read(self._namespace, "task", "")

    def share_output(
        self,
        agent_name: str,
        output: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """共享 Agent 产出

        Args:
            agent_name: Agent 名称
            output: 产出内容
            metadata: 元数据
        """
        self._board.write(self._namespace, f"output_{agent_name}", output)
        if metadata:
            self._board.write(self._namespace, f"meta_{agent_name}", metadata)

        # 通过消息总线通知
        self._bus.publish_simple(
            sender=agent_name,
            topic=f"{self._namespace}_output",
            content={"agent": agent_name, "output": output, "metadata": metadata},
            msg_type="notification",
        )

    def get_output(self, agent_name: str) -> Optional[str]:
        """获取其他 Agent 的产出"""
        return self._board.read(self._namespace, f"output_{agent_name}")

    def get_all_outputs(self) -> Dict[str, str]:
        """获取所有 Agent 的产出"""
        outputs = {}
        for key in self._board.list_keys(self._namespace):
            if key.startswith("output_"):
                agent_name = key[len("output_"):]
                outputs[agent_name] = self._board.read(self._namespace, key)
        return outputs

    def set_progress(
        self,
        agent_name: str,
        step: int,
        total: int,
        status: str = "running",
    ) -> None:
        """设置 Agent 进度"""
        progress = {"agent": agent_name, "step": step, "total": total, "status": status}
        self._board.write(self._namespace, f"progress_{agent_name}", progress)

    def get_progress(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Agent 的进度"""
        progress = {}
        for key in self._board.list_keys(self._namespace):
            if key.startswith("progress_"):
                agent_name = key[len("progress_"):]
                progress[agent_name] = self._board.read(self._namespace, key)
        return progress

    def subscribe_output(
        self,
        agent_name: str,
        handler: Callable[[str, Dict[str, Any]], None],
    ) -> Callable[[], None]:
        """订阅指定 Agent 的产出

        Args:
            agent_name: Agent 名称
            handler: 处理函数 (output, metadata) -> None

        Returns:
            取消订阅函数
        """
        def _observer(value, version, writer):
            if isinstance(value, str):
                metadata = self._board.read(self._namespace, f"meta_{agent_name}", {})
                handler(value, metadata or {})

        return self._board.subscribe(self._namespace, f"output_{agent_name}", _observer)

    def clear(self) -> None:
        """清空协作上下文"""
        self._board.clear_namespace(self._namespace)


# ============================================================================
# 增强的多 Agent 协作器（阶段 O.2.3）
# ============================================================================

class EnhancedMultiAgentCollaborator(MultiAgentCollaborator):
    """增强的多 Agent 协作器

    在 MultiAgentCollaborator 基础上增加：
    1. 消息总线集成：Agent 间可通信
    2. 共享黑板集成：Agent 间可共享状态
    3. 管道式协作：支持 A → B → C 的顺序执行
    4. 共识投票：多 Agent 对结果投票

    使用方式：
        collab = EnhancedMultiAgentCollaborator()
        collab.add_role(role_coder, depends_on=[])
        collab.add_role(role_reviewer, depends_on=["coder"])
        # 管道式执行
        result = await collab.run_with_pipeline(task, messages)
    """

    def __init__(
        self,
        orchestrator_model: str = "glm",
        max_steps_per_agent: int = 5,
        enable_message_bus: bool = True,
        enable_blackboard: bool = True,
    ):
        """初始化

        Args:
            orchestrator_model: 协调者模型
            max_steps_per_agent: 每个 Agent 最大步数
            enable_message_bus: 是否启用消息总线
            enable_blackboard: 是否启用共享黑板
        """
        super().__init__(
            orchestrator_model=orchestrator_model,
            max_steps_per_agent=max_steps_per_agent,
        )

        self.enable_message_bus = enable_message_bus
        self.enable_blackboard = enable_blackboard

        # 角色依赖图
        self._dependency_graph = RoleDependencyGraph()

        # 消息总线和黑板（按需获取）
        self._bus: Optional[MessageBus] = None
        self._context: Optional[CollaborationContext] = None

    def add_role(
        self,
        role: AgentRole,
        depends_on: Optional[List[str]] = None,
    ) -> None:
        """添加角色（支持依赖关系）

        Args:
            role: Agent 角色
            depends_on: 依赖的角色名列表
        """
        super().add_role(role)
        self._dependency_graph.add_role(role, depends_on)

    def remove_role(self, name: str) -> None:
        """移除角色"""
        super().remove_role(name)
        self._dependency_graph.remove_role(name)

    async def run_with_pipeline(
        self,
        task: str,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        """管道式协作执行

        按依赖图顺序执行 Agent，前一个 Agent 的产出作为后一个的输入。

        Args:
            task: 任务描述
            messages: 对话历史

        Returns:
            (最终结果, 执行状态字典)
        """
        # 初始化上下文
        if self.enable_blackboard:
            self._context = CollaborationContext()
            self._context.set_task(task)
        if self.enable_message_bus:
            self._bus = get_message_bus()
            # 注册所有 Agent
            for role_name, role in self.roles.items():
                self._bus.register_agent(role_name, {
                    "specialty": role.specialty,
                    "tools_whitelist": role.tools_whitelist,
                })

        # 按拓扑排序执行
        execution_order = self._dependency_graph.topological_sort()

        # 分批执行：每批包含无依赖关系的角色（可并行）
        while not self._dependency_graph.is_all_done():
            ready_roles = self._dependency_graph.get_ready_roles()
            if not ready_roles:
                break  # 无可执行角色（可能有循环依赖）

            # 并行执行就绪的角色
            tasks = []
            for role in ready_roles:
                self._dependency_graph.mark_running(role.name)
                # 构造子任务：包含前置 Agent 的产出
                subtask = self._build_subtask_with_context(task, role)
                tasks.append(self._run_single_agent_with_context(role, subtask, messages))

            # 等待本批完成
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            for role, result in zip(ready_roles, results):
                if isinstance(result, Exception):
                    self._dependency_graph.mark_failed(role.name, str(result))
                    if self._context:
                        self._context.set_progress(role.name, 0, 1, "failed")
                else:
                    self._dependency_graph.mark_done(role.name, result)
                    if self._context:
                        self._context.share_output(role.name, result)
                        self._context.set_progress(role.name, 1, 1, "done")

        # 汇总结果
        final_result = self._synthesize_results(task)
        status = self._dependency_graph.get_status()

        return final_result, status

    def _build_subtask_with_context(
        self,
        original_task: str,
        role: AgentRole,
    ) -> str:
        """构造包含上下文的子任务"""
        subtask = original_task

        if self._context:
            # 获取前置 Agent 的产出
            outputs = self._context.get_all_outputs()
            if outputs:
                context_parts = []
                for agent_name, output in outputs.items():
                    if agent_name != role.name:  # 不包含自己的产出
                        context_parts.append(f"[{agent_name} 的产出]\n{output}")

                if context_parts:
                    subtask = (
                        f"原始任务：{original_task}\n\n"
                        f"前置 Agent 产出：\n{'='*40}\n"
                        f"{chr(10).join(context_parts)}\n{'='*40}\n\n"
                        f"请基于以上信息完成你的部分。"
                    )

        return subtask

    async def _run_single_agent_with_context(
        self,
        role: AgentRole,
        subtask: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        """执行单个 Agent（带上下文）"""
        # 调用父类的 _run_single_agent
        return await self._run_single_agent(role, subtask, messages)

    def _synthesize_results(self, task: str) -> str:
        """汇总所有 Agent 的结果"""
        if not self._context:
            # 回退：取最后一个完成的角色的结果
            for name, node in reversed(list(self._dependency_graph._nodes.items())):
                if node.status == "done" and node.result:
                    return node.result
            return "无可用结果"

        outputs = self._context.get_all_outputs()
        if not outputs:
            return "无 Agent 产出"

        # 如果只有一个 Agent，直接返回
        if len(outputs) == 1:
            return list(outputs.values())[0]

        # 多个 Agent：拼接汇总
        parts = [f"# 多 Agent 协作结果\n任务：{task}\n"]
        for agent_name, output in outputs.items():
            parts.append(f"\n## {agent_name} 的产出\n{output}\n")

        return "\n".join(parts)

    async def run_with_consensus(
        self,
        task: str,
        messages: List[Dict[str, Any]],
        voter_roles: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """共识投票执行

        多个 Agent 独立完成任务，然后对结果投票选择最佳方案。

        Args:
            task: 任务描述
            messages: 对话历史
            voter_roles: 参与投票的角色（None 表示所有角色）

        Returns:
            (胜出结果, 投票详情)
        """
        # 第1步：所有角色独立完成任务
        independent_results: Dict[str, str] = {}
        tasks = []
        role_names = []

        for role_name, role in self.roles.items():
            role_names.append(role_name)
            tasks.append(self._run_single_agent(role, task, messages))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for name, result in zip(role_names, results):
            if isinstance(result, Exception):
                independent_results[name] = f"[错误] {result}"
            else:
                independent_results[name] = result

        # 第2步：投票选择最佳方案
        vote_details = await self._vote(task, independent_results, voter_roles)

        return vote_details["winner"], vote_details

    async def _vote(
        self,
        task: str,
        results: Dict[str, str],
        voter_roles: Optional[List[str]] = None,
        max_rounds: int = 3,
    ) -> Dict[str, Any]:
        """多轮淘汰制共识投票

        每轮所有投票者对剩余候选方案打分（1-10）并给出批判性评价，
        淘汰得分最低的方案，直到剩下一个或达到最大轮数。

        Returns:
            {
                "winner": str,
                "winner_agent": str,
                "votes": Dict[str, float],   # 最终累计得分
                "details": List[Dict],       # 每轮详细投票信息
                "elimination_history": List[Dict],
            }
        """
        if not results:
            return {"winner": "", "winner_agent": "", "votes": {}, "details": [],
                    "elimination_history": []}

        if len(results) == 1:
            name, result = next(iter(results.items()))
            return {
                "winner": result, "winner_agent": name,
                "votes": {name: 10.0},
                "details": [{"voter": "auto", "choice": name, "reason": "唯一方案"}],
                "elimination_history": [],
            }

        # 确定投票者
        if voter_roles is None:
            voter_roles = list(self.roles.keys())
        if not voter_roles:
            voter_roles = ["orchestrator"]

        candidates = dict(results)  # 候选方案（可被淘汰）
        all_details: List[Dict[str, Any]] = []
        elimination_history: List[Dict[str, Any]] = []
        cumulative_scores: Dict[str, float] = {n: 0.0 for n in results}

        for round_idx in range(max_rounds):
            if len(candidates) <= 1:
                break

            # 每轮：所有投票者对所有候选打分 + 批判
            round_scores: Dict[str, float] = {n: 0.0 for n in candidates}
            round_details: List[Dict[str, Any]] = []

            scoring_tasks = []
            for voter in voter_roles:
                scoring_tasks.append(
                    self._score_candidates(task, candidates, voter, round_idx)
                )

            scoring_results = await asyncio.gather(*scoring_tasks, return_exceptions=True)

            for voter, sr in zip(voter_roles, scoring_results):
                if isinstance(sr, Exception) or not sr:
                    continue
                for cand_name, score_info in sr.get("scores", {}).items():
                    if cand_name in round_scores:
                        round_scores[cand_name] += score_info["score"]
                        round_details.append({
                            "voter": voter,
                            "candidate": cand_name,
                            "score": score_info["score"],
                            "critique": score_info.get("critique", ""),
                            "round": round_idx,
                        })

            all_details.extend(round_details)

            for n, s in round_scores.items():
                cumulative_scores[n] = cumulative_scores.get(n, 0.0) + s

            # 淘汰：得分最低的 1-2 个方案
            ranked = sorted(round_scores.items(), key=lambda x: x[1])
            n_eliminate = min(len(ranked) - 1, 2 if len(ranked) > 4 else 1)
            eliminated = []
            for i in range(n_eliminate):
                elim_name, elim_score = ranked[i]
                eliminated.append({"name": elim_name, "score": elim_score})
                candidates.pop(elim_name, None)

            elimination_history.append({
                "round": round_idx,
                "remaining": list(candidates.keys()),
                "eliminated": eliminated,
                "round_scores": round_scores,
            })

        # 最终胜出
        if candidates:
            winner_name = max(candidates, key=lambda n: cumulative_scores.get(n, 0))
        else:
            winner_name = max(cumulative_scores, key=cumulative_scores.get)

        return {
            "winner": results[winner_name],
            "winner_agent": winner_name,
            "votes": cumulative_scores,
            "details": all_details,
            "elimination_history": elimination_history,
        }

    async def _score_candidates(
        self,
        task: str,
        candidates: Dict[str, str],
        voter: str,
        round_idx: int,
    ) -> Dict[str, Any]:
        """单个投票者对所有候选方案打分 + 批判性评价

        强制要求找出每个方案的缺陷（对抗性思维），而非简单选优。
        """
        cand_desc = "\n\n".join(
            f"=== 方案 [{name}] ===\n{result[:600]}"
            for name, result in candidates.items()
        )

        prompt = f"""你是评审专家 [{voter}]，第 {round_idx + 1} 轮评审。

任务：{task}

候选方案：
{cand_desc}

请对每个方案打分（1-10）并给出批判性评价。必须指出每个方案的缺陷。

输出 JSON：
```json
{{
  "scores": [
    {{"name": "方案名", "score": 8, "critique": "缺陷分析..."}},
    ...
  ]
}}
```

评判标准：
1. 正确性：是否正确完成任务
2. 完整性：是否覆盖所有要求
3. 质量：代码/文档质量
4. 清晰度：是否易于理解
5. 对抗性：必须找出真实缺陷，不能只说好话"""

        client = LLMClient(model_key=self.orchestrator_model)
        msgs = [{"role": "user", "content": prompt}]
        try:
            resp = await client.chat(msgs, temperature=0.3)
            match = re.search(r'\{.*\}', resp, re.DOTALL)
            if match:
                data = json.loads(match.group())
                scores_dict = {}
                for item in data.get("scores", []):
                    name = item.get("name", "")
                    if name in candidates:
                        scores_dict[name] = {
                            "score": max(1, min(10, float(item.get("score", 5)))),
                            "critique": item.get("critique", ""),
                        }
                return {"scores": scores_dict}
        except Exception:
            pass

        # 回退：均匀打分
        return {"scores": {n: {"score": 5.0, "critique": "评分失败"} for n in candidates}}


__all__ = [
    "RoleNode",
    "RoleDependencyGraph",
    "CollaborationContext",
    "EnhancedMultiAgentCollaborator",
]
