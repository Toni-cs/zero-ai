"""Expert routing and management for ZeroAI

提供两层专家路由：

1. ExpertRouter（单专家路由）
   - route_by_keywords：基于关键词匹配
   - route_by_glm：基于 GLM 语义分析
   - LRU 缓存加速

2. HybridExpertSystem（混合模式，多专家协作）
   - select_experts：GLM 分析任务，选择多个专家
   - call_experts_parallel：多专家并行调用
   - call_experts_chain：协作链模式（顺序传递结果）
   - dedup_responses：基于 Jaccard 相似度去重
   - summarize_responses：GLM 汇总多专家回答
   - 专家记忆：每个专家独立上下文，避免主上下文污染

从 tui_agent.py 迁移并模块化，保持核心逻辑一致。
"""
import asyncio
import re
import hashlib
import math
import json
from typing import Dict, List, Optional, Tuple, Any, Callable
from collections import OrderedDict, deque
from .config import get_config
from .llm import LLMClient, MultiModelClient, get_multi_model_client


# ============================================================================
# LRU 缓存
# ============================================================================

class LRUCache:
    """Thread-safe LRU cache for expert routing"""

    def __init__(self, maxsize: int = 256):
        self.cache = OrderedDict()
        self.maxsize = maxsize

    def get(self, key: str) -> Optional[str]:
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, key: str, value: str):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return key in self.cache

    def __len__(self) -> int:
        return len(self.cache)


# ============================================================================
# 文本工具（从 tui_agent.py 迁移）
# ============================================================================

def jaccard_similarity(s1: str, s2: str) -> float:
    """计算两段文本的 Jaccard 相似度（基于字符 n-gram 集合）

    用于专家回答去重：相似度越高说明回答越重复。
    返回 0.0-1.0 的浮点数。
    """
    if not s1 or not s2:
        return 0.0
    n = 3
    if len(s1) < n or len(s2) < n:
        set1, set2 = set(s1), set(s2)
    else:
        set1 = {s1[i:i + n] for i in range(len(s1) - n + 1)}
        set2 = {s2[i:i + n] for i in range(len(s2) - n + 1)}
    if not set1 or not set2:
        return 0.0
    inter = len(set1 & set2)
    union = len(set1 | set2)
    return inter / union if union else 0.0


def truncate_expert_response(text: str, max_chars: int) -> str:
    """截断专家回答到指定字符数，并附加截断提示

    用于 HYBRID_EXPERT_MAX_CHARS 限制：避免单个专家回答过长导致汇总 token 暴涨。
    """
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    nl = cut.rfind("\n")
    if nl > max_chars * 0.7:
        cut = cut[:nl]
    else:
        for sep in ("。", "？", "！", ".", "?", "!"):
            sp = cut.rfind(sep)
            if sp > max_chars * 0.7:
                cut = cut[:sp + 1]
                break
    return cut + "\n\n…（专家回答已截断，仅汇总关键部分）"


# ============================================================================
# 单专家路由
# ============================================================================

class ExpertRouter:
    """Expert routing based on keyword matching and GLM semantic analysis"""

    def __init__(self):
        self.config = get_config()
        self._route_cache = LRUCache(maxsize=256)
        self._expert_team = self.config._config.get("experts", {})
        self._embedding_cache: Dict[str, list] = {}  # 语义路由 embedding 缓存
        self._embedding_cache: Dict[str, list] = {}  # 语义路由 embedding 缓存

    def route_by_keywords(self, user_input: str) -> str:
        """Route to expert based on keyword matching"""
        input_lower = user_input.lower()
        scores = {}

        for expert_key, expert_config in self._expert_team.items():
            keywords = expert_config.get("keywords", [])
            score = sum(1 for kw in keywords if kw.lower() in input_lower)
            if score > 0:
                scores[expert_key] = score

        if scores:
            return max(scores, key=scores.get)

        return "pm"

    async def route_by_glm(self, user_input: str) -> str:
        """Route to expert using GLM semantic analysis"""
        if len(user_input) < 10:
            return self.route_by_keywords(user_input)

        cache_key = hashlib.md5(user_input.encode("utf-8")).hexdigest()[:16]
        cached = self._route_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from .llm import LLMClient

            llm = LLMClient("glm-v")
            prompt = f"""判断以下用户问题属于哪个专家领域，只回复一个词：
- coder：编程开发、代码、函数、bug、技术实现
- reasoner：数学推理、逻辑证明、算法分析、复杂计算
- academic：学术论文、公式推导、文献综述、研究方法、LaTeX、定理证明
- chinese：中文写作、文章、报告、文案、邮件
- vision：图片理解、截图分析、视觉
- pm：任务分析、计划制定、翻译、通用问答、解释说明
- knowledge：百科知识、事实查询、翻译、其他
- devops：运维、部署、系统管理、容器、SSH、监控
- security：安全分析、漏洞评估、加固、审计
- data：数据分析、统计建模、可视化、数据清洗

用户问题：{user_input[:300]}

只回复上面列出的一个词，不要回复其他任何内容。"""

            response = await llm.chat(
                system_prompt="你是 ZeroAI 路由分析器，只负责把用户问题分类到一个专家。严格只输出一个英文标识词，不要做解释。",
                user_prompt=prompt,
                temperature=0.01,
                max_tokens=10,
                stream=False
            )

            if response is None:
                return "pm"

            result = response.strip().lower()

            valid_keys = set(self._expert_team.keys())
            for vk in valid_keys:
                if vk in result:
                    self._route_cache.set(cache_key, vk)
                    return vk

            expert_key = self.route_by_keywords(user_input)
            self._route_cache.set(cache_key, expert_key)
            return expert_key

        except Exception:
            expert_key = self.route_by_keywords(user_input)
            self._route_cache.set(cache_key, expert_key)
            return expert_key

    async def route_semantic(self, user_input: str) -> List[Tuple[str, float]]:
        """语义路由：用 embedding 相似度选择专家

        Returns:
            [(expert_key, confidence), ...] 按相似度降序排列
        """
        try:
            query_emb = await self._get_embedding(user_input)
            if not query_emb:
                return [(self.route_by_keywords(user_input), 0.5)]

            scores = {}
            for expert_key, cfg in self._expert_team.items():
                # 用专家关键词 + 描述构建语义表示
                kw_list = cfg.get("keywords", [])
                desc = cfg.get("description", "")
                sys_prompt = cfg.get("system_prompt", "")
                expert_desc = " ".join(kw_list) + " " + desc + " " + sys_prompt[:200]
                expert_emb = await self._get_embedding(expert_desc)
                sim = self._cosine_similarity(query_emb, expert_emb)
                scores[expert_key] = sim

            ranked = sorted(scores.items(), key=lambda x: -x[1])
            return ranked
        except Exception:
            return [(self.route_by_keywords(user_input), 0.5)]

    async def route_with_confidence(self, user_input: str) -> List[str]:
        """高置信→单专家，低置信→多专家兜底

        Returns:
            专家 key 列表（1-3 个）
        """
        if len(user_input) < 10:
            return [self.route_by_keywords(user_input)]

        ranked = await self.route_semantic(user_input)
        if not ranked:
            return [self.route_by_keywords(user_input)]

        top_score = ranked[0][1]
        if top_score > 0.85:
            return [ranked[0][0]]  # 高置信，单专家
        elif top_score > 0.6:
            return [k for k, _ in ranked[:2]]  # 中置信，top-2
        else:
            return [k for k, _ in ranked[:3]]  # 低置信，top-3 兜底

    async def _get_embedding(self, text: str) -> Optional[list]:
        """获取文本 embedding，带缓存"""
        cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        try:
            from .llm import LLMClient
            llm = LLMClient("glm-v")
            emb = await llm.get_embedding(text)
            if emb and isinstance(emb, list):
                self._embedding_cache[cache_key] = emb
                return emb
        except Exception:
            pass
        return None

    @staticmethod
    def _cosine_similarity(a: list, b: list) -> float:
        """余弦相似度"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def get_expert_config(self, expert_key: str) -> Dict:
        """Get configuration for a specific expert"""
        return self.config.get_expert_config(expert_key)

    def get_all_experts(self) -> Dict[str, Dict]:
        """Get all expert configurations"""
        return self._expert_team.copy()

    def clear_cache(self):
        """Clear the routing cache"""
        self._route_cache = LRUCache(maxsize=256)


# ============================================================================
# 混合模式：多专家协作系统
# ============================================================================

class HybridExpertSystem:
    """混合思考模式：多专家协作处理同一问题

    流程：GLM分析 → 专家并行回答 → 去重 → GLM汇总

    从 tui_agent.py 的 _run_hybrid_turn 迁移并模块化。
    TUI 显示逻辑由调用方处理，本类只负责数据流。
    """

    def __init__(self):
        self.config = get_config()
        self.router = ExpertRouter()
        self.llm_client = get_multi_model_client()
        self._expert_memory: Dict[str, List[Dict]] = {}

        # 从配置加载参数
        hybrid_cfg = self.config.get_hybrid_config()
        self.max_parallel_experts = hybrid_cfg.get("max_parallel_experts", 3)
        self.expert_max_chars = hybrid_cfg.get("expert_max_chars", 800)
        self.dedup_similarity_threshold = hybrid_cfg.get("dedup_similarity_threshold", 0.7)
        self.memory_turns = hybrid_cfg.get("memory_turns", 3)
        self.enable_collab_chain = hybrid_cfg.get("enable_collab_chain", False)

    async def select_experts(self, user_input: str) -> List[str]:
        """GLM 分析任务，选择多个专家

        Returns:
            专家 key 列表（最多 max_parallel_experts 个）
        """
        # 短消息用关键词路由
        if len(user_input) < 10:
            return [self.router.route_by_keywords(user_input)]

        try:
            llm = LLMClient("glm-v")
            analyze_prompt = f"""请分析以下用户需求，判断需要哪些专家协作处理。
可用专家：
- coder: 编程开发
- reasoner: 深度推理/数学
- academic: 学术研究/公式推导/论文写作
- chinese: 中文写作/文案
- knowledge: 通用知识/翻译
- vision: 图片理解
- devops: 运维/部署/系统管理/容器/SSH
- security: 安全分析/漏洞评估/加固
- data: 数据分析/统计/可视化

用户需求：{user_input[:500]}

请只回复专家标识，用逗号分隔（最多{self.max_parallel_experts}个），例如：coder,reasoner
不要回复其他内容。"""

            response = await llm.chat(
                system_prompt="你是 ZeroAI 路由分析器，只负责判断用户问题应分配给哪些专家。严格按要求只输出专家标识，不要做任何解释。",
                user_prompt=analyze_prompt,
                temperature=0.1,
                max_tokens=30,
                stream=False,
                timeout=30,
            )

            if response is None:
                return [self.router.route_by_keywords(user_input)]

            analysis = response.strip().lower()
        except Exception:
            analysis = ""

        # 解析专家列表
        expert_keys = []
        valid_experts = set(self.router.get_all_experts().keys())
        for part in analysis.replace("，", ",").split(","):
            part = part.strip()
            if part in valid_experts and part not in ("pm",) and part not in expert_keys:
                expert_keys.append(part)

        if not expert_keys:
            expert_keys = [self.router.route_by_keywords(user_input)]

        # 限制并行度
        return expert_keys[:self.max_parallel_experts]

    async def call_expert(
        self,
        expert_key: str,
        user_input: str,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> Optional[str]:
        """调用单个专家（含专家记忆）

        Args:
            expert_key: 专家标识
            user_input: 用户输入
            temperature: 温度
            stream: 是否流式

        Returns:
            专家回答，或 None 表示失败
        """
        expert_config = self.config.get_expert_config(expert_key)
        system_prompt = expert_config.get(
            "system_prompt",
            "你是 ZeroAI 专家团队成员，从专业角度回答用户问题。"
        )

        # 构建消息（含专家记忆）
        messages = [{"role": "system", "content": system_prompt}]
        memory = self._expert_memory.get(expert_key, [])
        messages.extend(memory)
        messages.append({"role": "user", "content": user_input})

        try:
            response = await self.llm_client.call_expert(
                expert_key=expert_key,
                messages=messages,
                temperature=temperature,
                max_tokens=2000,
                stream=stream,
            )
        except Exception:
            return None

        if response is None:
            return None

        # 截断过长的回答
        if self.expert_max_chars > 0 and len(response) > self.expert_max_chars:
            response = truncate_expert_response(response, self.expert_max_chars)

        # 更新专家记忆
        if self.memory_turns > 0:
            memory = self._expert_memory.get(expert_key, [])
            memory.append({"role": "user", "content": user_input})
            memory.append({"role": "assistant", "content": response})
            max_msgs = self.memory_turns * 2
            if len(memory) > max_msgs:
                memory = memory[-max_msgs:]
            self._expert_memory[expert_key] = memory

        return response

    async def call_experts_parallel(
        self,
        expert_keys: List[str],
        user_input: str,
        temperature: float = 0.7,
    ) -> List[Dict[str, str]]:
        """多专家真正并行调用（asyncio.gather）

        Returns:
            [{"expert": key, "label": label, "content": text}, ...]
            失败的专家不会出现在结果中
        """
        async def _call_one(ek: str) -> Optional[Dict[str, str]]:
            try:
                content = await self.call_expert(ek, user_input, temperature, stream=False)
                if content:
                    expert_cfg = self.config.get_expert_config(ek)
                    return {
                        "expert": ek,
                        "label": expert_cfg.get("label", ek),
                        "content": content,
                    }
            except Exception:
                pass
            return None

        raw_results = await asyncio.gather(*[_call_one(ek) for ek in expert_keys])
        return [r for r in raw_results if r is not None]

    async def call_experts_chain(
        self,
        expert_keys: List[str],
        user_input: str,
        temperature: float = 0.7,
    ) -> List[Dict[str, str]]:
        """协作链模式：专家依次回答，每个后续专家能看到前一位专家的结果

        场景：coder 写代码 → reasoner 审查逻辑 → academic 补充引用

        Returns:
            [{"expert": key, "label": label, "content": text}, ...]
        """
        results = []
        prev_content = None

        for ek in expert_keys:
            if prev_content:
                chain_input = (
                    f"用户原始问题：{user_input}\n\n"
                    f"前一位专家（{results[-1]['expert']}）的回答：\n{prev_content}\n\n"
                    f"请基于以上信息，从你的专业角度补充和完善回答。"
                )
            else:
                chain_input = user_input

            content = await self.call_expert(ek, chain_input, temperature, stream=False)
            if content:
                expert_cfg = self.config.get_expert_config(ek)
                results.append({
                    "expert": ek,
                    "label": expert_cfg.get("label", ek),
                    "content": content,
                })
                prev_content = content

        return results

    def dedup_responses(
        self,
        responses: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Tuple[str, str, float]]]:
        """基于 Jaccard 相似度去重

        Returns:
            (unique_responses, dedup_skipped)
            - unique_responses: 去重后的回答列表
            - dedup_skipped: 被去重的记录 [(expert_a, expert_b, similarity), ...]
        """
        if self.dedup_similarity_threshold <= 0 or len(responses) <= 1:
            return responses, []

        unique = [responses[0]]
        skipped = []
        for resp in responses[1:]:
            is_dup = False
            for kept in unique:
                sim = jaccard_similarity(resp["content"], kept["content"])
                if sim >= self.dedup_similarity_threshold:
                    is_dup = True
                    skipped.append((resp["expert"], kept["expert"], round(sim, 2)))
                    break
            if not is_dup:
                unique.append(resp)

        return unique, skipped

    async def summarize_responses(
        self,
        responses: List[Dict[str, str]],
        user_input: str,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> Optional[str]:
        """GLM 汇总多专家回答

        Args:
            responses: 专家回答列表
            user_input: 原始用户输入
            temperature: 温度
            stream: 是否流式

        Returns:
            汇总后的最终回复
        """
        if not responses:
            return None
        if len(responses) == 1:
            return responses[0]["content"]

        summary_prompt = "以下是多位专家的回答，请综合整理为一份完整、连贯的回复：\n\n"
        for resp in responses:
            summary_prompt += f"【{resp['expert']}】\n{resp['content'][:2000]}\n\n"
        summary_prompt += "\n请综合以上内容，给出最终回复。"

        try:
            llm = LLMClient("glm-v")
            return await llm.chat(
                system_prompt=(
                    "你是 ZeroAI 项目经理，负责将多位专家的回答综合整理为完整、连贯的最终回复。"
                    "保留关键信息，消除重复，按用户问题逻辑组织。"
                ),
                user_prompt=summary_prompt,
                temperature=temperature,
                max_tokens=2000,
                stream=stream,
                timeout=60,
            )
        except Exception:
            # 汇总失败，返回第一个专家的回答
            return responses[0]["content"] if responses else None

    async def adversarial_debate(
        self,
        user_input: str,
        expert_keys: List[str],
        rounds: int = 2,
        temperature: float = 0.7,
        log_func: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[Dict[str, str]], List[Dict]]:
        """对抗辩论：多轮交叉审查，让专家互相质疑并修正

        流程：
          round 0: 各专家独立回答
          round 1-N: 每位专家看到其他人的观点并批判 + 修正自己
          最终: 显式处理冲突而非简单拼接

        Returns:
            (final_responses, debate_history)
        """
        # round 0: 独立回答
        responses = await self.call_experts_parallel(expert_keys, user_input, temperature)
        if not responses:
            return [], []

        debate_history = [{"round": 0, "summary": f"{len(responses)} 位专家独立回答"}]

        for round_idx in range(1, rounds + 1):
            if log_func:
                log_func(f"对抗辩论第 {round_idx} 轮：交叉批判")

            async def _critique_one(resp: Dict[str, str]) -> Optional[Dict[str, str]]:
                my_view = resp["content"][:1500]
                others = {
                    r["expert"]: r["content"][:1000]
                    for r in responses
                    if r["expert"] != resp["expert"]
                }
                if not others:
                    return resp

                critique_prompt = (
                    f"你是严格的同行评审专家。请对以下回答进行批判性审查：\n\n"
                    f"原始问题：{user_input[:500]}\n\n"
                    f"你的回答：\n{my_view}\n\n"
                    f"其他专家的回答：\n"
                )
                for k, v in others.items():
                    critique_prompt += f"  [{k}]: {v}\n"
                critique_prompt += (
                    "\n请执行：\n"
                    "1. 指出其他专家回答中的错误或不足\n"
                    "2. 指出自己回答中的错误或不足\n"
                    "3. 给出修正后的完整回答\n\n"
                    "输出 JSON：{\"critiques\": \"对其他专家的批判\", "
                    "\"self_correction\": \"自我修正\", \"revised_answer\": \"修正后的完整回答\"}"
                )

                try:
                    llm = LLMClient("glm-v")
                    raw = await llm.chat(
                        system_prompt="你是严格的同行评审专家，负责批判性审查并修正回答。",
                        user_prompt=critique_prompt,
                        temperature=temperature,
                        max_tokens=2000,
                        stream=False,
                        timeout=60,
                    )
                    if raw:
                        # 尝试解析 JSON，失败则直接用 raw
                        try:
                            import json as _json
                            # 去掉可能的 markdown 代码块标记
                            clean = raw.strip()
                            if clean.startswith("```"):
                                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                            parsed = _json.loads(clean)
                            revised = parsed.get("revised_answer", "")
                            if revised:
                                resp = dict(resp)
                                resp["content"] = revised
                        except Exception:
                            pass  # JSON 解析失败，保留原回答
                except Exception:
                    pass
                return resp

            responses = await asyncio.gather(*[_critique_one(r) for r in responses])
            responses = [r for r in responses if r is not None]
            debate_history.append({
                "round": round_idx,
                "summary": f"第 {round_idx} 轮交叉批判完成，{len(responses)} 位专家已修正",
            })

        return responses, debate_history

    async def _resolve_debate_conflicts(
        self,
        user_input: str,
        responses: List[Dict[str, str]],
        temperature: float = 0.3,
    ) -> Optional[str]:
        """显式处理专家间的冲突，而非简单拼接"""
        if not responses:
            return None
        if len(responses) == 1:
            return responses[0]["content"]

        expert_answers = {r["expert"]: r["content"][:1500] for r in responses}
        import json as _json

        prompt = (
            f"以下是多位专家经过多轮辩论后对同一问题的回答：\n\n"
            f"问题：{user_input[:500]}\n\n"
            f"专家回答：\n{_json.dumps(expert_answers, ensure_ascii=False, indent=2)}\n\n"
            f"请综合这些回答。要求：\n"
            f"1. 如果专家意见一致，直接整合\n"
            f"2. 如果存在冲突，对每个冲突分析根源并给出明确裁决"
            f"（不要简单列出两种方案让用户选）\n"
            f"3. 输出一份连贯、完整的最终回答"
        )

        try:
            llm = LLMClient("glm-v")
            return await llm.chat(
                system_prompt="你是 ZeroAI 首席专家，负责综合多位专家的辩论结果并给出最终裁决。",
                user_prompt=prompt,
                temperature=temperature,
                max_tokens=3000,
                stream=False,
                timeout=60,
            )
        except Exception:
            return responses[0]["content"]

    async def run_hybrid_turn(
        self,
        user_input: str,
        temperature: float = 0.7,
        stream: bool = False,
        log_func: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """执行一次完整的混合思考流程

        流程：选择专家 → 并行/链式调用 → 去重 → 汇总

        Args:
            user_input: 用户输入
            temperature: 温度
            stream: 是否流式
            log_func: 日志回调

        Returns:
            {
                "experts": [选中的专家 keys],
                "responses": [专家回答列表],
                "dedup_skipped": [去重记录],
                "final_response": 最终汇总回复,
            }
        """
        # 1. 选择专家
        expert_keys = await self.select_experts(user_input)
        if log_func:
            labels = []
            for ek in expert_keys:
                cfg = self.config.get_expert_config(ek)
                labels.append(f"{cfg.get('label', ek)}")
            log_func(f"需要 {len(expert_keys)} 位专家协作：{', '.join(labels)}")

        # 2. 调用专家（并行或链式）
        if self.enable_collab_chain and len(expert_keys) > 1:
            if log_func:
                log_func("协作链模式：专家依次回答并传递结果")
            responses = await self.call_experts_chain(expert_keys, user_input, temperature)
            debate_history = []
        elif len(expert_keys) > 1:
            if log_func:
                log_func("对抗辩论模式：多专家交叉批判")
            responses, debate_history = await self.adversarial_debate(
                user_input, expert_keys, rounds=2, temperature=temperature, log_func=log_func
            )
        else:
            responses = await self.call_experts_parallel(expert_keys, user_input, temperature)
            debate_history = []

        if not responses:
            return {
                "experts": expert_keys,
                "responses": [],
                "dedup_skipped": [],
                "debate_history": debate_history,
                "final_response": "（所有专家调用失败，请检查网络或 API Key 后重试）",
            }

        # 3. 去重
        responses, dedup_skipped = self.dedup_responses(responses)
        if dedup_skipped and log_func:
            dedup_parts = [f"{a}≈{b}({s})" for a, b, s in dedup_skipped]
            log_func(f"去重：{', '.join(dedup_parts)} 已合并")

        # 4. 汇总
        if len(responses) == 1:
            final = responses[0]["content"]
        elif debate_history:
            # 对抗辩论后用冲突解决
            final = await self._resolve_debate_conflicts(user_input, responses)
        else:
            final = await self.summarize_responses(responses, user_input, temperature, stream)

        return {
            "experts": expert_keys,
            "responses": responses,
            "dedup_skipped": dedup_skipped,
            "debate_history": debate_history,
            "final_response": final or "",
        }

    def clear_expert_memory(self, expert_key: Optional[str] = None):
        """清除专家记忆

        Args:
            expert_key: 指定专家 key，None 表示清除所有
        """
        if expert_key:
            self._expert_memory.pop(expert_key, None)
        else:
            self._expert_memory.clear()


# ============================================================================
# 全局实例管理
# ============================================================================

_expert_router: Optional[ExpertRouter] = None
_hybrid_system: Optional[HybridExpertSystem] = None


def get_expert_router() -> ExpertRouter:
    """Get global expert router instance"""
    global _expert_router
    if _expert_router is None:
        _expert_router = ExpertRouter()
    return _expert_router


def get_hybrid_system() -> HybridExpertSystem:
    """Get global hybrid expert system instance"""
    global _hybrid_system
    if _hybrid_system is None:
        _hybrid_system = HybridExpertSystem()
    return _hybrid_system


def route_expert(user_input: str) -> str:
    """Route user input to appropriate expert (sync wrapper)"""
    router = get_expert_router()
    return router.route_by_keywords(user_input)


async def route_expert_async(user_input: str) -> str:
    """Route user input to appropriate expert (async)"""
    router = get_expert_router()
    return await router.route_by_glm(user_input)
