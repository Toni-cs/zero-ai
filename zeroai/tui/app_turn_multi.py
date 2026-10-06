"""多专家回合并行变体：_run_hybrid_turn 混合思考、_run_react_turn ReAct 循环（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import asyncio
from openai import AsyncOpenAI
from rich.text import Text
from zeroai.core.constants import CLEANUP_THRESHOLD_RATIO, COMPRESS_THRESHOLD_RATIO, EXPERT_MEMORY_TURNS, EXPERT_TEAM, HYBRID_DEDUP_SIMILARITY_THRESHOLD, HYBRID_ENABLE_COLLAB_CHAIN, HYBRID_EXPERT_MAX_CHARS, HYBRID_MAX_PARALLEL_EXPERTS, MODEL_CONFIGS
from zeroai.core.context_compress import _estimate_tokens, cleanup_and_compress
from zeroai.core.expert_route import get_expert_config, route_expert
from zeroai.core.prompts import TOOL_CAPABILITY_PROMPT
from zeroai.core.response_utils import _jaccard_similarity, _parse_think_tags, _sanitize_identity_leak, _strip_model_tokens, _truncate_expert_response
from zeroai.core.runtime import _interruptible_await, _set_stop_flag
from zeroai.core.secrets import _is_proxy_enabled, _make_openai_client
from zeroai.tools.registry import TOOL_MAP, TOOLS
from zeroai.tools.render import _safe_markdown, render_latex_in_text
from zeroai.tui.colors import C_BLUE, C_CYAN, C_DIM, C_FG, C_GREEN, C_PURPLE, C_YELLOW
from zeroai.tui.icons import _load_svg_icon


class MultiTurnMixin:
    """多专家回合并行变体：_run_hybrid_turn 混合思考、_run_react_turn ReAct 循环"""

    async def _run_hybrid_turn(self):
        """混合思考模式：多专家协作处理同一问题
        流程：GLM分析 → 专家回答 → GLM汇总
        """
        self._is_generating = True
        self._stop_generation = False
        # 重置全局停止标志
        _set_stop_flag(False)
        from openai import AsyncOpenAI

        # ── API Key 检查：GLM 未配置时直接提示 ──
        glm_key = MODEL_CONFIGS.get("glm", {}).get("api_key", "")
        if not glm_key:
            self._add_static(Text.assemble(
                (f"  {_load_svg_icon('warning')} 混合思考需要智谱GLM 密钥\n", f"bold {C_FG}"),
                (f"  请按 Ctrl+P 打开设置面板配置 GLM API Key\n", f"bold {C_FG}"),
                (f"  智谱GLM 免费 Key 获取：https://open.bigmodel.cn/\n", C_DIM),
                (f"  或输入 /手动 切换到单模型模式\n", C_DIM),
            ))
            self._is_generating = False
            return

        # ── 上下文管理：主动清理 + 按需压缩（两层防护，防止幻觉）──
        def _ctx_log(text, style=None):
            self._add_static(Text.assemble(
                (text, style if style else C_DIM),
            ))

        # 用 block 显示清理/压缩进度
        est_tokens_pre = _estimate_tokens(self.messages)
        cleanup_threshold = int(self.context_limit * CLEANUP_THRESHOLD_RATIO)
        compress_threshold = int(self.context_limit * COMPRESS_THRESHOLD_RATIO)

        if est_tokens_pre > cleanup_threshold and len(self.messages) > 8:
            block_ctx = self._add_block("上下文管理", C_DIM)
            block_ctx.update(Text.assemble(
                (f"  {_load_svg_icon('tool')} 上下文管理\n", f"bold {C_DIM}"),
                (f"  │ 当前约 {est_tokens_pre} tokens（清理阈值 {cleanup_threshold}，压缩阈值 {compress_threshold}）\n", C_DIM),
            ))
            await cleanup_and_compress(self, _ctx_log)
            self._precise_input_tokens = 0
        else:
            # 未触发清理阈值，但仍调用一次（内部会判断是否需要压缩）
            await cleanup_and_compress(self, _ctx_log if est_tokens_pre > compress_threshold else None)
            self._precise_input_tokens = 0

        # 获取用户最后一条消息
        last_user = ""
        for msg in reversed(self.messages):
            if msg["role"] == "user":
                last_user = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
                break
        if not last_user:
            self._is_generating = False
            return

        # ── 第1步：项目经理GLM-4.7分析任务，选择专家 ──
        block1 = self._add_block("项目经理·GLM-4.7", C_BLUE)
        block1.update(Text.assemble(
            ("  ┌─ 项目经理·GLM-4.7\n", f"bold {C_BLUE}"),
            ("  │ 分析任务中…\n", C_DIM),
        ))

        glm_cfg = MODEL_CONFIGS["glm-v"]  # 多模态经理，支持图片
        glm_client = _make_openai_client("glm-v")
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

用户需求：{last_user[:500]}

请只回复专家标识，用逗号分隔（最多{HYBRID_MAX_PARALLEL_EXPERTS}个），例如：coder,reasoner
不要回复其他内容。"""

        try:
            resp = await _interruptible_await(glm_client.chat.completions.create(
                model=glm_cfg["model"],
                messages=[{"role": "system", "content": "你是 ZeroAI 路由分析器，只负责判断用户问题应分配给哪些专家。严格按要求只输出专家标识，不要做任何解释。"},
                          {"role": "user", "content": analyze_prompt}],
                temperature=0.1,
                stream=False,
            ))
            if resp is None or self._stop_generation:
                self._is_generating = False
                self._add_static(Text("  ⏹ 已停止\n", style=C_DIM))
                return
            analysis = resp.choices[0].message.content.strip().lower()
        except Exception as e:
            analysis = ""

        # 解析专家列表
        expert_keys = []
        for part in analysis.replace("，", ",").split(","):
            part = part.strip()
            if part in EXPERT_TEAM and part not in ("pm",):
                expert_keys.append(part)
        if not expert_keys:
            # 降级：用关键词路由
            expert_keys = [route_expert(last_user)]
        # 专家并行度控制：限制最多并行专家数（避免 token 暴涨）
        expert_keys = expert_keys[:HYBRID_MAX_PARALLEL_EXPERTS]

        analysis_md = f"**任务分析**\n\n需要 {len(expert_keys)} 位专家协作：\n"
        for ek in expert_keys:
            analysis_md += f"- {EXPERT_TEAM[ek]['label']}：{EXPERT_TEAM[ek]['desc']}\n"
        block1.update(Text.assemble(
            ("  ┌─ 项目经理·GLM-4.7\n", f"bold {C_BLUE}"),
            ("  │ ", C_DIM),
        ))
        self._add_static(_safe_markdown(render_latex_in_text(analysis_md), code_theme="monokai"))
        self._add_static(Text("  └─", style=C_DIM))

        if self._stop_generation:
            self._is_generating = False
            return

        # ── 第2步：所有专家并行回答（asyncio.gather 同时调用）──
        # 预先为每个专家创建输出区块，显示"思考中…"
        expert_blocks = {}
        for ek in expert_keys:
            expert = EXPERT_TEAM[ek]
            block_e = self._add_block(f"专家·{expert['label']}", C_YELLOW)
            block_e.update(Text.assemble(
                (f"  ┌─ {expert['label']}\n", f"bold {C_YELLOW}"),
                ("  │ 思考中…\n", C_DIM),
            ))
            expert_blocks[ek] = block_e

        # 单个专家调用协程（并行任务单元）
        async def _call_expert(ek: str, user_msg: str = None) -> dict:
            """并行调用单个专家，返回 {"expert": label, "content": text} 或 None

            集成三项优化：
            - 专家记忆：加载/保存独立上下文（EXPERT_MEMORY_TURNS），避免主上下文污染
            - 失败降级：任何异常都返回 None，不阻断整体协作流程
            - 长度限制：最终回答截断到 HYBRID_EXPERT_MAX_CHARS，便于汇总
            - 协作链：user_msg 可传入前一位专家的结果（默认用 last_user）
            """
            # 检查是否已被 Ctrl+C 停止
            if self._stop_generation:
                return None
            # 协作链支持：允许传入自定义用户消息（含前一位专家结果）
            actual_user_msg = user_msg if user_msg is not None else last_user
            expert = EXPERT_TEAM[ek]
            e_cfg = get_expert_config(ek)
            block_e = expert_blocks[ek]

            # ── 构建消息列表（含专家记忆：独立上下文，避免主上下文污染） ──
            sys_msg = {"role": "system", "content": TOOL_CAPABILITY_PROMPT + "\n\n" + expert.get("system_prompt", "你是 ZeroAI 专家团队成员，从专业角度回答用户问题。")}
            # 加载专家记忆（最近 EXPERT_MEMORY_TURNS 轮对话，每轮=用户问+专家答=2条消息）
            memory = self._expert_memory.get(ek, [])
            e_messages = [sys_msg] + list(memory) + [{"role": "user", "content": actual_user_msg}]

            # ── 标准单模型调用（带超时重试 + 失败降级） ──
            max_tries = 3  # hybrid 模式：最多重试 3 次
            e_stream = None
            for _try in range(max_tries):
                try:
                    e_client = _make_openai_client(e_cfg.get("model_key", "glm"))
                    if not _is_proxy_enabled():
                        # 本地模式：保留原 timeout/max_retries
                        e_client = AsyncOpenAI(
                            base_url=e_cfg["base_url"],
                            api_key=e_cfg["api_key"],
                            timeout=180.0, max_retries=0,
                        )
                    e_stream = await e_client.chat.completions.create(
                        model=e_cfg["model"],
                        messages=e_messages,
                        temperature=self.temperature,
                        stream=self.stream_enabled,
                        timeout=180,
                    )
                    break
                except Exception as retry_e:
                    err_str = str(retry_e).lower()
                    is_to = "timeout" in err_str or "timed out" in err_str or "readtimeout" in type(retry_e).__name__.lower()
                    is_rl = "429" in str(retry_e) or "rate" in err_str
                    if (is_to or is_rl) and _try < max_tries - 1:
                        wait = _try + 1  # 1s, 2s
                        block_e.update(Text.assemble(
                            (f"  ├─ {expert['label']}\n", f"bold {C_GREEN}"),
                            ("  │ ", C_DIM),
                            (f"⏳ {'超时' if is_to else '限流'}，{wait}秒后重试", f"bold {C_YELLOW}"),
                            (f"（第{_try+1}/{max_tries}次）\n", C_DIM),
                        ))
                        await asyncio.sleep(wait)
                        if self._stop_generation:
                            return None
                        continue
                    # 失败降级：重试耗尽或不可重试异常，跳过该专家而非整个流程失败
                    block_e.update(Text.assemble(
                        (f"  ├─ {expert['label']}\n", f"bold {C_FG}"),
                        ("  │ ", C_DIM),
                        (f"⚠ 调用失败，已跳过该专家：{str(retry_e)[:80]}\n", f"bold {C_YELLOW}"),
                    ))
                    return None
            if e_stream is None:
                # 失败降级：未获取到流，跳过
                return None
            try:
                e_content = ""
                e_reasoning = ""
                if self.stream_enabled:
                    async for chunk in e_stream:
                        if self._stop_generation:
                            break
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        # 捕获思考内容（原生 reasoning_content）
                        rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                        if rc:
                            e_reasoning += rc
                            # 合并 <think> 标签内容
                            think_in, body = _parse_think_tags(e_content)
                            combined = (e_reasoning + ("\n" + think_in if think_in else "")).strip()
                            if combined:
                                self._update_streaming_with_reasoning(block_e, body, combined)
                            else:
                                self._update_streaming(block_e, body)
                            await asyncio.sleep(0)
                        if delta.content:
                            e_content += delta.content
                            # 过滤模型内部特殊标签（<|observation|> <|system|> 等）
                            e_content = _strip_model_tokens(e_content)
                            # 解析 <think> 标签，分离思考与正文
                            think_in, body = _parse_think_tags(e_content)
                            combined = (e_reasoning + ("\n" + think_in if think_in else "")).strip()
                            if combined:
                                self._update_streaming_with_reasoning(block_e, body, combined)
                            else:
                                self._update_streaming(block_e, body)
                            await asyncio.sleep(0)
                else:
                    e_content = _strip_model_tokens(e_stream.choices[0].message.content or "")
                    e_reasoning = getattr(e_stream.choices[0].message, "reasoning_content", "") or ""
                # 最终更新：分离 think 和 body，合并所有思考内容
                think_in, body = _parse_think_tags(e_content)
                combined_reasoning = (e_reasoning + ("\n" + think_in if think_in else "")).strip()
                if combined_reasoning:
                    self._update_streaming_with_reasoning(block_e, body, combined_reasoning, final=True)
                else:
                    self._update_streaming(block_e, body, final=True)
                # 后续使用 body（已去除 <think> 标签）
                e_content = body

                # ── 专家回答长度限制：截断到 HYBRID_EXPERT_MAX_CHARS，便于汇总 ──
                if HYBRID_EXPERT_MAX_CHARS > 0 and len(e_content) > HYBRID_EXPERT_MAX_CHARS:
                    e_content = _truncate_expert_response(e_content, HYBRID_EXPERT_MAX_CHARS)

                if e_content.strip():
                    # ── 保存到专家记忆（独立上下文，避免主上下文污染） ──
                    if EXPERT_MEMORY_TURNS > 0:
                        memory.append({"role": "user", "content": actual_user_msg})
                        memory.append({"role": "assistant", "content": e_content})
                        # 仅保留最近 EXPERT_MEMORY_TURNS 轮（每轮 2 条消息）
                        max_msgs = EXPERT_MEMORY_TURNS * 2
                        if len(memory) > max_msgs:
                            memory = memory[-max_msgs:]
                        self._expert_memory[ek] = memory
                    return {"expert": expert["label"], "content": e_content, "expert_key": ek}
                return None
            except Exception as e:
                # 失败降级：流处理过程中的异常，跳过该专家而非整个流程失败
                err_msg = f"  ⚠ {expert['label']} 调用失败，已跳过：{str(e)[:80]}\n"
                try:
                    block_e.update(Text(err_msg, style=C_DIM))
                except Exception:
                    self._add_static(Text(err_msg, style=C_DIM))
                return None
            finally:
                self._add_static(Text("  └─", style=C_DIM))

        # ── 执行专家调用：协作链（顺序）或并行 ──
        # 专家协作链：支持专家间传递结果（如 coder 写代码 → reasoner 审查逻辑 → academic 补充引用）
        # 默认关闭（HYBRID_ENABLE_COLLAB_CHAIN=False），避免 token 消耗翻倍
        if HYBRID_ENABLE_COLLAB_CHAIN and len(expert_keys) > 1:
            # 协作链模式：顺序调用，每个后续专家能看到前一位专家的回答
            self._add_static(Text("  🔗 协作链模式：专家依次回答并传递结果\n", style=C_DIM))
            expert_responses = []
            prev_response = ""
            for ek in expert_keys:
                if self._stop_generation:
                    break
                # 将前一位专家的回答注入本次用户问题，形成协作链
                if prev_response:
                    chain_user_msg = (
                        f"{last_user}\n\n"
                        f"── 上一环节专家（{EXPERT_TEAM[expert_keys[0]]['label']}）的回答 ──\n"
                        f"{prev_response[:HYBRID_EXPERT_MAX_CHARS]}\n"
                        f"── 请在此基础上从你的专业角度补充/审查/完善 ──"
                    )
                else:
                    chain_user_msg = last_user
                # 通过 user_msg 参数传递协作链上下文
                result = await _call_expert(ek, chain_user_msg)
                if isinstance(result, dict) and result.get("content"):
                    expert_responses.append(result)
                    prev_response = result["content"]
                # 协作链中某专家失败：降级跳过，继续下一个专家（不阻断链）
            parallel_results = expert_responses  # 统一变量名
        else:
            # 并行模式：所有专家同时调用（默认，token 效率最优）
            parallel_results = await asyncio.gather(
                *[_call_expert(ek) for ek in expert_keys],
                return_exceptions=True,
            )

        # 收集成功的回复
        expert_responses = []
        if HYBRID_ENABLE_COLLAB_CHAIN and len(expert_keys) > 1:
            # 协作链模式：parallel_results 已经是 list[dict]
            for result in parallel_results:
                if isinstance(result, dict) and result.get("content"):
                    expert_responses.append(result)
        else:
            # 并行模式：parallel_results 是 gather 的返回（含异常）
            for result in parallel_results:
                if isinstance(result, dict) and result.get("content"):
                    expert_responses.append(result)

        if not expert_responses:
            self._add_static(Text(f"  {_load_svg_icon('cross')} 所有专家调用失败\n", style=C_FG))
            # 复制修复：即使所有专家失败，也保存错误提示到 _last_reply_text，避免 Ctrl+Y 显示"无内容可复制"
            self._last_reply_text = "（所有专家调用失败，请检查网络或 API Key 后重试）"
            self._is_generating = False
            return

        # ── 专家去重：基于 Jaccard 相似度过滤高度相似的回答 ──
        # 场景：项目经理选了 coder + reasoner，但两者回答高度相似，汇总时去重以减少 token
        if HYBRID_DEDUP_SIMILARITY_THRESHOLD > 0 and len(expert_responses) > 1:
            unique_responses = [expert_responses[0]]
            dedup_skipped = []
            for resp in expert_responses[1:]:
                is_dup = False
                for kept in unique_responses:
                    sim = _jaccard_similarity(resp["content"], kept["content"])
                    if sim >= HYBRID_DEDUP_SIMILARITY_THRESHOLD:
                        is_dup = True
                        dedup_skipped.append((resp["expert"], kept["expert"], round(sim, 2)))
                        break
                if not is_dup:
                    unique_responses.append(resp)
            if len(dedup_skipped) > 0:
                # 显示去重提示
                dedup_msg = "  ℹ 去重："
                dedup_parts = [f"{a}≈{b}({s})" for a, b, s in dedup_skipped]
                dedup_msg += "，".join(dedup_parts) + " 已合并\n"
                self._add_static(Text(dedup_msg, style=C_DIM))
            expert_responses = unique_responses

        # ── 第3步：项目经理GLM汇总 ──
        if self._stop_generation or len(expert_responses) <= 1:
            # 单专家或被停止，直接用第一个专家的结果
            if expert_responses:
                self._last_reply_text = expert_responses[0]["content"]
            self.messages.append({"role": "assistant", "content": expert_responses[0]["content"] if expert_responses else ""})
            self._is_generating = False
            return

        block_sum = self._add_block("汇总·GLM-4.7", C_GREEN)
        block_sum.update(Text.assemble(
            ("  ┌─ 项目经理·GLM-4.7 汇总\n", f"bold {C_GREEN}"),
            ("  │ 汇总中…\n", C_DIM),
        ))

        summary_prompt = "以下是多位专家的回答，请综合整理为一份完整、连贯的回复：\n\n"
        for resp in expert_responses:
            summary_prompt += f"【{resp['expert']}】\n{resp['content'][:2000]}\n\n"
        summary_prompt += "\n请综合以上内容，给出最终回复。"

        try:
            if self.stream_enabled:
                sum_stream = await glm_client.chat.completions.create(
                    model=glm_cfg["model"],
                    messages=[{"role": "system", "content": TOOL_CAPABILITY_PROMPT + "\n\n你是 ZeroAI 项目经理，负责将多位专家的回答综合整理为完整、连贯的最终回复。保留关键信息，消除重复，按用户问题逻辑组织。"},
                              {"role": "user", "content": summary_prompt}],
                    temperature=self.temperature,
                    stream=True,
                )
                sum_content = ""
                sum_reasoning = ""
                async for chunk in sum_stream:
                    if self._stop_generation:
                        break
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if rc:
                        sum_reasoning += rc
                        # 合并 <think> 标签内容
                        think_in, body = _parse_think_tags(sum_content)
                        combined = (sum_reasoning + ("\n" + think_in if think_in else "")).strip()
                        if combined:
                            self._update_streaming_with_reasoning(block_sum, body, combined)
                        else:
                            self._update_streaming(block_sum, body)
                        await asyncio.sleep(0)
                    if delta.content:
                        sum_content += delta.content
                        # 过滤模型内部特殊标签（<|observation|> <|system|> 等）
                        sum_content = _strip_model_tokens(sum_content)
                        # 解析 <think> 标签，分离思考与正文
                        think_in, body = _parse_think_tags(sum_content)
                        combined = (sum_reasoning + ("\n" + think_in if think_in else "")).strip()
                        if combined:
                            self._update_streaming_with_reasoning(block_sum, body, combined)
                        else:
                            self._update_streaming(block_sum, body)
                        # 复制修复：流式汇总过程中实时更新 _last_reply_text，确保中途停止也能复制
                        if body.strip():
                            self._last_reply_text = body
                        await asyncio.sleep(0)
            else:
                # stream=False：用可中断 await 包装
                sum_resp = await _interruptible_await(glm_client.chat.completions.create(
                    model=glm_cfg["model"],
                    messages=[{"role": "system", "content": TOOL_CAPABILITY_PROMPT + "\n\n你是 ZeroAI 项目经理，负责将多位专家的回答综合整理为完整、连贯的最终回复。保留关键信息，消除重复，按用户问题逻辑组织。"},
                              {"role": "user", "content": summary_prompt}],
                    temperature=self.temperature,
                    stream=False,
                ))
                if sum_resp is None or self._stop_generation:
                    # 复制修复：汇总被停止时，保存已有内容（流式模式下 _last_reply_text 已实时更新）
                    if not self._last_reply_text.strip() and expert_responses:
                        self._last_reply_text = expert_responses[0]["content"]
                    self._is_generating = False
                    self._add_static(Text("  ⏹ 已停止\n", style=C_DIM))
                    return
                sum_content = _strip_model_tokens(sum_resp.choices[0].message.content or "")
                sum_reasoning = getattr(sum_resp.choices[0].message, "reasoning_content", "") or ""
            # 最终更新：分离 think 和 body，合并所有思考内容
            think_in, body = _parse_think_tags(sum_content)
            combined_reasoning = (sum_reasoning + ("\n" + think_in if think_in else "")).strip()
            if combined_reasoning:
                self._update_streaming_with_reasoning(block_sum, body, combined_reasoning, final=True)
            else:
                self._update_streaming(block_sum, body, final=True)
            # 后续使用 body（已去除 <think> 标签）
            sum_content = body
            # 身份泄露过滤（混合模式汇总回复）
            sum_content, _id_leaked = _sanitize_identity_leak(sum_content)
            if _id_leaked:
                self._add_static(Text("  └─ 🛡️ 身份保护：已过滤底层模型信息", style=f"bold {C_YELLOW}"))
            if sum_content.strip():
                self._last_reply_text = sum_content
                self.messages.append({"role": "assistant", "content": sum_content})
        except Exception as e:
            # 汇总失败，用第一个专家的结果
            fallback = expert_responses[0]["content"]
            # 身份泄露过滤（混合模式降级回复）
            fallback, _id_leaked = _sanitize_identity_leak(fallback)
            if _id_leaked:
                self._add_static(Text("  └─ 🛡️ 身份保护：已过滤底层模型信息", style=f"bold {C_YELLOW}"))
            self._add_static(_safe_markdown(render_latex_in_text(fallback), code_theme="monokai"))
            self.messages.append({"role": "assistant", "content": fallback})
            self._last_reply_text = fallback

        self._add_static(Text("  └─", style=C_DIM))
        self._is_generating = False
    async def _run_react_turn(self):
        """ReAct Agent 模式（增强版）：观察→思考→行动 循环

        阶段 B 深度升级：
        - 使用 AdvancedAgentLoop（含思维链、反思、并行、摘要）
        - 思维链实时可视化（on_thought_chain 回调）
        - 自动注入 MCP 工具到 Agent 的 tool_map
        - RAG 自动检索项目上下文（无需 /索引 也可调用）
        - Plan-and-Execute 模式可选（/智能体 plan 切换）
        """
        self._is_generating = True
        self._stop_generation = False
        _set_stop_flag(False)

        try:
            from zeroai.core.agent import (
                AdvancedAgentLoop, ReActPlanner,
                PlanAndExecutePlanner, ReflexionEngine, ToolResultSummarizer,
                Thought,
            )
            from zeroai.memory import get_retriever, get_conversation_memory
            from zeroai.tools.registry import TOOLS, TOOL_MAP

            # 提取最后一条用户消息
            user_input = ""
            for msg in reversed(self.messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        user_input = " ".join(
                            p.get("text", "") for p in content
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    else:
                        user_input = str(content)
                    break

            if not user_input:
                self._add_static(Text("  └─ 无用户输入\n", style=C_DIM))
                return

            # API Key 检查
            cur_key = MODEL_CONFIGS.get(self.model_key, {}).get("api_key", "")
            if not cur_key:
                self._add_static(Text.assemble(
                    (f"  {_load_svg_icon('warning')} 未配置 API 密钥\n", f"bold {C_FG}"),
                    (f"  ReAct Agent 需要调用 LLM 规划器，请先配置 API Key\n", C_DIM),
                ))
                return

            # 获取 RAG 检索器（如果已索引）
            retriever = None
            try:
                r = get_retriever()
                if r.get_stats()["total_chunks"] > 0:
                    retriever = r
            except Exception:
                pass

            # B.3: RAG 自动检索 - 即使无索引也尝试对话记忆检索
            rag_context = ""
            if retriever:
                try:
                    docs = retriever.retrieve(user_input, top_k=3)
                    if docs:
                        rag_context = "\n\n[项目上下文]\n" + "\n---\n".join(docs[:3])
                except Exception:
                    pass

            # 对话记忆检索（跨会话）
            try:
                conv_mem = get_conversation_memory()
                history = conv_mem.recall(user_input, top_k=2)
                if history:
                    rag_context += "\n\n[历史对话]\n" + "\n---\n".join(
                        h.get("content", "")[:500] for h in history
                    )
            except Exception:
                pass

            # 注入 RAG 上下文到 user_input
            enhanced_input = user_input + rag_context if rag_context else user_input

            # 收集工具集（内置 + MCP）
            tools_schema = list(TOOLS)
            tool_map = dict(TOOL_MAP)

            # 尝试合并 MCP 工具
            mcp_tools_count = 0
            try:
                from zeroai.mcp import get_mcp_registry
                registry = get_mcp_registry()
                if registry.is_initialized:
                    mcp_tools = registry.get_tools_schema()
                    mcp_funcs = registry.get_tool_functions()
                    tools_schema.extend(mcp_tools)
                    tool_map.update(mcp_funcs)
                    mcp_tools_count = len(mcp_tools)
            except Exception:
                pass

            # 创建增强版 AgentLoop
            planner = ReActPlanner(model_key=self.model_key)
            loop = AdvancedAgentLoop(
                planner=planner,
                tool_map=tool_map,
                tools_schema=tools_schema,
                max_steps=self.max_turns,
                retriever=retriever,
                enable_plan=getattr(self, "_react_plan_mode", False),
                enable_reflexion=True,    # 启用反思
                enable_parallel=True,     # 启用并行
                enable_summarize=True,    # 启用摘要
            )

            # 思维链可视化 block（实时更新）
            chain_block = None

            # 注册回调：更新 TUI 显示
            async def _on_thought(text):
                nonlocal chain_block
                if chain_block is None:
                    chain_block = self._add_block("思考", C_CYAN)
                chain_block.update(Text.assemble(
                    (f"  {_load_svg_icon('search')} {text}\n", f"bold {C_CYAN}"),
                ))

            async def _on_thought_chain(thought: Thought):
                """思维链实时回调：每步推理过程流式展示"""
                step = thought.step
                if thought.action_type == "reflect":
                    # 反思：黄色警告
                    self._add_static(Text.assemble(
                        (f"  │ [反思 {step}] ", C_DIM),
                        (f"工具 {thought.tool_name} 失败\n", f"bold {C_YELLOW}"),
                        (f"  │   原因：{thought.reflection or '未知'}\n", C_DIM),
                    ))
                elif thought.action_type == "parallel_tool_calls":
                    # 并行调用
                    self._add_static(Text.assemble(
                        (f"  │ [并行 {step}] ", C_DIM),
                        (f"同时执行多个工具\n", f"bold {C_PURPLE}"),
                    ))
                elif thought.action_type == "plan":
                    self._add_static(Text.assemble(
                        (f"  │ [规划 {step}] ", C_DIM),
                        (f"{thought.thought[:200]}\n", f"bold {C_BLUE}"),
                    ))

            async def _on_tool_call(name, args):
                import json as _json
                # 区分 MCP 工具
                is_mcp = name.startswith("mcp__")
                tag = "MCP" if is_mcp else "工具"
                color = C_PURPLE if is_mcp else C_FG
                tool_info = f"**调用{tag}** `{name}`\n\n```json\n{_json.dumps(args, ensure_ascii=False, indent=2)}\n```"
                self._add_static(_safe_markdown(tool_info, code_theme="monokai"))

            async def _on_tool_result(name, result):
                result_md = f"**结果**\n\n{result[:2000]}"
                self._add_static(_safe_markdown(result_md, code_theme="monokai"))
                self._add_static(Text("  └─", style=C_DIM))

            async def _on_final_answer(answer):
                if answer.strip():
                    self._last_reply_text = answer
                    self.messages.append({"role": "assistant", "content": answer})
                    self._add_static(_safe_markdown(answer, code_theme="monokai"))

            async def _on_error(text):
                self._add_static(Text.assemble(
                    (f"  └─ 错误：{text}\n", f"bold {C_YELLOW}"),
                ))

            loop.on_thought = _on_thought
            loop.on_thought_chain = _on_thought_chain
            loop.on_tool_call = _on_tool_call
            loop.on_tool_result = _on_tool_result
            loop.on_final_answer = _on_final_answer
            loop.on_error = _on_error
            loop.is_stopped = lambda: self._stop_generation

            # 标记开始
            block_start = self._add_block("ReAct Agent", C_PURPLE)
            start_parts = [
                (f"  ⏵ ReAct Agent 启动（增强版）\n", f"bold {C_PURPLE}"),
                (f"  │ 观察→思考→行动循环（最多 {self.max_turns} 步）\n", C_DIM),
                (f"  │ RAG 检索：{'已启用' if retriever else '未启用'}\n", C_DIM),
            ]
            if mcp_tools_count > 0:
                start_parts.append((f"  │ MCP 工具：{mcp_tools_count} 个\n", C_DIM))
            start_parts.extend([
                (f"  │ 增强：反思+并行+摘要\n", C_DIM),
                (f"  │ 规划模式：{'Plan-and-Execute' if getattr(self, '_react_plan_mode', False) else 'ReAct'}\n", C_DIM),
            ])
            block_start.update(Text.assemble(*start_parts))

            # 运行 Agent 循环（增强版，返回思维链）
            final_answer, steps, chain = await loop.run_with_chain(
                user_input=enhanced_input,
                messages=self.messages,
            )

            # 显示步数统计
            tool_count = sum(1 for s in steps if s.get("action_type") == "tool_call")
            reflect_count = sum(1 for t in chain if t.action_type == "reflect")

            stats_parts = [
                ("  └─ ", C_DIM),
                (f"ReAct 完成：{len(steps)} 步 / {tool_count} 次工具调用", C_DIM),
            ]
            if reflect_count > 0:
                stats_parts.append((f" / {reflect_count} 次反思", C_DIM))
            if mcp_tools_count > 0:
                stats_parts.append((f" / MCP:{mcp_tools_count}", C_DIM))
            stats_parts.append(("\n", C_DIM))
            self._add_static(Text.assemble(*stats_parts))

            # B.3: 保存对话到向量记忆
            try:
                conv_mem = get_conversation_memory()
                await conv_mem.add_turn(
                    user_input=user_input,
                    assistant_response=final_answer,
                    metadata={
                        "mode": "react_enhanced",
                        "steps": len(steps),
                        "tools": tool_count,
                    },
                )
            except Exception:
                pass

        except Exception as e:
            self._add_static(Text.assemble(
                ("  └─ ReAct Agent 错误：", C_DIM),
                (f"{e}\n", f"bold {C_YELLOW}"),
            ))
        finally:
            self._is_generating = False
