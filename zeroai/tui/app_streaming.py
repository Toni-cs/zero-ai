"""流式渲染：消息块实时更新（纯文本 → Markdown）、Token 统计栏、上下文窗口显示（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import time
from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static
from zeroai.core.constants import EXPERT_TEAM, MODEL_CONFIGS
from zeroai.core.context_compress import _estimate_tokens, _get_model_context_limit
from zeroai.core.expert_route import get_expert_config
from zeroai.tools.render import _safe_markdown, render_latex_in_text
from zeroai.tui.colors import C_CYAN, C_DIM, C_FG, C_GREEN, C_RED
from zeroai.tui.widgets import HintBar, TokenBar


class StreamingMixin:
    """流式渲染：消息块实时更新（纯文本 → Markdown）、Token 统计栏、上下文窗口显示"""

    def _update_token_bar(self):
        """更新右侧 Token 统计栏（输入+输出 token，动态上下文窗口）"""
        try:
            token_bar = self.query_one("#token-bar", TokenBar)
            elapsed = time.time() - self.stream_start_time
            rate = self.stream_token_count / elapsed if elapsed > 0.1 else 0.0
            # 输入 token：优先用 API 返回的精确值，否则用估算值
            input_tokens = getattr(self, '_precise_input_tokens', 0) or _estimate_tokens(self.messages)
            # 动态获取当前模型的上下文窗口
            ctx_window = self._get_current_ctx_window()
            token_bar.update_stats(self.total_tokens, rate, input_tokens, ctx_window)
            # 同步更新底部 HintBar 的 token 显示
            try:
                hint_bar = self.query_one("#hints", HintBar)
                hint_bar.update_ctx(input_tokens + self.total_tokens, ctx_window)
            except Exception:
                pass
        except Exception:
            pass
    def _get_current_ctx_window(self) -> int:
        """获取当前工作模式下的实际上下文窗口大小"""
        try:
            if self.work_mode == "expert":
                # 专家模式：用当前路由专家的模型
                expert_key = getattr(self, '_current_expert_key', None) or 'knowledge'
                if expert_key in EXPERT_TEAM:
                    e_cfg = get_expert_config(expert_key)
                    limit = _get_model_context_limit(e_cfg["model"])
                    return limit if limit > 0 else 128000
            elif self.work_mode == "hybrid":
                # 混合模式：用 PM 的模型
                e_cfg = get_expert_config("pm")
                limit = _get_model_context_limit(e_cfg["model"])
                return limit if limit > 0 else 128000
            # 手动模式：用当前选定的模型配置
            cfg = MODEL_CONFIGS.get(self.model_key, {})
            limit = _get_model_context_limit(cfg.get("model", ""))
            return limit if limit > 0 else 128000
        except Exception:
            return 128000
    def _update_streaming(self, block: Static, content: str, final: bool = False):
        """实时更新消息块 - 流式时纯文本稳定显示，完成后 Markdown 渲染"""
        if not content.strip():
            return
        try:
            # 提取当前专家标签
            expert_label = getattr(self, '_current_expert_label', 'Expert')
            if final:
                # 完成：先扫描代码块（供 /copy N 使用）
                self._last_reply_code_blocks = self._extract_code_blocks(content)
                # 完成：用 Markdown 渲染（Typora 风格，含 LaTeX 公式渲染）
                md = _safe_markdown(render_latex_in_text(content), code_theme="monokai")
                from rich.console import Group
                elapsed = time.time() - getattr(self, 'stream_start_time', time.time())
                # 若有代码块，在末尾添加复制提示
                copy_hint = None
                if self._last_reply_code_blocks:
                    hint_parts = [("  └─ 📋 可复制代码块：", C_DIM)]
                    for i, b in enumerate(self._last_reply_code_blocks, 1):
                        hint_parts.append((f" /copy {i}", f"bold {C_CYAN}"))
                        hint_parts.append((f"({b['lang']})  ", C_DIM))
                    hint_parts.append(("\n", ""))
                    copy_hint = Text.assemble(*hint_parts)
                group = Group(
                    Text.assemble(
                        (f"  ⏵ 构建 · {expert_label}", f"bold {C_RED}"),
                        (f"  ·  {elapsed:.1f}s", C_DIM),
                    ),
                    md,
                    copy_hint,
                ) if copy_hint else Group(
                    Text.assemble(
                        (f"  ⏵ 构建 · {expert_label}", f"bold {C_RED}"),
                        (f"  ·  {elapsed:.1f}s", C_DIM),
                    ),
                    md,
                )
                block.update(group)
                # 完成时才滚底
                if not self._user_scrolling:
                    self.query_one("#log-scroll", VerticalScroll).scroll_end(animate=False)
            else:
                # 流式：节流UI更新
                now = time.time()
                last_update = getattr(self, '_last_stream_update', 0)
                if now - last_update < 0.05:  # 50ms节流（20fps）
                    return
                self._last_stream_update = now
                
                # 用Text.assemble（Rich优化过的）
                parts = [(f"  ⏵ 构建 · {expert_label}", f"bold {C_RED}"), ("\n", "")]
                # 流式时只保留尾部固定行数，避免全文重排随内容增长而 O(n²) 变慢
                _max_stream_lines = 120
                if len(content) > _max_stream_lines * 80:
                    _tail_start = max(0, len(content) - _max_stream_lines * 80)
                    _nl = content.find("\n", _tail_start)
                    _display_content = content[_nl + 1:] if _nl != -1 else content[_tail_start:]
                    parts.append(("  ...（前面内容流式折叠中）\n", C_DIM))
                else:
                    _display_content = content
                for line in _display_content.split("\n"):
                    parts.append((f"  {line}\n", C_FG))
                block.update(Text.assemble(*parts))
                
                # 节流scroll_end：每200ms最多调用一次
                last_scroll = getattr(self, '_last_scroll_time', 0)
                if not self._user_scrolling and now - last_scroll > 0.2:
                    self._last_scroll_time = now
                    self.query_one("#log-scroll", VerticalScroll).scroll_end(animate=False)
        except Exception:
            # 降级：纯文本
            block.update(Text.assemble(
                ("  ┌─ 助手\n", f"bold {C_GREEN}"),
                (f"  {content}", C_FG),
            ))
    def _update_streaming_with_reasoning(self, block: Static, content: str,
                                          reasoning: str, final: bool = False):
        """实时更新消息块（含思考过程显示）

        思考过程用灰色显示在正文上方，完成后可折叠。
        - 流式中：思考过程实时显示（灰色），正文在下方（白色）
        - 完成后：思考过程用 Panel 包裹（灰色斜体），正文用 Markdown 渲染
        """
        try:
            from rich.console import Group
            if final:
                # 完成：先扫描代码块（供 /copy N 使用）
                self._last_reply_code_blocks = self._extract_code_blocks(content)
                # 完成：思考过程用 Panel + 正文用 Markdown
                md = _safe_markdown(render_latex_in_text(content), code_theme="monokai") if content.strip() else Text("")
                # 思考过程限制显示长度（太长影响阅读）
                reasoning_display = reasoning if len(reasoning) <= 2000 else reasoning[:2000] + "\n  ...（思考过程较长，已截断）"
                reasoning_panel = Panel(
                    Text(reasoning_display, style=f"italic {C_DIM}"),
                    title="思考过程",
                    title_align="left",
                    border_style=C_DIM,
                    padding=(0, 1),
                )
                # 若有代码块，在末尾添加复制提示
                copy_hint = None
                if self._last_reply_code_blocks:
                    hint_parts = [("  └─ 📋 可复制代码块：", C_DIM)]
                    for i, b in enumerate(self._last_reply_code_blocks, 1):
                        hint_parts.append((f" /copy {i}", f"bold {C_CYAN}"))
                        hint_parts.append((f"({b['lang']})  ", C_DIM))
                    hint_parts.append(("\n", ""))
                    copy_hint = Text.assemble(*hint_parts)
                group = Group(
                    Text.assemble(
                        ("  ┌─ 助手\n", f"bold {C_GREEN}"),
                        ("  │\n", C_DIM),
                    ),
                    reasoning_panel,
                    Text("  │\n", C_DIM),
                    md,
                    copy_hint,
                ) if copy_hint else Group(
                    Text.assemble(
                        ("  ┌─ 助手\n", f"bold {C_GREEN}"),
                        ("  │\n", C_DIM),
                    ),
                    reasoning_panel,
                    Text("  │\n", C_DIM),
                    md,
                )
                block.update(group)
                # 完成时才滚底
                if not self._user_scrolling:
                    self.query_one("#log-scroll", VerticalScroll).scroll_end(animate=False)
            else:
                # 流式：节流UI更新
                now = time.time()
                last_update = getattr(self, '_last_stream_update', 0)
                if now - last_update < 0.05:  # 50ms节流
                    return
                self._last_stream_update = now
                
                # 用Text.assemble（Rich优化过的）
                parts = [("  ┌─ 助手\n", f"bold {C_GREEN}"), ("  │\n", C_DIM)]
                if reasoning.strip():
                    parts.append(("  │ 💭 思考中…\n", f"italic {C_DIM}"))
                    # 只显示最后几行思考内容（避免刷屏）
                    reasoning_lines = reasoning.split("\n")
                    show_lines = reasoning_lines[-6:]  # 最后6行
                    for line in show_lines:
                        if line.strip():
                            parts.append((f"  │ {line}\n", C_DIM))
                    parts.append(("  │\n", C_DIM))
                if content.strip():
                    # 流式时只保留尾部固定行数，避免全文重排随内容增长而 O(n²) 变慢
                    _max_stream_lines = 120
                    if len(content) > _max_stream_lines * 80:
                        _tail_start = max(0, len(content) - _max_stream_lines * 80)
                        _nl = content.find("\n", _tail_start)
                        _display_content = content[_nl + 1:] if _nl != -1 else content[_tail_start:]
                        parts.append(("  ...（前面内容流式折叠中）\n", C_DIM))
                    else:
                        _display_content = content
                    for line in _display_content.split("\n"):
                        parts.append((f"  {line}\n", C_FG))
                else:
                    parts.append(("  ⏳…\n", C_DIM))
                block.update(Text.assemble(*parts))
                
                # 节流scroll_end
                last_scroll = getattr(self, '_last_scroll_time', 0)
                if not self._user_scrolling and now - last_scroll > 0.2:
                    self._last_scroll_time = now
                    self.query_one("#log-scroll", VerticalScroll).scroll_end(animate=False)
        except Exception:
            # 降级：纯文本
            block.update(Text.assemble(
                ("  ┌─ 助手\n", f"bold {C_GREEN}"),
                (f"  {content}", C_FG),
            ))
