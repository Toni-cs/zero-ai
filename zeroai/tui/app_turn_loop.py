"""单专家主循环：_run_turn 包装 + _run_turn_impl 实际实现（流式、工具调用、模式降级恢复）（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import asyncio
import json
import re
import time
from openai import AsyncOpenAI
from rich.text import Text
from zeroai.core.constants import CLEANUP_THRESHOLD_RATIO, COMPRESS_THRESHOLD_RATIO, EXPERT_TEAM, MODEL_CONFIGS
from zeroai.core.context_compress import _estimate_tokens, _filter_messages_for_model, cleanup_and_compress
from zeroai.core.expert_route import _OPENROUTER_FAIL_COUNTS, _check_openrouter_circuit_breaker, _record_openrouter_failure, _record_openrouter_success, get_expert_config, route_expert, route_expert_glm
from zeroai.core.model_manager import get_model_display_name
from zeroai.core.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_CORE
from zeroai.core.response_utils import _parse_think_tags, _sanitize_identity_leak, _strip_model_tokens
from zeroai.core.runtime import _interruptible_sleep, _set_stop_flag
from zeroai.core.secrets import _is_proxy_enabled, _make_openai_client
from zeroai.core.tool_call_parser import needs_tool_calls as _needs_tool_calls, parse_tool_call_xml as _parse_tool_call_xml
from zeroai.tools.registry import TOOL_MAP, TOOLS, invoke_tool
from zeroai.tools.render import _safe_markdown
from zeroai.tui.colors import C_DIM, C_FG, C_GREEN, C_RED, C_YELLOW
from zeroai.tui.icons import _load_svg_icon


class TurnLoopMixin:
    """单专家主循环：_run_turn 包装 + _run_turn_impl 实际实现（流式、工具调用、模式降级恢复）"""

    async def _run_turn(self):
        """执行 Agent 循环 - 流式 + Markdown 渲染（实时更新）"""
        # ── ReAct Agent 模式：走 观察-思考-行动 循环 ──
        if self.react_enabled:
            await self._run_react_turn()
            return

        # ── 安全策略：hybrid 模式下若请求需要调用工具，强制降级到 expert 模式 ──
        # hybrid 子代理目前不传递 tools 参数，无法执行真正的 function_calling；
        # 检测到工具类请求时自动切换到 expert 模式，避免模型输出 <tool_call> 伪 XML。
        _original_work_mode = None
        if self.work_mode == "hybrid":
            last_user_text = ""
            for msg in reversed(self.messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        last_user_text = " ".join(
                            p.get("text", "") for p in content
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    else:
                        last_user_text = str(content)
                    break
            if _needs_tool_calls(last_user_text):
                _original_work_mode = self.work_mode
                self.work_mode = "expert"
                self._add_static(Text.assemble(
                    ("  ", C_DIM),
                    ("[!] 当前请求需要调用系统工具，已自动切换到专家模式执行\n", f"bold {C_YELLOW}"),
                ))
        try:
            await self._run_turn_impl()
        finally:
            if _original_work_mode is not None:
                self.work_mode = _original_work_mode
    async def _run_turn_impl(self):
        """_run_turn 的实际实现（被 _run_turn 包装以处理模式降级恢复）"""
        self._is_generating = True
        self._stop_generation = False
        # 重置全局停止标志
        _set_stop_flag(False)

        # ── API Key 检查：未配置时直接提示，不发起请求 ──
        cur_key = MODEL_CONFIGS.get(self.model_key, {}).get("api_key", "")
        if not cur_key:
            self._add_static(Text.assemble(
                (f"  {_load_svg_icon('warning')} 未配置 API 密钥\n", f"bold {C_FG}"),
                (f"  当前模型：{get_model_display_name(self.model_key)}\n", C_DIM),
                (f"  请按 Ctrl+P 打开设置面板配置 API Key\n", f"bold {C_FG}"),
                (f"  智谱GLM 免费 Key 获取：https://open.bigmodel.cn/\n", C_DIM),
                (f"  或输入 /模型 ollama 使用本地模型（无需 Key）\n", C_DIM),
            ))
            self._is_generating = False
            return

        # ── 上下文自动压缩：两层防护（30% 清理 + 70% GLM 压缩），防止上下文爆炸 ──
        try:
            est_tokens_pre = _estimate_tokens(self.messages)
            cleanup_threshold = int(self.context_limit * CLEANUP_THRESHOLD_RATIO)
            compress_threshold = int(self.context_limit * COMPRESS_THRESHOLD_RATIO)

            def _ctx_log(text, style=None):
                self._add_static(Text.assemble(
                    (text, style if style else C_DIM),
                ))

            if est_tokens_pre > cleanup_threshold and len(self.messages) > 8:
                block_ctx = self._add_block("上下文管理", C_DIM)
                block_ctx.update(Text.assemble(
                    (f"  {_load_svg_icon('tool')} 上下文管理\n", f"bold {C_DIM}"),
                    (f"  │ 当前约 {est_tokens_pre} tokens（清理阈值 {cleanup_threshold}，压缩阈值 {compress_threshold}）\n", C_DIM),
                ))
                await cleanup_and_compress(self, _ctx_log)
                # 清理后重置精确输入 token，让右侧统计重新估算
                self._precise_input_tokens = 0
            else:
                # 未触发清理阈值，但仍检查是否需要 GLM 压缩
                await cleanup_and_compress(self, _ctx_log if est_tokens_pre > compress_threshold else None)
                self._precise_input_tokens = 0
        except Exception as e:
            # 压缩失败不阻塞对话
            self._add_static(Text(f"  {_load_svg_icon('warning')} 上下文管理跳过：{str(e)[:80]}\n", style=C_DIM))

        _loop_expert_key = None
        _tool_call_count = 0
        _MAX_TOOL_CALLS_PER_TURN = 15  # 单轮最多工具调用次数（提高以支持多步搜索+执行任务）
        while True:
            try:
                # ── 根据工作模式决定使用的模型 ──
                if self.work_mode == "expert":
                    # 专家模式：根据最后一条用户消息路由（仅首次/新用户消息时路由）
                    last_user = ""
                    last_user_has_image = False
                    is_new_user_msg = False
                    if not self.messages or self.messages[-1].get("role") == "user":
                        is_new_user_msg = True
                    for msg in reversed(self.messages):
                        if msg["role"] == "user":
                            content = msg["content"]
                            if isinstance(content, list):
                                text_parts = []
                                for part in content:
                                    if isinstance(part, dict):
                                        if part.get("type") == "text":
                                            text_parts.append(part.get("text", ""))
                                        elif part.get("type") == "image_url":
                                            last_user_has_image = True
                                last_user = " ".join(text_parts)
                            else:
                                last_user = content
                            break
                    if last_user_has_image:
                        expert_key = "vision"
                        _loop_expert_key = expert_key
                        block = self._add_block("路由分析", C_DIM)
                        block.update(Text.assemble(
                            (f"  {_load_svg_icon('image')} 检测到图片，自动路由到视觉专家\n", C_DIM),
                        ))
                    elif _loop_expert_key is None or is_new_user_msg:
                        _long_text_patterns = ["5000字", "3000字", "2000字", "万字", "长文",
                                               "综述类", "综述文章", "毕业论文", "学位论文",
                                               "长篇", "完整论文", "写一篇"]
                        _is_long_text = any(p in last_user for p in _long_text_patterns)
                        if _is_long_text and self.work_mode == "expert":
                            self._add_static(Text.assemble(
                                ("  ", C_DIM),
                                ("[!] 检测到长文写作任务，建议按 Ctrl+M 切换到混合思考模式（Hy）\n", f"bold {C_FG}"),
                                ("      混合模式可调度多位专家协作，生成更完整的长文内容\n", C_DIM),
                            ))
                        if len(last_user) >= 10:
                            # 混合路由：先跑关键词匹配，命中就不显示"GLM分析"
                            _kw_pre = route_expert(last_user)
                            if _kw_pre != "knowledge":
                                # 关键词命中，零延迟路由
                                block = self._add_block("路由分析", C_DIM)
                                block.update(Text.assemble(
                                    (f"  {_load_svg_icon('search')} 路由 → {EXPERT_TEAM[_kw_pre]['label']}\n", C_DIM),
                                ))
                                expert_key = _kw_pre
                            else:
                                # 关键词未命中，走 GLM 语义路由
                                block = self._add_block("路由分析", C_DIM)
                                block.update(Text.assemble(
                                    (f"  {_load_svg_icon('search')} GLM 正在分析问题类型…\n", C_DIM),
                                ))
                                expert_key = await route_expert_glm(last_user)
                        else:
                            expert_key = route_expert(last_user)
                        _loop_expert_key = expert_key
                    else:
                        expert_key = _loop_expert_key
                    # OpenRouter 熔断检查：连续失败≥3次则直接降级到 GLM，避免卡在"思考中…"
                    if _check_openrouter_circuit_breaker(expert_key):
                        _fail_cnt = _OPENROUTER_FAIL_COUNTS.get(expert_key, 0)
                        _orig_label = EXPERT_TEAM[expert_key]["label"]
                        self._add_static(Text.assemble(
                            ("  ", C_DIM),
                            (f"{_orig_label}", f"bold {C_FG}"),
                            (f" 连续失败 {_fail_cnt} 次，已熔断，自动降级到 GLM…\n", C_DIM),
                        ))
                        expert_key = "knowledge"  # 降级到通用知识专家（GLM-4.7-Flash）
                    ecfg = get_expert_config(expert_key)
                    expert_label = EXPERT_TEAM[expert_key]["label"]
                    self._current_expert_label = expert_label
                    self._current_expert_key = expert_key
                    block = self._add_block(f"构建 · {expert_label}", C_RED)
                    block.update(Text.assemble(
                        (f"  ⏵ 构建 · {expert_label}\n", f"bold {C_RED}"),
                        ("  │ 思考中…\n", C_DIM),
                    ))
                    async_client = _make_openai_client(ecfg.get("model_key", "glm"))
                    if not _is_proxy_enabled():
                        async_client = AsyncOpenAI(
                            base_url=ecfg["base_url"], api_key=ecfg["api_key"],
                            timeout=120.0, max_retries=0,  # 禁用 SDK 内置重试，由下方自定义重试处理
                        )
                    _ctx_len = sum(len(str(m.get("content", ""))) for m in self.messages)
                    # 动态超时：上下文越长，超时越长。最小90秒，最大600秒（10分钟）
                    _dyn_timeout = min(600, max(90, 90 + _ctx_len // 500))
                    # ── 注入专家 system prompt（专家角色 + 核心能力，确保工具调用和联网搜索能力不丢失）──
                    # 视觉模型（glm-v）上下文仅 16K，用精简版核心 prompt 避免超限
                    # 文本模型（glm/glm-4）上下文 128K，用完整版 prompt
                    _expert_role = EXPERT_TEAM[expert_key].get("system_prompt", "")
                    _model_key = ecfg.get("model_key", "glm")
                    _base_prompt = SYSTEM_PROMPT_CORE if _model_key == "glm-v" else SYSTEM_PROMPT
                    expert_system = f"{_expert_role}\n\n{_base_prompt}" if _expert_role else _base_prompt
                    _raw_filtered = _filter_messages_for_model(self.messages, ecfg["model"])
                    _expert_messages = []
                    _system_injected = False
                    for _m in _raw_filtered:
                        if _m.get("role") == "system" and not _system_injected:
                            _expert_messages.append({"role": "system", "content": expert_system})
                            _system_injected = True
                        else:
                            _expert_messages.append(_m)
                    if not _system_injected:
                        _expert_messages.insert(0, {"role": "system", "content": expert_system})
                    api_params = {
                        "model": ecfg["model"],
                        "messages": _expert_messages,
                        "tools": TOOLS,
                        "temperature": self.temperature,
                        "stream": self.stream_enabled,
                        "timeout": _dyn_timeout,
                    }
                    # 流式时请求 usage 数据（精确 token 统计）
                    if self.stream_enabled:
                        api_params["stream_options"] = {"include_usage": True}
                    # 专家降级标记：如果专家模型失败，降级到GLM
                    _fallback_to_glm = False
                elif self.work_mode == "hybrid":
                    # 混合思考：多专家协作
                    await self._run_hybrid_turn()
                    return
                else:
                    # 手动模式：使用用户选定的模型
                    manual_label = MODEL_CONFIGS.get(self.model_key, {}).get("label", self.model_key)
                    self._current_expert_label = manual_label
                    self._current_expert_key = "manual"
                    block = self._add_block(f"构建 · {manual_label}", C_RED)
                    block.update(Text.assemble(
                        (f"  ⏵ 构建 · {manual_label}\n", f"bold {C_RED}"),
                        ("  │ 思考中…\n", C_DIM),
                    ))
                    cfg = MODEL_CONFIGS[self.model_key]
                    async_client = _make_openai_client(self.model_key)
                    if not _is_proxy_enabled():
                        async_client = AsyncOpenAI(
                            base_url=cfg["base_url"], api_key=cfg["api_key"],
                            timeout=120.0, max_retries=0,  # 禁用 SDK 内置重试，由下方自定义重试处理
                        )
                    _ctx_len = sum(len(str(m.get("content", ""))) for m in self.messages)
                    _dyn_timeout = min(300, max(60, 60 + _ctx_len // 1000))
                    api_params = {
                        "model": cfg["model"],
                        "messages": _filter_messages_for_model(self.messages, cfg["model"]),
                        "tools": TOOLS,
                        "temperature": self.temperature,
                        "stream": self.stream_enabled,
                        "timeout": _dyn_timeout,
                    }
                    # 流式时请求 usage 数据（精确 token 统计）
                    if self.stream_enabled:
                        api_params["stream_options"] = {"include_usage": True}

                # 带重试的 API 调用（处理 429 速率限制 + ReadTimeout 超时 + 降级到GLM）
                stream = None
                max_retries = 5  # 从4增至5，超时多一次机会
                for attempt in range(max_retries):
                    try:
                        stream = await async_client.chat.completions.create(**api_params)
                        break
                    except Exception as retry_err:
                        err_str = str(retry_err)
                        err_type = type(retry_err).__name__
                        is_timeout = ("timeout" in err_str.lower() or "timed out" in err_str.lower()
                                      or "ReadTimeout" in err_type or "APITimeoutError" in err_type)
                        is_rate_limit = "429" in err_str or "rate" in err_str.lower()
                        is_server_error = any(code in err_str for code in ("500", "502", "503", "504")) or "ServerError" in err_type
                        # 连接错误（DNS/TCP/SSL 失败，通常是网络问题，应重试）
                        is_connection_error = ("APIConnectionError" in err_type
                                               or "ConnectionError" in err_type
                                               or "connection" in err_str.lower()
                                               or "ssl" in err_str.lower()
                                               or "eof" in err_str.lower())

                        # 专家模式下：专家模型失败时智能降级（首次失败即降级）
                        if self.work_mode == "expert" and not _fallback_to_glm and expert_key != "pm":
                            _fallback_to_glm = True
                            # 记录 OpenRouter 专家失败（用于熔断器累计，连续3次后直接跳过）
                            _fail_n = _record_openrouter_failure(expert_key)
                            # 智能选择降级模型：视觉专家降级到GLM-4V，文本专家降级到 GLM-4（不同限流池，避免同模型限流）
                            if expert_key == "vision":
                                _fallback_key = "glm-v"
                                _fallback_label = "GLM-4V"
                            else:
                                _fallback_key = "glm-4"
                                _fallback_label = "GLM-4"
                            self._add_static(Text.assemble(
                                ("  ", C_DIM),
                                (f"{expert_label}", f"bold {C_FG}"),
                                (f" 不可用（{err_type}），降级到 {_fallback_label}…\n", C_DIM),
                            ))
                            fb_cfg = MODEL_CONFIGS[_fallback_key]
                            expert_label = f"{_fallback_label}（降级）"
                            block = self._add_block(f"助手 [{expert_label}]", C_GREEN)
                            block.update(Text.assemble(
                                (f"  ┌─ 助手 [{expert_label}]\n", f"bold {C_GREEN}"),
                                ("  │ 思考中…\n", C_DIM),
                            ))
                            async_client = _make_openai_client(_fallback_key)
                            if not _is_proxy_enabled():
                                async_client = AsyncOpenAI(
                                    base_url=fb_cfg["base_url"], api_key=fb_cfg["api_key"],
                                    timeout=180.0, max_retries=0,  # 降级时用更长超时
                                )
                            # 降级时仍保留专家 system prompt，避免模型丢失角色说英文
                            # 如果降级目标模型与原专家模型类型不同（如文本→视觉），需重新选择 prompt
                            _fb_model_key = _fallback_key
                            _fb_base_prompt = SYSTEM_PROMPT_CORE if _fb_model_key == "glm-v" else SYSTEM_PROMPT
                            _fb_expert_system = f"{_expert_role}\n\n{_fb_base_prompt}" if _expert_role else _fb_base_prompt
                            _fallback_messages = _filter_messages_for_model(_expert_messages, fb_cfg["model"])
                            # 替换 system 消息为降级后的 prompt
                            _fallback_messages = [
                                {"role": "system", "content": _fb_expert_system} if m.get("role") == "system" else m
                                for m in _fallback_messages
                            ]
                            if not any(m.get("role") == "system" for m in _fallback_messages):
                                _fallback_messages.insert(0, {"role": "system", "content": _fb_expert_system})
                            api_params = {
                                "model": fb_cfg["model"],
                                "messages": _fallback_messages,
                                "tools": TOOLS,
                                "temperature": self.temperature,
                                "stream": self.stream_enabled,
                                "timeout": _dyn_timeout,
                            }
                            if self.stream_enabled:
                                api_params["stream_options"] = {"include_usage": True}
                            continue

                        # 超时/服务器错误/连接错误：快速退避重试（限流且已降级则不重试，同限流池重试无效）
                        _can_retry = (is_timeout or is_server_error or is_connection_error) and attempt < max_retries - 1
                        # 限流且未降级时才重试（给原模型一次机会）；已降级还限流则直接抛错
                        if is_rate_limit and not _fallback_to_glm and attempt < max_retries - 1:
                            _can_retry = True
                        if _can_retry:
                            # 快速退避：1s, 2s, 3s, 4s（比 2/4/8 更快恢复）
                            wait = attempt + 1
                            if is_connection_error:
                                reason = "网络连接"
                            elif is_timeout:
                                reason = "超时"
                            elif is_rate_limit:
                                reason = "限流"
                            else:
                                reason = "服务器错误"
                            block.update(Text.assemble(
                                (f"  ⏵ 构建 · {expert_label}", f"bold {C_RED}"),
                                ("\n  │ ", C_DIM),
                                (f"⏳ {reason}，{wait}秒后重试", f"bold {C_YELLOW}"),
                                (f"（第{attempt+1}/{max_retries}次）\n", C_DIM),
                            ))
                            await _interruptible_sleep(wait)
                            if self._stop_generation:
                                break
                            # 超时重试时增加下次的超时时间（+60秒，更快达到宽容超时）
                            if is_timeout:
                                api_params["timeout"] = min(600, api_params["timeout"] + 60)
                            continue
                        # 限流且已降级：提示用户稍后再试
                        if is_rate_limit and _fallback_to_glm:
                            self._add_static(Text.assemble(
                                ("  └─ ", C_DIM),
                                (f"限流持续，请稍后再试或切换模型\n", f"bold {C_YELLOW}"),
                            ))
                        # 连接错误且已用尽重试：提示检查网络
                        if is_connection_error:
                            self._add_static(Text.assemble(
                                ("  └─ ", C_DIM),
                                (f"网络连接失败，请检查网络后重试\n", f"bold {C_YELLOW}"),
                            ))
                        raise retry_err

                if stream is None:
                    raise Exception("API 调用失败")

                # OpenRouter 专家本次调用成功（未降级），重置连续失败计数
                if self.work_mode == "expert" and not _fallback_to_glm:
                    _record_openrouter_success(expert_key)

                full_content = ""
                reasoning_content = ""
                tool_calls_buf = {}
                update_counter = 0
                _loop_detect_buf = []
                _loop_detect_window = 10
                _loop_detect_threshold = 7
                _loop_detected = False
                # 内容层循环检测（防止模型在正文重复输出无意义片段，如"代码执行前必须问"）
                _content_loop_buf = []
                _content_loop_window = 15
                _content_loop_threshold = 10
                # Token 统计：开始计时
                self.stream_start_time = time.time()
                self.stream_token_count = 0
                _api_usage = None  # 精确 usage（从 API 响应获取）
                async for chunk in stream:
                    # 检查是否被用户 Ctrl+C 停止
                    if self._stop_generation:
                        break
                    # 捕获 usage 数据（stream_options include_usage 时最后一个 chunk 带 usage）
                    if hasattr(chunk, "usage") and chunk.usage:
                        _api_usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # 捕获思考内容（原生 reasoning_content，推理模型才有）
                    rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if rc:
                        reasoning_content += rc
                        self.stream_token_count += 1
                        self.total_tokens += 1
                        _loop_detect_buf.append(rc.strip())
                        if len(_loop_detect_buf) > _loop_detect_window:
                            _loop_detect_buf.pop(0)
                        if len(_loop_detect_buf) >= _loop_detect_window:
                            recent = _loop_detect_buf[-_loop_detect_threshold:]
                            if len(set(recent)) <= 1 and recent[0] and len(recent[0]) > 5:
                                _loop_detected = True
                                reasoning_content += "\n[系统自动截断：检测到循环思考]"
                                self._add_static(Text("  └─ ⚠️ 检测到循环思考，已自动截断", style="bold yellow"))
                                break
                        # 实时显示思考过程（合并原生 reasoning 和 <arg_key> 标签内容）
                        think_in_content, body_content = _parse_think_tags(full_content)
                        combined_reasoning = (reasoning_content + ("\n" + think_in_content if think_in_content else "")).strip()
                        if combined_reasoning:
                            self._update_streaming_with_reasoning(block, body_content, combined_reasoning)
                        else:
                            self._update_streaming(block, body_content)
                        await asyncio.sleep(0)

                    if delta.content:
                        full_content += delta.content
                        # 内容层循环检测：采样检测（每 5 个 chunk 检测一次，避免拖慢流式输出）
                        content_chunk = delta.content.strip()
                        if content_chunk:
                            _content_loop_buf.append(content_chunk)
                            if len(_content_loop_buf) > _content_loop_window:
                                _content_loop_buf.pop(0)
                            if update_counter % 5 == 0 and len(_content_loop_buf) >= _content_loop_threshold:
                                recent = _content_loop_buf[-_content_loop_threshold:]
                                # 严格判断：连续 N 个 chunk 完全相同且非空白、长度≥8字符才算循环
                                if len(set(recent)) <= 1 and len(recent[0]) >= 8 and recent[0].strip():
                                    _loop_detected = True
                                    full_content += "\n[系统自动截断：检测到重复输出]"
                                    self._add_static(Text("  └─ ⚠️ 检测到模型重复输出，已自动截断", style="bold yellow"))
                                    break
                            # 标记链检测（轻量，仅 count 操作）
                            if update_counter % 5 == 0:
                                if "<回答>" in content_chunk and content_chunk.count("<回答>") >= 4:
                                    _loop_detected = True
                                    full_content += "\n[系统自动截断：检测到重复标记链]"
                                    self._add_static(Text("  └─ ⚠️ 检测到重复标记链，已自动截断", style="bold yellow"))
                                    break
                                if "用户回应" in content_chunk and content_chunk.count("用户回应") >= 4:
                                    _loop_detected = True
                                    full_content += "\n[系统自动截断：检测到重复标记链]"
                                    self._add_static(Text("  └─ ⚠️ 检测到重复标记链，已自动截断", style="bold yellow"))
                                    break
                        update_counter += 1
                        # 统计 token（每个 chunk 约 1 token）
                        self.stream_token_count += 1
                        self.total_tokens += 1
                        # 每 3 个 chunk 更新一次（避免过于频繁）
                        if update_counter % 3 == 0:
                            # 解析 <think>...</think> 标签内容，合并到思考过程
                            think_in_content, body_content = _parse_think_tags(full_content)
                            combined_reasoning = (reasoning_content + ("\n" + think_in_content if think_in_content else "")).strip()
                            if combined_reasoning:
                                self._update_streaming_with_reasoning(block, body_content, combined_reasoning)
                            else:
                                self._update_streaming(block, body_content)
                            self._update_token_bar()
                        await asyncio.sleep(0)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_buf:
                                tool_calls_buf[idx] = {"id": "", "name": "", "args": ""}
                            if tc.id:
                                tool_calls_buf[idx]["id"] = tc.id
                            if tc.function.name:
                                tool_calls_buf[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_buf[idx]["args"] += tc.function.arguments
                                self.stream_token_count += 1
                                self.total_tokens += 1

                # 最终更新：一次性过滤模型内部标签（流式期间不过滤，避免 O(n²) 拖慢）
                full_content = _strip_model_tokens(full_content)
                think_in_content, body_content = _parse_think_tags(full_content)
                combined_reasoning = (reasoning_content + ("\n" + think_in_content if think_in_content else "")).strip()
                if combined_reasoning:
                    self._update_streaming_with_reasoning(block, body_content, combined_reasoning, final=True)
                else:
                    self._update_streaming(block, body_content, final=True)
                # 用 API 返回的精确 usage 更新 token 统计
                if _api_usage:
                    try:
                        # usage 对象格式：{prompt_tokens, completion_tokens, total_tokens}
                        prompt_t = getattr(_api_usage, "prompt_tokens", None) or _api_usage.get("prompt_tokens", 0)
                        completion_t = getattr(_api_usage, "completion_tokens", None) or _api_usage.get("completion_tokens", 0)
                        if completion_t > 0:
                            # 用精确值替换估算值
                            self.stream_token_count = completion_t
                            self.total_tokens = (self.total_tokens - self.stream_token_count + completion_t
                                                 if self.total_tokens >= self.stream_token_count else completion_t)
                            # 保存精确输入 token 到实例属性
                            self._precise_input_tokens = prompt_t
                    except Exception:
                        pass
                self._update_token_bar()
                # 后续统一使用 body_content（已去除 <think> 标签）
                full_content = body_content
                # 兜底：某些模型（如 GLM-4.7-Flash）会以 <tool_call>name(args)</tool_call>
                # 文本形式输出工具调用，而不是标准 delta.tool_calls。这里解析并转为真实调用。
                if not tool_calls_buf and full_content:
                    xml_calls = _parse_tool_call_xml(full_content)
                    if xml_calls:
                        for idx, tc in enumerate(xml_calls):
                            tool_calls_buf[idx] = {
                                "id": f"call_xml_{idx}_{tc['name']}",
                                "name": tc["name"],
                                "args": tc["arguments"],
                            }
                        # 从展示内容中移除伪 XML 标签，避免用户看到原始标签
                        full_content = re.sub(r"<tool_call>.*?</tool_call>", "", full_content, flags=re.DOTALL).strip()
                # 身份泄露过滤：检测底层模型自报家门，替换为标准 ZeroAI 身份（响应层防线）
                full_content, _id_leaked = _sanitize_identity_leak(full_content)
                if _id_leaked:
                    self._add_static(Text("  └─ 🛡️ 身份保护：已过滤底层模型信息", style=f"bold {C_YELLOW}"))
                # 存储最近回复文本（用于复制）
                if full_content.strip():
                    self._last_reply_text = full_content
                # 被停止时显示截断标记
                if self._stop_generation:
                    self._add_static(Text("  └─ ⏹ 已停止", style=C_DIM))
                    self._is_generating = False
                    if full_content.strip():
                        self.messages.append({"role": "assistant", "content": full_content})
                    return

                # 处理工具调用
                if tool_calls_buf:
                    assistant_msg = {
                        "role": "assistant",
                        "content": full_content if full_content else "",
                        "tool_calls": []
                    }
                    for idx_key, tc in tool_calls_buf.items():
                        if not tc["id"]:
                            tc["id"] = f"call_{idx_key}_{tc['name']}"
                        assistant_msg["tool_calls"].append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["args"]}
                        })
                    self.messages.append(assistant_msg)

                    for tc in tool_calls_buf.values():
                        # 工具执行前检查是否被 Ctrl+C 停止
                        if self._stop_generation:
                            break
                        name = tc["name"]
                        try:
                            args = json.loads(tc["args"]) if tc["args"].strip() else {}
                        except json.JSONDecodeError:
                            args = {}

                        tool_info = f"**调用工具** `{name}`\n\n```json\n{json.dumps(args, ensure_ascii=False, indent=2)}\n```"
                        self._add_static(_safe_markdown(tool_info, code_theme="monokai"))

                        if name in TOOL_MAP:
                            try:
                                # 【修复 2026-09-16】改用统一入口 invoke_tool：
                                #   (a) 参数过滤走单一真源，不再把 **kwargs 型工具
                                #       （MCP 包装函数）的真实参数当"幻觉参数"丢掉；
                                #   (b) 正确 await 异步工具 —— 此前直接
                                #       `fn(**safe_args)` 拿到的是**协程对象**，
                                #       从未被 await，随后 `result += "..."` 抛
                                #       TypeError 被下面 `except TypeError` 吞成
                                #       "参数错误：unsupported operand type(s)
                                #        for +=: 'coroutine' and 'str'"。
                                #       后果：15 个电脑操作工具全部"点了没反应"，
                                #       且报错文案完全误导（实测复现）。
                                result, extra = await invoke_tool(name, args)
                                if extra:
                                    result += f"\n[提示：忽略多余参数 {extra}]"
                                # read_image 返回 base64，构造多模态消息
                                if name == "read_image" and isinstance(result, str) and result.startswith("data:"):
                                    img_path = args.get("path", "图片")
                                    b64_data = result  # 保存 base64 数据
                                    result = f"{_load_svg_icon('document')} 已读取图片：{img_path}，图片内容已发送给模型"
                                    self.messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc["id"],
                                        "name": name,
                                        "content": result,
                                    })
                                    self.messages.append({
                                        "role": "user",
                                        "content": [
                                            {"type": "text", "text": f"这是图片 {img_path}，请描述和理解这张图片"},
                                            {"type": "image_url", "image_url": {"url": b64_data}},
                                        ],
                                    })
                                    self.messages.append({"role": "assistant", "content": "我看到了这张图片，"})
                                    result_md = f"**结果**\n\n{result}"
                                    self._add_static(_safe_markdown(result_md, code_theme="monokai"))
                                    self._add_static(Text("  └─", style=C_DIM))
                                    continue
                            except TypeError as e:
                                result = f"参数错误：{e}"
                            except Exception as e:
                                result = f"执行错误：{e}"
                        else:
                            result = f"未知工具：{name}"

                        # 工具结果长度限制，防止超长内容卡死模型
                        _result_str = str(result)
                        MAX_TOOL_RESULT = 8000  # 字符上限，防止超长结果撑爆上下文
                        _truncated = False
                        if len(_result_str) > MAX_TOOL_RESULT:
                            _orig_len = len(_result_str)
                            _result_str = (_result_str[:MAX_TOOL_RESULT]
                                           + f"\n\n[结果过长，已截断。原始长度 {_orig_len} 字符。"
                                           + "请用更具体的路径或 search_files 精确搜索。]")
                            _truncated = True

                        _result_label = f"**结果**（已截断至 {MAX_TOOL_RESULT} 字符）" if _truncated else "**结果**"
                        result_md = f"{_result_label}\n\n```\n{_result_str}\n```"
                        self._add_static(_safe_markdown(result_md, code_theme="monokai"))

                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": name,
                            "content": _result_str,
                        })
                    self._add_static(Text("  └─", style=C_DIM))
                    _tool_call_count += len(tool_calls_buf)
                    # 工具执行后检查是否被停止
                    if self._stop_generation:
                        self._is_generating = False
                        self._add_static(Text("  ⏹ 已停止\n", style=C_DIM))
                        return
                    # 工具调用次数限制：超过上限则强制结束本轮，避免模型陷入循环
                    if _tool_call_count >= _MAX_TOOL_CALLS_PER_TURN:
                        self._add_static(Text.assemble(
                            ("  ", C_DIM),
                            (f"⚠️ 工具调用次数达到上限（{_MAX_TOOL_CALLS_PER_TURN} 次），已停止自动调用。", f"bold {C_YELLOW}"),
                            ("\n", C_DIM),
                        ))
                        self._is_generating = False
                        return
                    continue
                else:
                    if full_content.strip():
                        self.messages.append({"role": "assistant", "content": full_content})
                    self._add_static(Text("  └─", style=C_DIM))
                    self._is_generating = False
                    return

            except Exception as e:
                err_str = str(e)
                err_type = type(e).__name__
                # 401 认证错误：API Key 无效或未配置
                if "401" in err_str or "AuthenticationError" in err_type or "Authorization" in err_str:
                    self._add_static(Text.assemble(
                        (f"  {_load_svg_icon('cross')} 认证失败（401）：API Key 无效或未配置\n", f"bold {C_FG}"),
                        (f"  错误详情：{err_str[:120]}\n", C_DIM),
                        (f"  解决方法：\n", f"bold {C_FG}"),
                        (f"    1. 按 Ctrl+P 打开设置面板，重新配置 API Key\n", C_FG),
                        (f"    2. 检查 Key 是否正确（智谱GLM 格式：xxx.xxx）\n", C_DIM),
                        (f"    3. 智谱GLM 免费 Key 获取：https://open.bigmodel.cn/\n", C_DIM),
                        (f"    4. 或输入 /模型 ollama 使用本地模型（无需 Key）\n", C_DIM),
                    ))
                elif "429" in err_str or "rate" in err_str.lower():
                    self._add_static(Text.assemble(
                        (f"  {_load_svg_icon('warning')} 速率限制：", f"bold {C_FG}"),
                        (f"{err_str[:100]}\n", C_DIM),
                        (f"  {_load_svg_icon('warning')} 建议：", C_DIM),
                        ("输入 /手动 切到GLM（免费无限）或 /模型 ollama 用本地模型\n", C_FG),
                    ))
                elif "timeout" in err_str.lower() or "timed out" in err_str.lower() or "ReadTimeout" in err_type:
                    self._add_static(Text.assemble(
                        (f"  {_load_svg_icon('warning')} 请求超时（{err_type}）：\n", f"bold {C_FG}"),
                        (f"  {err_str[:150]}\n", C_DIM),
                        (f"  {_load_svg_icon('warning')} 已自动重试 {max_retries} 次仍失败\n", C_DIM),
                        (f"  {_load_svg_icon('warning')} 建议：\n", f"bold {C_FG}"),
                        (f"    1. 输入 /手动 切到 GLM（国内直连·稳定）\n", C_FG),
                        (f"    2. OpenRouter 慢时可开启 VPN 或稍后重试\n", C_DIM),
                    ))
                else:
                    self._add_static(Text.assemble(
                        (f"  {err_type}：", f"bold {C_FG}"),
                        (f"{err_str[:200]}\n", C_DIM),
                    ))
                if self.messages and self.messages[-1].get("role") == "user":
                    self.messages.pop()
                while self.messages and self.messages[-1].get("role") in ("assistant", "tool"):
                    self.messages.pop()
                self._is_generating = False
                return
