"""ZeroAI 主应用类：生命周期、消息块骨架（方法按职责拆在同目录 app_*.py）

【2026-10-06 拆分】
  本文件从 3,664 行 / 单类 3,513 行瘦身为骨架，方法按职责搬到同目录的
  8 个 mixin（`class ZeroAI(..., App)` 的类头即清单）：

      app_settings.py   SettingsMixin     模型与设置面板（Ctrl+P / 增删模型 / 代理）
      app_clipboard.py  ClipboardMixin    代码块复制（Ctrl+Y）、粘贴图片（Ctrl+G）
      app_voice.py      VoiceMixin        按住说话（Ctrl+T）、语音对话、伴随模式
      app_commands.py   CommandMixin      on_input_submitted 输入分发、/帮助、/init
      app_mcp.py        MCPCommandMixin   MCP 后台初始化提示、/mcp 命令
      app_turn_loop.py  TurnLoopMixin     单专家主循环 _run_turn / _run_turn_impl
      app_turn_multi.py MultiTurnMixin    混合思考 _run_hybrid_turn、ReAct _run_react_turn
      app_streaming.py  StreamingMixin    流式渲染、Token 栏、上下文窗口

  方法体**逐字节未改**：拆分脚本按 AST 定位行区间原样搬运，
  验收关卡为 方法集合不变 / 字节一致 / 无悬空全局名 / MRO 合法 / 全量测试通过。
  复杂度闸门见 `tests/test_tui_source_budget.py`（单文件 800 行即红）。

【迁移历史】
  阶段 0  本模块最初是 `from tui_agent import ZeroAI` 的转发壳（16 行）。
  轮次 N  类体（3,459 行）从 tui_agent.py L9879-13337 搬到此处，字节保真。
          tui_agent.py 侧改为转发导入，外部 `from tui_agent import ZeroAI`
          仍然可用，且拿到的是**同一个对象**。

【为什么必须先改 markdown.py 才能搬】
  存在两条以本模块为终点的循环边：
      zeroai.tui.app → zeroai.tui.markdown → tui_agent → zeroai.tui.app
      zeroai.tui.app → zeroai.tui.identity → tui_agent → zeroai.tui.app
  一旦本模块承载真实实现，上述路径就会拿到「部分初始化」的中间模块。
  沙箱实验（exp_sandbox_test.py，7 入口 × 4 方案）实测：
      TA（app 反向依赖 tui_agent）  E1 PASS，其余 FAIL
      R （只改 app 的 render 导入）  E1 PASS，其余 FAIL   ← 四代理共识、但错误
      X1（app 用 render + markdown 去反向依赖）  E1..E7 全 PASS
      X2（X1 + identity 去反向依赖）             E1..E7 全 PASS
  所以真正的前提在 markdown.py 那一侧，不在本模块。

【依赖方向（本模块与同级 app_*.py 一起，都只朝下看）】
  zeroai.tui.{app, app_*} → zeroai.core.* / zeroai.tools.* / zeroai.tui.{colors,widgets,screens,icons}
  本模块与全部 app_*.py **均不**导入 tui_agent。

【为什么故意不写 from __future__ import annotations】
  该语句会把方法注解变成字符串、把嵌套函数的注解折叠成常量元组，
  从而让方法的字节码与迁移前不一致（_run_hybrid_turn / _run_react_turn
  已拆到 app_turn_multi.py，同理其所在模块也不写）。
  虽然已证明它语义惰性（不改变任何数据流、Textual 也不读注解），
  但「零语义改动」是本次解耦的硬指标，因此宁可放弃这个便利。
  去掉后全部方法字节码可与迁移前逐字节比对相等。
"""
import asyncio
import base64
import inspect
import json
import os
import re
import subprocess
import time
from pathlib import Path

from openai import AsyncOpenAI, OpenAI
from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static

# ---- core 层 ----
from zeroai.core.agents_md import _auto_generate_agents_md
from zeroai.core.constants import (
    CLEANUP_THRESHOLD_RATIO,
    COMPRESS_THRESHOLD_RATIO,
    EXPERT_MEMORY_TURNS,
    EXPERT_TEAM,
    HYBRID_DEDUP_SIMILARITY_THRESHOLD,
    HYBRID_ENABLE_COLLAB_CHAIN,
    HYBRID_EXPERT_MAX_CHARS,
    HYBRID_MAX_PARALLEL_EXPERTS,
    MODEL_CONFIGS,
    set_work_mode,
)
from zeroai.core.paths import WORK_DIR
from zeroai.core.context_compress import (
    _estimate_tokens,
    _filter_messages_for_model,
    _get_model_context_limit,
    cleanup_and_compress,
)
from zeroai.core.expert_route import (
    _OPENROUTER_FAIL_COUNTS,
    _check_openrouter_circuit_breaker,
    _record_openrouter_failure,
    _record_openrouter_success,
    get_expert_config,
    route_expert,
    route_expert_glm,
)
from zeroai.core.model_manager import (
    CURRENT_MODEL_KEY,
    _save_custom_models,
    detect_ollama_models,
    get_model_display_name,
)
from zeroai.core.prompts import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_CORE,
    TOOL_CAPABILITY_PROMPT,
)
from zeroai.core.response_utils import (
    _jaccard_similarity,
    _parse_think_tags,
    _sanitize_identity_leak,
    _strip_model_tokens,
    _truncate_expert_response,
)
from zeroai.core.runtime import (
    _interruptible_await,
    _interruptible_sleep,
    _set_stop_flag,
)
from zeroai.core.secrets import (
    _is_proxy_enabled,
    _load_config,
    _load_proxy_config,
    _make_openai_client,
    _make_openai_sync_client,
    _refresh_proxy_config,
    _save_config,
    _save_proxy_config,
)
from zeroai.core.tool_call_parser import (
    needs_tool_calls as _needs_tool_calls,
    parse_tool_call_xml as _parse_tool_call_xml,
)

# ---- tools 层 ----
from zeroai.tools.clipboard import _copy_to_clipboard
from zeroai.tools.file_manager import read_image
from zeroai.tools.registry import TOOL_MAP, TOOLS, invoke_tool
from zeroai.tools.render import (
    _safe_markdown,
    render_image_preview,
    render_latex_in_text,
)
from zeroai.tools.security import security_audit
from zeroai.tools.voice import listen_asr, speak_tts
from zeroai.tools.window_mgr import read_screen_content

# ---- tui 层（同层，且均已去反向依赖）----
from zeroai.tui.colors import (
    C_ACCENT,
    C_BG,
    C_BLUE,
    C_BORDER,
    C_CYAN,
    C_DIM,
    C_FG,
    C_GREEN,
    C_PURPLE,
    C_RED,
    C_YELLOW,
)
from zeroai.tui.icons import _load_svg_icon
from zeroai.tui.screens import AddModelScreen, SettingsScreen, VoiceDialogScreen
from zeroai.tui.widgets import HintBar, InfoBar, MessageInput, TokenBar

from zeroai.tui.app_settings import SettingsMixin
from zeroai.tui.app_clipboard import ClipboardMixin
from zeroai.tui.app_voice import VoiceMixin
from zeroai.tui.app_commands import CommandMixin
from zeroai.tui.app_mcp import MCPCommandMixin
from zeroai.tui.app_turn_loop import TurnLoopMixin
from zeroai.tui.app_turn_multi import MultiTurnMixin
from zeroai.tui.app_streaming import StreamingMixin


class ZeroAI(
    SettingsMixin,
    ClipboardMixin,
    VoiceMixin,
    CommandMixin,
    MCPCommandMixin,
    TurnLoopMixin,
    MultiTurnMixin,
    StreamingMixin,
    App,
):
    """ZeroAI 终端 AI 助手"""

    CSS = f"""
    Screen {{
        background: {C_BG};
        color: {C_FG};
    }}

    /* 顶部：信息条（极简） */
    #info {{
        dock: top;
        height: 1;
        background: {C_BG};
        color: {C_DIM};
        padding: 0 2;
    }}

    /* 中间：对话区 + 右侧状态栏 */
    #main-area {{
        height: 1fr;
    }}

    #log-scroll {{
        width: 1fr;
        background: {C_BG};
        padding: 1 2;
        overflow-y: auto;
        scrollbar-color: {C_DIM};
        scrollbar-background: {C_BG};
        scrollbar-size-vertical: 1;
    }}

    #token-bar {{
        dock: right;
        width: 22;
        height: 1fr;
        background: {C_BG};
        color: {C_FG};
        padding: 1 1;
    }}

    .msg-block {{
        background: {C_BG};
        color: {C_FG};
        padding: 0 0 1 0;
        margin: 0 0 1 0;
    }}

    .msg-header {{
        color: {C_DIM};
        text-style: bold;
    }}

    /* 底部：输入区（黑色填充大输入框） */
    #input-wrap {{
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 18;
        background: {C_BG};
        border-top: solid {C_BORDER};
        padding: 0 2;
    }}

    #input {{
        background: {C_BG};
        color: {C_FG};
        border: solid {C_BORDER};
        height: auto;
        min-height: 1;
        max-height: 15;
        padding: 0 1;
    }}

    #input:focus {{
        border: solid {C_DIM};
    }}

    #input .cursor {{
        background: {C_FG};
        color: {C_BG};
    }}

    /* 底部：快捷键栏（极简灰色） */
    #hints {{
        dock: bottom;
        height: 1;
        background: {C_BG};
        color: {C_DIM};
        padding: 0 2;
    }}
    """

    BINDINGS = [
        Binding("ctrl+c", "stop_or_quit", "停止/退出", show=False),
        Binding("ctrl+l", "clear_log", "清屏", show=False),
        Binding("ctrl+n", "clear_history", "新对话", show=False),
        Binding("ctrl+p", "open_settings", "设置", show=False),
        Binding("ctrl+y", "copy_last_reply", "复制", show=False),
        Binding("ctrl+v", "paste_image", "粘贴图片", show=False),
        Binding("ctrl+g", "paste_image", "粘贴图片", show=False),
        Binding("ctrl+w", "toggle_companion", "伴随模式", show=False),
        Binding("ctrl+t", "push_to_talk", "语音输入", show=False),
        Binding("ctrl+d", "voice_dialog", "语音对话", show=False),
        Binding("pageup", "scroll_pageup", "上翻页", show=False),
        Binding("pagedown", "scroll_pagedown", "下翻页", show=False),
    ]

    # 禁用 Textual 自带的命令面板（避免和我们的设置冲突）
    ENABLE_COMMAND_PALETTE = False

    def __init__(self):
        super().__init__()
        # ── 自动加载/生成 AGENTS.md（仿 OpenCode 项目上下文机制）──
        # 启动时自动检测：有 AGENTS.md 就加载，没有就自动生成
        self._agents_md_content = ""
        _agents_path = os.path.join(WORK_DIR, "AGENTS.md")
        if os.path.exists(_agents_path):
            try:
                with open(_agents_path, "r", encoding="utf-8") as _f:
                    self._agents_md_content = _f.read().strip()
            except Exception:
                pass
        else:
            # 没有 AGENTS.md，自动生成（静默模式，不显示 UI）
            try:
                self._agents_md_content = _auto_generate_agents_md(WORK_DIR)
            except Exception:
                self._agents_md_content = ""
        # 如果有 AGENTS.md，注入到 system prompt
        if self._agents_md_content:
            _full_system = SYSTEM_PROMPT + "\n\n# 项目上下文（AGENTS.md）\n" + self._agents_md_content
        else:
            _full_system = SYSTEM_PROMPT
        self.messages = [{"role": "system", "content": _full_system}]
        self.model_key = CURRENT_MODEL_KEY
        self.work_mode = "expert"  # expert / hybrid / manual（默认专家路由，自动选择最合适的专家）
        # 可调参数
        self.temperature = 0.3
        self.stream_enabled = True
        self.max_turns = 8
        self.context_limit = 8192
        # Token 统计
        self.total_tokens = 0
        self.stream_start_time = 0.0
        self.stream_token_count = 0
        self._precise_input_tokens = 0  # API 返回的精确输入 token（0 表示用估算）
        # 用户滚动状态（用户往上翻看时暂停自动滚底）
        self._user_scrolling = False
        # 停止生成标志（Ctrl+C 第一次停止，第二次退出）
        self._stop_generation = False
        self._is_generating = False
        # 最近一次助手回复的纯文本（用于复制）
        self._last_reply_text = ""
        # 最近一次回复中的代码块列表（用于 /copy N 命令复制指定代码块）
        self._last_reply_code_blocks = []
        # 专家记忆：每个专家维护独立上下文，避免主上下文污染
        # 结构：{expert_key: [{"role": "user"|"assistant", "content": "..."}, ...]}
        self._expert_memory = {}
        # 待发送的图片 base64 列表（Ctrl+V 粘贴）
        self._pending_images = []
        # 伴随模式（屏幕感知）
        self._companion_mode = False
        self._companion_log = []  # 最近的屏幕变化日志
        self._last_window_title = ""
        self._last_clipboard_text = ""
        self._companion_thread = None
        # 语音交互状态
        self._tts_enabled = False  # TTS 朗读开关（由 /语音 命令切换）
        self._tts_voice = "zh-CN-XiaoxiaoNeural"  # 默认女声
        self._tts_rate = "+0%"  # 默认语速
        self._is_listening = False  # ASR 录音中状态
        self._voice_dialog_active = False  # 语音对话模式（保留兼容字段）
        # ReAct Agent 模式（/react 切换）：启用后走 观察-思考-行动 循环
        self.react_enabled = False
        # RAG 检索器（/索引 命令构建后自动启用）
        self._retriever = None









    def compose(self) -> ComposeResult:
        yield InfoBar(id="info")
        # 中间：对话 + 右侧 token 栏
        with Horizontal(id="main-area"):
            yield VerticalScroll(id="log-scroll")
            yield TokenBar(id="token-bar")
        # 底部：快捷键 + 输入区
        yield HintBar(id="hints")
        with Vertical(id="input-wrap"):
            yield MessageInput(placeholder="输入消息…  /帮助  Ctrl+P 设置  Ctrl+G 粘贴图片  Ctrl+T 语音输入  Ctrl+J 换行", id="input")

    def on_mount(self) -> None:
        scroll = self.query_one("#log-scroll", VerticalScroll)
        # 欢迎信息（MiMo 风格：最简、灰文字、彩色工具标签）
        parts = [
            ("  你好！我是 ZeroAI 助手，由 7 个专家模型构建。\n", C_FG),
            ("  我可以帮你处理各种任务，包括：\n\n", C_FG),
            ("    •  ", C_DIM), ("代码编写、调试、重构、项目搭建\n", C_FG),
            ("    •  ", C_DIM), ("数学证明、逻辑推理、问题分析\n", C_FG),
            ("    •  ", C_DIM), ("文档写作、翻译、润色\n", C_FG),
            ("    •  ", C_DIM), ("图片理解、截图分析\n", C_FG),
            ("    •  ", C_DIM), ("学术研究、文献检索、引用检查\n\n", C_FG),
            ("  有什么我可以帮你的吗？\n\n", C_FG),
            ("  ▶ 输入问题回车发送，或按 ", C_DIM),
            ("Ctrl+T", C_ACCENT),
            (" 开启语音对话\n", C_DIM),
        ]
        # 显示模型状态：内置 Key 已就绪
        if MODEL_CONFIGS.get("glm", {}).get("api_key"):
            parts.append(("  ┌──────────────────────────────────────┐\n", f"bold {C_FG}"))
            parts.append(("  │  ✓ 免费模型已就绪，可直接使用        │\n", f"bold {C_FG}"))
            parts.append(("  └──────────────────────────────────────┘\n", f"bold {C_FG}"))
        else:
            parts.append(("  ┌──────────────────────────────────────┐\n", f"bold {C_FG}"))
            parts.append(("  │  [!] 未配置 API 密钥                │\n", f"bold {C_FG}"))
            parts.append(("  │                                      │\n", f"bold {C_FG}"))
            parts.append(("  │  按 Ctrl+P 打开设置面板配置 Key     │\n", f"bold {C_FG}"))
            parts.append(("  │  智谱GLM 免费 Key 获取：            │\n", f"bold {C_FG}"))
            parts.append(("  │  https://open.bigmodel.cn/          │\n", f"bold {C_FG}"))
            parts.append(("  └──────────────────────────────────────┘\n", f"bold {C_FG}"))
        parts.append(("  直接输入需求开始对话，输入 /帮助 查看命令\n", C_DIM))
        welcome = Static(Text.assemble(*parts), classes="msg-block")
        scroll.mount(welcome)
        self.query_one("#input", MessageInput).focus()

        # 后台异步初始化 MCP 工具（不阻塞 UI）
        self._auto_init_mcp()




    def _add_block(self, header: str, header_color: str = C_BLUE) -> Static:
        """添加一个消息块，返回 Static widget 以便后续 update"""
        scroll = self.query_one("#log-scroll", VerticalScroll)
        block = Static(Text.assemble(
            (f"  ┌─ {header}\n", f"bold {header_color}"),
            ("  │\n", C_DIM),
        ), classes="msg-block")
        scroll.mount(block)
        self._user_scrolling = False
        scroll.scroll_end(animate=False)
        # 保持输入框焦点（mount 新 widget 可能导致焦点丢失）
        self._keep_input_focus()
        return block

    def _add_static(self, content) -> Static:
        """添加一个纯 Static 内容块"""
        scroll = self.query_one("#log-scroll", VerticalScroll)
        block = Static(content, classes="msg-block")
        scroll.mount(block)
        if not self._user_scrolling:
            scroll.scroll_end(animate=False)
        # 保持输入框焦点
        self._keep_input_focus()
        return block

    def _keep_input_focus(self):
        """确保输入框保持焦点（生成过程中也能打字）"""
        try:
            inp = self.query_one("#input", MessageInput)
            if not inp.has_focus:
                self.call_after_refresh(inp.focus)
        except Exception:
            pass

    def _get_system_prompt(self) -> str:
        """获取系统提示词（含 AGENTS.md 项目上下文）"""
        if getattr(self, "_agents_md_content", ""):
            return SYSTEM_PROMPT + "\n\n# 项目上下文（AGENTS.md）\n" + self._agents_md_content
        return SYSTEM_PROMPT

    def action_clear_log(self):
        scroll = self.query_one("#log-scroll", VerticalScroll)
        for child in list(scroll.children):
            child.remove()
        self.on_mount()
        self.notify("已清屏")

    def action_clear_history(self):
        self.messages = [{"role": "system", "content": self._get_system_prompt()}]
        self.total_tokens = 0
        self.stream_token_count = 0
        scroll = self.query_one("#log-scroll", VerticalScroll)
        for child in list(scroll.children):
            child.remove()
        self.on_mount()
        try:
            self.query_one("#token-bar", TokenBar).update_stats(0, 0.0)
        except Exception:
            pass
        self.notify("已开始新对话")

    def action_stop_or_quit(self):
        import time as _time
        if self._is_generating:
            self._stop_generation = True
            # 同步到全局标志，让独立函数（route_expert_glm 等）也能感知
            _set_stop_flag(True)
            self._add_static(Text.assemble(
                ("  ⏹ 已停止生成", f"bold {C_FG}"),
                ("（再按 Ctrl+C 退出）\n", C_DIM),
            ))
        else:
            # 空闲时：先检查是否有选中文本，有则复制选中
            try:
                selected = self.screen.get_selected_text()
                if selected and selected.strip():
                    if _copy_to_clipboard(selected):
                        preview = selected[:40].replace("\n", " ")
                        self.notify(f"已复制选中：{preview}…")
                    else:
                        import subprocess
                        subprocess.run("clip", input=selected, text=True, check=True)
                        self.notify("已复制选中内容")
                    return
            except Exception:
                pass
            # 无选中：复制最近回复，连续按两次 Ctrl+C 才退出
            now = _time.time()
            last_press = getattr(self, "_last_ctrl_c_time", 0)
            if now - last_press < 2.0:
                # 2 秒内第二次按 Ctrl+C，退出
                self.exit()
                return
            self._last_ctrl_c_time = now
            # 第一次按 Ctrl+C：复制最近回复
            if self._last_reply_text and self._last_reply_text.strip():
                if _copy_to_clipboard(self._last_reply_text):
                    preview = self._last_reply_text[:40].replace("\n", " ")
                    self.notify(f"已复制回复（再按 Ctrl+C 退出）：{preview}…")
                else:
                    import subprocess
                    try:
                        subprocess.run("clip", input=self._last_reply_text, text=True, check=True)
                        self.notify("已复制回复（再按 Ctrl+C 退出）")
                    except Exception:
                        self.notify("复制失败，再按 Ctrl+C 退出")
            else:
                self.notify("无内容可复制，再按 Ctrl+C 退出")












    def action_scroll_pageup(self):
        self._user_scrolling = True
        self.query_one("#log-scroll", VerticalScroll).scroll_page_up()

    def action_scroll_pagedown(self):
        scroll = self.query_one("#log-scroll", VerticalScroll)
        scroll.scroll_page_down()
        # 翻到底部时取消用户滚动标志
        if scroll.is_scrollable and scroll.scroll_y >= scroll.max_scroll_y - 1:
            self._user_scrolling = False

    def on_scroll_up(self, event) -> None:
        """鼠标滚轮向上 — 标记用户在翻看历史"""
        self._user_scrolling = True

    def on_scroll_down(self, event) -> None:
        """鼠标滚轮向下 — 翻到底部时恢复自动滚底"""
        scroll = self.query_one("#log-scroll", VerticalScroll)
        if scroll.is_scrollable and scroll.scroll_y >= scroll.max_scroll_y - 1:
            self._user_scrolling = False














__all__ = ["ZeroAI"]
