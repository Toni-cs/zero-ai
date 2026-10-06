"""ZeroAI TUI 模态对话框

本模块是 AddModelScreen / SettingsScreen / VoiceDialogScreen 的**唯一定义处**。

历史沿革（三段）：
1. 最初本文件是 `from tui_agent import ...` 的转发壳；
2. 2026-09-15 第一轮：AddModelScreen / SettingsScreen 实现搬入；
3. 2026-09-15 第二轮：VoiceDialogScreen（1573 行）实现搬入。

搬迁依据：AST 实测这三个类**没有任何 global 语句**，只读取下列外部名字，
全部可从 zeroai 显式导入，故可直接搬运、无需先抽取状态。

- AddModelScreen: 添加自定义模型对话框
- SettingsScreen: 设置面板（Ctrl+P 打开）
- VoiceDialogScreen: 语音讨论助手（见下方"曾经的阻塞"）

【曾经的阻塞：VoiceDialogScreen 为什么第三轮才搬得动】
它当时含 `global _ASR_MODEL`，而 `_ASR_MODEL` / `_SENSE_VOICE_*` 这套语音
单例在 tui_agent.py 与 zeroai/tools/voice.py 里**各有一份**，且识别器
初始化代码在两处逐字重复 —— 运行时真的会加载**两份** SenseVoice 模型
（int8 约 220MB × 2）。搬迁前必须先把单例收敛到 voice.py 的
`get_asr_model()` / `recognize_audio()`；完成后再搬本类，`global` 就消失了
（本类如今实测 0 条 global 语句）。

【为什么撤掉了模块级 __getattr__】
第二轮之前，本模块用 PEP 562 的 `__getattr__` 惰性转发
`VoiceDialogScreen` 到 tui_agent，以打破循环导入（tui_agent 导入本模块的
AddModelScreen/SettingsScreen，本模块又回头导入 tui_agent）。
现在 VoiceDialogScreen 就是**本模块的定义**，转发层已无存在理由，故删除。
`zeroai.tui.screens.VoiceDialogScreen` 这个公开访问路径保持不变 ——
tests/test_release_readiness.py 与 zero-ai-repo/test_v1_1_3_release.py
都依赖它。
"""
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, RichLog, Static
from textual.binding import Binding

from zeroai.core.constants import EXPERT_TEAM
from zeroai.core.model_manager import MODEL_CONFIGS
from zeroai.core.response_utils import _strip_model_tokens
from zeroai.core.secrets import (
    PROXY_CONFIG,
    _is_proxy_enabled,
    _make_openai_sync_client,
)
from zeroai.tools.file_manager import read_image
from zeroai.tools.voice import listen_asr, speak_tts
from zeroai.tui.colors import (
    C_ACCENT, C_AI_BUBBLE, C_BG, C_BG2, C_BORDER, C_DIM, C_FG,
    C_GREEN, C_RED, C_USER_BUBBLE, C_YELLOW,
)
from openai import OpenAI

import time
from pathlib import Path

# 【为什么 VoiceDialogScreen 需要上面这些模块级导入】
# 这些名字原本来自 tui_agent.py 的模块级命名空间，类体里直接裸用（不是
# self.xxx，也不是方法内 import）。搬到本模块后必须自己绑定，否则类定义
# 阶段就抛 NameError（实测第一轮导入即 `name 'events' is not defined`）。
#
# 逐个说明来源与搬法（AST 实测，2026-09-15）：
#   events              ← `from textual import events`，用于 on_click 的类型标注
#   Horizontal          ← textual.containers，用于 compose() 里的 with Horizontal(...)
#   RichLog             ← textual.widgets，字幕区
#   VerticalScroll      ← textual.containers，消息滚动区
#   time                ← 标准库。⚠️ 注意此处与本模块原有的搭配：
#                         搬入前 tui_agent 顶层有 `import time`，本模块没有；
#                         类里 11 处在用（计时/动画/轮询 sleep）。
#   Path                ← pathlib。类里用 Path(x).name 取文件名。
#   OpenAI              ← openai。L1458 在**方法内再次 import**，所以那个
#                         使用点其实不依赖模块级绑定；但为保持与搬入前
#                         完全一致的解析行为，仍在此绑定。
#   read_image          ← zeroai.tools.file_manager（已由 zeroai.* 提供）
#   _strip_model_tokens ← zeroai.core.response_utils
#   listen_asr / speak_tts ← zeroai.tools.voice
#
# 【2026-09-16 更新】此处原先记着：「L1830 方法内 `from tui_agent import
# speak_tts` 是唯一有意保留的跨模块引用，语义上拿到的就是同一个 speak_tts
# （tui_agent 的首层转发已指向 zeroai.tools.voice）」。
#
# **这段注释是错的，已实测证伪：**
#   tui_agent.speak_tts is zeroai.tools.voice.speak_tts  →  False
#   tui_agent.speak_tts.__module__                        →  'tui_agent'
# 它并不是转发，而是 tui_agent.py 里的**第二份独立实现**。两者签名相同、
# 除一行 docstring 外源码逐字节相同，所以行为一致 —— 但「同一对象」的说法
# 不成立，属于典型的双份真源。
#
# 已改为直接 `from zeroai.tools.voice import speak_tts`，
# 消除该函数级反向依赖（模块级检查发现不了这类依赖）。

__all__ = ["AddModelScreen", "SettingsScreen", "VoiceDialogScreen"]


class AddModelScreen(ModalScreen):
    CSS = f"""
    AddModelScreen {{
        align: center middle;
    }}
    #add-dialog {{
        width: 48;
        height: auto;
        max-height: 90;
        background: {C_BG};
        padding: 1 2;
    }}
    #add-title {{
        color: {C_FG};
        text-style: bold;
        padding: 0 0 1 0;
    }}
    #add-hint {{
        color: {C_DIM};
        padding: 0 0 1 0;
    }}
    #add-hint-footer {{
        color: {C_DIM};
        padding: 1 0 0 0;
    }}
    #add-input {{
        background: {C_BG};
        color: {C_FG};
        height: 3;
    }}
    #add-input:focus {{
    }}
    #add-input .input--cursor {{
        background: {C_FG};
        color: {C_BG};
    }}
    .add-field {{
        background: {C_BG};
        color: {C_FG};
        border: none;
        border-bottom: solid {C_BORDER};
        height: 3;
        margin: 0 0 1 0;
    }}
    .add-field:focus {{
        border-bottom: solid {C_FG};
    }}
    """

    BINDINGS = [
        Binding("escape", "close_add", "关闭", show=False),
    ]

    FIELDS = [
        ("key", "模型标识（英文，如 mymodel）", ""),
        ("label", "显示名称（如 我的模型）", ""),
        ("base_url", "接口地址", ""),
        ("api_key", "密钥", ""),
        ("model", "模型标识符（如 gpt-4o）", ""),
    ]

    def __init__(self, prefill: dict = None):
        super().__init__()
        self.field_index = 0
        self.values = {f[0]: f[2] for f in self.FIELDS}
        self.prefill = prefill or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="add-dialog"):
            yield Static("添加自定义模型", id="add-title")
            yield Static("Tab 切换字段 · 回车确认 · Esc 取消", id="add-hint")
            for key, hint, default in self.FIELDS:
                prefill_val = self.prefill.get(key, default)
                yield Input(placeholder=hint, value=prefill_val, id=f"field-{key}", classes="add-field")
            # 【修复】此处原先也用 id="add-hint"，与上方第 95 行重复。
            # Textual 要求同一容器内 widget ID 唯一，重复会导致 compose 阶段
            # 抛 MountError("Tried to insert 2 widgets with the same ID")，
            # 即「添加自定义模型」对话框**从未能打开过**（每次点开即崩）。
            # 该缺陷自 0.1.x 时代就存在（搬运前的 tui_agent.py 第 10751/10755 行
            # 即为同一重复），与本次解耦无关，是搬运过程中被挂载测试发现的。
            # 现改为独立 ID 并复用 #add-hint 的样式，不改变任何可见文案。
            yield Static("回车确认添加 · Esc 取消", id="add-hint-footer")

    def on_mount(self) -> None:
        self.query_one(".add-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._collect_values()
        key = self.values.get("key", "").strip()
        if not key:
            return
        if key in MODEL_CONFIGS:
            self.dismiss({"action": "error", "msg": f"标识 '{key}' 已存在"})
            return
        if not self.values.get("base_url", "").strip() or not self.values.get("model", "").strip():
            self.dismiss({"action": "error", "msg": "接口地址和模型标识符不能为空"})
            return
        self.dismiss({"action": "add_model", "values": dict(self.values)})

    def _collect_values(self):
        for key, _, _ in self.FIELDS:
            try:
                inp = self.query_one(f"#field-{key}", Input)
                self.values[key] = inp.value
            except Exception:
                pass

    def action_close_add(self):
        self._collect_values()
        self.dismiss(None)

class SettingsScreen(ModalScreen):
    """设置面板（模态对话框）- 按 Ctrl+P 打开，ESC 关闭"""

    RESULT_QUIT = "__quit__"

    CSS = f"""
    SettingsScreen {{
        align: center middle;
    }}
    #settings-dialog {{
        width: 48;
        height: auto;
        max-height: 90;
        background: {C_BG};
        padding: 1 2;
    }}
    #settings-title {{
        color: {C_FG};
        text-style: bold;
        padding: 0 0 1 0;
    }}
    .settings-section {{
        color: {C_DIM};
        text-style: bold;
        padding: 1 0 0 0;
    }}
    #settings-hint {{
        color: {C_DIM};
        padding: 0 0 1 0;
    }}
    ListView {{
        background: {C_BG};
        color: {C_FG};
        height: auto;
        max-height: 24;
    }}
    ListView > ListItem {{
        color: {C_FG};
        padding: 0 1;
    }}
    ListView > ListItem:hover {{
        background: {C_BORDER};
    }}
    ListView > ListItem.--highlight {{
        background: {C_BORDER};
        text-style: bold;
    }}
    #settings-footer {{
        color: {C_DIM};
        padding: 1 0 0 0;
    }}
    """

    BINDINGS = [
        Binding("escape", "close_settings", "关闭", show=False),
    ]

    def __init__(self, model_key: str, temperature: float, stream_enabled: bool,
                 max_turns: int, context_limit: int, work_mode: str = "expert"):
        super().__init__()
        self.model_key = model_key
        self.temperature = temperature
        self.stream_enabled = stream_enabled
        self.max_turns = max_turns
        self.context_limit = context_limit
        self.work_mode = work_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("设置", id="settings-title")
            yield Static("↑↓ 移动 · 回车选择 · Esc 关闭", id="settings-hint")

            # ── 工作模式 ──
            yield Static("工作模式", classes="settings-section")
            mode_items = []
            mode_labels = {"expert": "专家模式（自动路由）", "hybrid": "混合思考（多专家协作）", "manual": "手动模式（指定模型）"}
            for mk, ml in mode_labels.items():
                tag = " ●" if mk == self.work_mode else ""
                mode_items.append(ListItem(
                    Label(f"  {ml}  [{mk}]{tag}"), name=f"mode:{mk}"))
            yield ListView(*mode_items, id="mode-list")

            # ── 专家团队 ──
            yield Static("专家团队", classes="settings-section")
            expert_items = []
            for ek, ec in EXPERT_TEAM.items():
                # 仅显示角色（去掉·后面的模型名）和职能描述
                role = ec['label'].split('·')[0]
                expert_items.append(ListItem(
                    Label(f"  {role}  —  {ec['desc']}"), name=f"expert:{ek}"))
            yield ListView(*expert_items, id="expert-list")

            # ── 内置模型（手动模式用） ──
            yield Static("内置模型", classes="settings-section")
            builtin_keys = ("glm", "glm-v", "openrouter", "ollama")
            # 汇总项：显示已内置免费模型数量（不显示具体模型名称）
            builtin_ready = sum(1 for k in builtin_keys if MODEL_CONFIGS.get(k, {}).get("api_key"))
            builtin_total = len(builtin_keys)
            summary_item = ListItem(
                Label(f"  ✓ 已内置免费模型（{builtin_ready}/{builtin_total}）"),
                name="builtin_summary",
                disabled=True,
            )
            yield ListView(summary_item, id="builtin-model-list")

            # ── 自定义模型 ──
            yield Static("自定义模型", classes="settings-section")
            custom_items = []
            for key, cfg in MODEL_CONFIGS.items():
                if key in builtin_keys:
                    continue
                tag = " ●" if key == self.model_key else ""
                # 显示 Key 状态
                has_key = bool(cfg.get("api_key", ""))
                key_status = "✓" if has_key else "未配置"
                custom_items.append(ListItem(
                    Label(f"  {cfg['label']}  [{key}]{tag}  {key_status}"), name=f"model:{key}"))
            custom_items.append(ListItem(Label("  添加自定义模型…"), name="add_model"))
            custom_items.append(ListItem(Label("  扫描本地模型"), name="scan_ollama"))
            yield ListView(*custom_items, id="custom-model-list")

            # ── 参数 ──
            yield Static("参数", classes="settings-section")
            param_items = [
                ListItem(Label(f"  温度：{self.temperature}  （越低越稳定，越高越随机）"),
                        name=f"temperature:{self.temperature}"),
                ListItem(Label(f"  流式输出：{'开' if self.stream_enabled else '关'}"),
                        name=f"stream:{self.stream_enabled}"),
                ListItem(Label(f"  最大调用轮次：{self.max_turns}"),
                        name=f"max_turns:{self.max_turns}"),
                ListItem(Label(f"  上下文长度：{self.context_limit}"),
                        name=f"context_limit:{self.context_limit}"),
            ]
            yield ListView(*param_items, id="param-list")

            # ── 其他 ──
            yield Static("其他", classes="settings-section")
            other_items = [
                ListItem(Label("  删除自定义模型"), name="remove_model"),
                ListItem(Label("  ℹ 关于 ZeroAI"), name="about"),
            ]
            yield ListView(*other_items, id="other-list")

            # ── 代理服务器（v1.1.0 新增）──
            yield Static("代理服务器", classes="settings-section")
            proxy_items = []
            proxy_status = "已启用" if _is_proxy_enabled() else "未启用"
            proxy_url_display = PROXY_CONFIG.get("base_url", "") or "未配置"
            if len(proxy_url_display) > 30:
                proxy_url_display = proxy_url_display[:27] + "..."
            proxy_items.append(ListItem(
                Label(f"  代理模式：{proxy_status}"),
                name="proxy_toggle",
            ))
            proxy_items.append(ListItem(
                Label(f"  代理地址：{proxy_url_display}"),
                name="proxy_url",
            ))
            proxy_items.append(ListItem(
                Label(f"  访问 Token：{'已配置' if PROXY_CONFIG.get('token') else '未配置'}"),
                name="proxy_token",
            ))
            yield ListView(*proxy_items, id="proxy-list")

            yield Static("Esc 关闭", id="settings-footer")

    def action_close_settings(self):
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = event.item.name
        if name.startswith("mode:"):
            mode = name.split(":", 1)[1]
            self.dismiss({"action": "switch_mode", "mode": mode})
        elif name.startswith("expert:"):
            ek = name.split(":", 1)[1]
            expert = EXPERT_TEAM[ek]
            self.dismiss({"action": "expert_info", "key": ek})
        elif name.startswith("model:"):
            key = name.split(":", 1)[1]
            self.dismiss({"action": "switch_model", "key": key})
        elif name.startswith("temperature:"):
            cur = float(name.split(":", 1)[1])
            # 循环切换：0.1 → 0.3 → 0.5 → 0.7 → 0.9 → 0.1
            temps = [0.1, 0.3, 0.5, 0.7, 0.9]
            idx = temps.index(cur) if cur in temps else 1
            next_temp = temps[(idx + 1) % len(temps)]
            self.dismiss({"action": "set_temperature", "value": next_temp})
        elif name.startswith("stream:"):
            cur = name.split(":", 1)[1] == "True"
            self.dismiss({"action": "set_stream", "value": not cur})
        elif name.startswith("max_turns:"):
            cur = int(name.split(":", 1)[1])
            opts = [3, 5, 8, 10, 15]
            idx = opts.index(cur) if cur in opts else 2
            next_val = opts[(idx + 1) % len(opts)]
            self.dismiss({"action": "set_max_turns", "value": next_val})
        elif name.startswith("context_limit:"):
            cur = int(name.split(":", 1)[1])
            opts = [2048, 4096, 8192, 16384, 32768]
            idx = opts.index(cur) if cur in opts else 2
            next_val = opts[(idx + 1) % len(opts)]
            self.dismiss({"action": "set_context_limit", "value": next_val})
        elif name == "add_model":
            self.dismiss({"action": "add_model"})
        elif name == "scan_ollama":
            self.dismiss({"action": "scan_ollama"})
        elif name == "remove_model":
            self.dismiss({"action": "remove_model"})
        elif name == "about":
            self.dismiss({"action": "about"})
        elif name == "proxy_toggle":
            self.dismiss({"action": "proxy_toggle"})
        elif name == "proxy_url":
            self.dismiss({"action": "proxy_url"})
        elif name == "proxy_token":
            self.dismiss({"action": "proxy_token"})


class VoiceDialogScreen(ModalScreen):
    """语音讨论助手（全屏沉浸式 Modal，对标手机端讨论助手）

    界面布局（参考用户提供截图）：
    ┌─────────────────────────────────┐
    │  00:09  关闭字幕  静音   ×     │  ← 顶部状态栏（计时/字幕/静音/关闭）
    ├─────────────────────────────────┤
    │                                │
    │           [气泡] 你是干什么的   │  ← 用户问题气泡（右对齐）
    │                                │
    │  [打字机] AI 正在回答…         │  ← AI 回答气泡（左对齐，逐字显示）
    │                                │
    │   三个点…                      │  ← 思考中动画
    │                                │
    ├─────────────────────────────────┤
    │           🎤 录音波形           │  ← 状态文字 + 提示
    │       可以继续说话来打断       │
    ├─────────────────────────────────┤
    │   🎤麦克风    [结束]    📎附件 │  ← 底部控制按钮
    └─────────────────────────────────┘

    功能：
    - 持续语音对话循环（说→识别→回答→朗读→再听）
    - 打字机效果显示 AI 回答
    - 打断机制（用户说话时自动停止 TTS/生成）
    - 字幕开关、静音开关
    """

    CSS = f"""
    VoiceDialogScreen {{
        background: {C_BG};
        layout: vertical;
    }}

    #vd-root {{
        width: 100%;
        height: 100%;
        background: {C_BG};
        layout: vertical;
    }}

    /* 顶部状态栏 */
    #vd-top {{
        height: 1;
        background: {C_BG2};
        padding: 0 2;
        layout: horizontal;
        border-bottom: solid {C_BORDER};
    }}

    #vd-top-spacer {{
        width: 1fr;
        height: 1;
    }}

    #vd-timer, #vd-subtitle-btn {{
        width: auto;
        height: 1;
        color: {C_DIM};
    }}

    #vd-close-btn {{
        width: auto;
        height: 1;
        color: {C_RED};
        text-style: bold;
    }}

    /* 字幕区 */
    #vd-subtitle-bar {{
        height: 3;
        background: {C_BG2};
        color: {C_FG};
        padding: 0 2;
        text-style: bold;
        border-bottom: solid {C_BORDER};
        content-align: left middle;
    }}

    #vd-subtitle-bar.active {{
        color: {C_ACCENT};
        text-style: bold;
    }}

    #vd-subtitle-bar.thinking {{
        color: {C_YELLOW};
        text-style: bold italic;
    }}

    #vd-subtitle-bar.speaking {{
        color: {C_GREEN};
        text-style: bold;
    }}

    #vd-subtitle-bar.error {{
        color: {C_RED};
        text-style: bold;
    }}

    /* 对话内容区 */
    #vd-content {{
        height: 1fr;
        background: {C_BG};
        padding: 1 2;
        overflow-y: auto;
        scrollbar-color: {C_ACCENT};
        scrollbar-background: {C_BORDER};
        scrollbar-size-vertical: 1;
    }}

    .vd-row-user {{
        width: 100%;
        height: auto;
        padding: 0 0 1 0;
        layout: horizontal;
    }}

    .vd-row-ai {{
        width: 100%;
        height: auto;
        padding: 0 0 1 0;
        layout: horizontal;
    }}

    .vd-log-user {{
        background: {C_USER_BUBBLE};
        color: {C_FG};
        width: auto;
        max-width: 75%;
        height: auto;
        padding: 0 1;
        border: solid {C_ACCENT};
    }}

    .vd-log-ai {{
        background: {C_AI_BUBBLE};
        color: {C_FG};
        width: 100%;
        max-width: 100%;
        height: auto;
        padding: 0 1;
        border-left: solid {C_GREEN};
    }}

    .vd-thinking {{
        color: {C_DIM};
        text-align: center;
        width: 100%;
        text-style: italic;
    }}

    /* 打字输入框（默认隐藏） */
    #vd-text-input {{
        height: 3;
        margin: 0 2;
        background: {C_BG2};
        color: {C_FG};
        border: solid {C_ACCENT};
        display: none;
    }}

    #vd-text-input.visible {{
        display: block;
    }}

    /* 状态提示 */
    #vd-status {{
        height: 2;
        background: {C_BG2};
        padding: 0 2;
        layout: vertical;
        border-top: solid {C_BORDER};
    }}

    #vd-status-dot {{
        width: auto;
        height: 1;
        color: {C_ACCENT};
        text-style: bold;
    }}

    #vd-status-hint {{
        width: auto;
        height: 1;
        color: {C_DIM};
    }}

    /* 底部控制按钮 */
    #vd-controls {{
        height: 5;
        background: {C_BG2};
        layout: horizontal;
        align: center middle;
        padding: 1 2;
        border-top: solid {C_BORDER};
    }}

    /* 侧按钮（说话/附件）— 用类选择器 */
    .vd-btn-side {{
        width: 10;
        height: 3;
        background: {C_BG};
        color: {C_DIM};
        border: solid {C_BORDER};
        content-align: center middle;
        margin: 0 4;
    }}

    /* 左侧"说话"按钮录音中状态：红色高亮，提示用户正在录音 */
    #vd-btn-mute.recording {{
        background: {C_RED};
        color: {C_BG};
        border: solid {C_RED};
        text-style: bold;
    }}

    /* 中央麦克风按钮 */
    #vd-btn-mic {{
        width: 8;
        height: 3;
        background: {C_BG};
        color: {C_ACCENT};
        border: solid {C_ACCENT};
        content-align: center middle;
        margin: 0 4;
        text-style: bold;
    }}

    #vd-btn-mic.listening {{
        background: {C_ACCENT};
        color: {C_BG};
        border: solid {C_ACCENT};
    }}

    #vd-btn-mic.thinking {{
        background: {C_BG};
        color: {C_YELLOW};
        border: solid {C_YELLOW};
    }}

    #vd-btn-mic.speaking {{
        background: {C_BG};
        color: {C_GREEN};
        border: solid {C_GREEN};
    }}
    """

    BINDINGS = [
        Binding("escape", "close_dialog", "关闭", show=False),
        Binding("ctrl+d", "close_dialog", "关闭", show=False),
        Binding("ctrl+t", "manual_input", "打字输入", show=False),
        Binding("ctrl+s", "export_dialog", "导出对话", show=False),
    ]

    def __init__(self, app_instance):
        super().__init__()
        self.app_instance = app_instance  # ZeroAI 主 App 引用
        self._dialog_active = True  # 对话循环开关
        self._is_listening = False  # 是否正在录音
        self._is_generating = False  # 是否正在生成/朗读
        self._is_speaking = False  # TTS 朗读中
        self._turn_done = True  # 单轮完成标志
        self._subtitle_enabled = True  # 字幕开关
        self._mute_enabled = False  # 静音开关
        self._manual_mode = True  # 手动录音模式（默认开启：点左侧按钮才录音，不自动循环）
        self._manual_listening = False  # 手动录音中（True=正在录音，等待用户点停止）
        self._manual_audio_buf = None  # 手动录音音频缓冲
        self._manual_rec_thread = None  # 手动录音线程
        self._current_user_text = ""  # 当前用户提问
        self._current_ai_text = ""  # 当前 AI 回答（完整）
        self._current_ai_displayed = ""  # 当前 AI 回答（已显示）
        # 【修复 2026-09-16】此前这两个字段**从未初始化**，
        # _ai_text_buffer 仅靠 _update_ai_bubble 赋值。
        # 后果：上一轮的文本会残留到下一轮，导致新一轮 AI 气泡在首个
        # 流式片段到达前**回放上一轮的答案**（实测确证）。
        # 这里补上初始化，并在每轮开始时显式清空。
        self._ai_text_buffer = ""  # 本轮 AI 流式文本缓冲（挂载前到达的内容暂存于此）
        self._ai_log = None  # 本轮 AI 气泡的 RichLog（挂载完成后赋值）
        self._ai_thinking_log = None
        self._ai_was_interrupted = False
        self._ai_export_recorded = False
        self._dialog_thread = None
        self._start_time = None
        self._timer_handle = None
        self._last_was_interrupt = False  # 上一轮是否是打断
        self._text_input_active = False  # 打字输入模式开关
        # 麦克风按钮动画状态
        self._mic_visual_state = "idle"  # idle/listening/thinking/speaking
        self._anim_handle = None  # 动画定时器
        self._anim_frame = 0  # 动画帧计数
        # 附件图片（base64 data URI 列表，发送给多模态模型）
        self._dialog_pending_images = []  # 等待下次发送的图片
        # 字幕区当前显示文本（最近一次识别/输入的原文）
        self._subtitle_text = ""
        # 字幕状态（listening/recognized/thinking/speaking/error/""）
        self._subtitle_state = ""
        # 长按检测（静音按钮）
        self._mute_mouse_down_at = None  # mouse_down 时间戳
        self._mute_long_press_threshold = 0.6  # 600ms 视为长按
        # 对话导出缓存（Markdown 文本，按顺序累积）
        self._export_lines = []  # [(role, text), ...]
        # 当前对话开始时间（用于导出文件名）
        self._session_started_at = time.time()
        # 音色菜单打开标志
        self._voice_menu_open = False
        # 附件输入弹层标志
        self._attach_prompt_open = False

    def compose(self) -> ComposeResult:
        with Vertical(id="vd-root"):
            # 顶部状态栏
            with Horizontal(id="vd-top"):
                yield Static("00:00", id="vd-timer")
                yield Static("", id="vd-top-spacer")
                yield Static("字幕", id="vd-subtitle-btn")
                yield Static("  ×", id="vd-close-btn")
            # 字幕区（最近一次识别的用户原文，1 行固定条）
            yield Static("", id="vd-subtitle-bar")
            # 对话内容区（支持向上滚动查看历史）
            yield VerticalScroll(id="vd-content")
            # 打字输入框（默认隐藏，Ctrl+T 显示）
            yield Input(placeholder="打字输入问题，回车发送", id="vd-text-input")
            # 状态提示（点 + 提示文字，两行）
            with Vertical(id="vd-status"):
                yield Static("", id="vd-status-dot")
                yield Static("", id="vd-status-hint")
            # 底部控制按钮（仿讨论助手：左 静音 / 中 大圆麦克风 / 右 附件）
            with Horizontal(id="vd-controls"):
                yield Static("说话", id="vd-btn-mute", classes="vd-btn-side")
                yield Static("🎤", id="vd-btn-mic")
                yield Static("附件", id="vd-btn-attach", classes="vd-btn-side")

    def on_mount(self) -> None:
        """挂载时启动对话循环"""
        self._start_time = time.time()
        # 启动计时器
        self._timer_handle = self.set_interval(1.0, self._tick_timer)
        # 启动麦克风按钮波纹动画（200ms 一帧，节奏更顺）
        self._anim_handle = self.set_interval(0.2, self._tick_animation)
        # 显示欢迎引导
        self._show_welcome_guide()
        # 手动模式：不自动启动录音循环，等待用户点"说话"按钮
        if self._manual_mode:
            self._update_status("idle", "点左侧'说话'按钮开始录音")
            # 延迟 100ms 设置字幕，确保 UI 完全渲染后再更新
            self.set_timer(0.1, lambda: self._set_subtitle("🎤 点左侧「说话」按钮开始录音", "listening"))
        else:
            # 自动模式：启动语音对话循环
            import threading
            self._dialog_thread = threading.Thread(target=self._dialog_loop, daemon=True)
            self._dialog_thread.start()
            self._update_status("listening", "请说话提问，或 Ctrl+T 打字")

    def _show_welcome_guide(self) -> None:
        """显示使用引导（首次进入时，手动录音模式）"""
        try:
            content = self.query_one("#vd-content", VerticalScroll)
            guide = Static(
                "  ┌─────────────────────────────────────┐\n"
                "  │  🎤 语音对话已就绪（手动模式）      │\n"
                "  ├─────────────────────────────────────┤\n"
                "  │  🗣  点左下「说话」开始录音         │\n"
                "  │  ⏹   再点「停止」结束录音并识别    │\n"
                "  │  🔊  AI 会语音回复并显示字幕        │\n"
                "  │  ⌨   Ctrl+T 切换打字输入           │\n"
                "  │  💾  Ctrl+S 导出对话记录           │\n"
                "  │  📎  右下按钮：添加图片提问         │\n"
                "  │  ✕   顶部或 Esc：退出              │\n"
                "  └─────────────────────────────────────┘",
                classes="vd-thinking",
            )
            content.mount(guide)
            content.scroll_end(animate=False)
        except Exception:
            pass

    def on_unmount(self) -> None:
        """卸载时清理"""
        self._dialog_active = False
        self._manual_listening = False
        self._is_speaking = False
        self._is_generating = False
        self._is_listening = False
        if self._timer_handle:
            try:
                self._timer_handle.stop()
            except Exception:
                pass
        if self._anim_handle:
            try:
                self._anim_handle.stop()
            except Exception:
                pass
        # 停止 AI 生成
        try:
            self.app_instance._stop_generation = True
        except Exception:
            pass
        # 停止 TTS 播放
        try:
            import pygame
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass

    def _tick_timer(self) -> None:
        """更新顶部计时器"""
        if self._start_time is None:
            return
        elapsed = int(time.time() - self._start_time)
        mins = elapsed // 60
        secs = elapsed % 60
        try:
            timer = self.query_one("#vd-timer", Static)
            timer.update(f"{mins:02d}:{secs:02d}")
        except Exception:
            pass

    def _update_status(self, state: str, text: str = "") -> None:
        """更新状态提示

        state: listening(录音中) | thinking(思考中) | speaking(朗读中) | idle(空闲)
        """
        icons = {
            "listening": "🎤",
            "thinking": "💭",
            "speaking": "🔊",
            "idle": "⏸",
        }
        icon = icons.get(state, "●")
        # 中间动态点的动画
        dot_anim = "." * ((int(time.time() * 2) % 3) + 1)
        try:
            self.query_one("#vd-status-dot", Static).update(f"{icon} {dot_anim}")
            if text:
                self.query_one("#vd-status-hint", Static).update(text)
            else:
                default_text = {
                    "listening": "说点什么",
                    "thinking": "AI 正在思考",
                    "speaking": "AI 正在回答（说话可打断）",
                    "idle": "等待中",
                }
                self.query_one("#vd-status-hint", Static).update(default_text.get(state, ""))
        except Exception:
            pass
        # 同步更新麦克风按钮的视觉状态（用于波纹动画）
        self._set_mic_state(state)

    def _set_mic_state(self, state: str) -> None:
        """设置麦克风按钮的视觉状态（添加/移除 CSS 类）"""
        if self._mic_visual_state == state:
            return  # 状态没变不重绘
        self._mic_visual_state = state
        try:
            mic = self.query_one("#vd-btn-mic", Static)
            for cls in ("listening", "thinking", "speaking"):
                mic.remove_class(cls)
            if state in ("listening", "thinking", "speaking"):
                mic.add_class(state)
            # 立即刷新一次按钮文字
            self._tick_animation()
        except Exception:
            pass

    def _tick_animation(self) -> None:
        """麦克风按钮波纹动画（每 200ms 刷新一帧）

        不同状态显示不同动画：
        - idle:       🎤
        - listening:  · 🎤 ·  →  ·· 🎤 ··  →  ··· 🎤 ···  →  ·· 🎤 ·· （环状点波纹）
        - thinking:   💭 . → 💭 .. → 💭 ...
        - speaking:   🔊 ▁ → 🔊 ▂ → 🔊 ▃ → 🔊 ▄ → 🔊 ▅ → 🔊 ▆ （声波律动）
        """
        try:
            mic = self.query_one("#vd-btn-mic", Static)
        except Exception:
            return
        state = self._mic_visual_state
        # 取 8 帧循环
        self._anim_frame = (self._anim_frame + 1) % 8
        f = self._anim_frame
        if state == "listening":
            # 录音波纹：左右点环呼吸（4 帧一个循环）
            ring_frames = ["· 🎤 ·", "·· 🎤 ··", "··🎤··", "·🎤·", "· 🎤 ·", "·· 🎤 ··", "··🎤··", "·🎤·"]
            text = ring_frames[f % len(ring_frames)]
        elif state == "thinking":
            # 思考动画：点逐步累积
            dot_frames = ["💭 .", "💭 ..", "💭 ...", "💭 ..", "💭 .", "💭", "💭 .", "💭 .."]
            text = dot_frames[f % len(dot_frames)]
        elif state == "speaking":
            # 朗读声波律动（5 帧循环）
            wave_frames = ["🔊 ▁", "🔊 ▂", "🔊 ▃", "🔊 ▄", "🔊 ▅", "🔊 ▆", "🔊 ▅", "🔊 ▄"]
            text = wave_frames[f % len(wave_frames)]
        else:
            # idle
            text = "🎤"
        try:
            mic.update(text)
        except Exception:
            pass

    # ═══ 字幕区 ═══
    def _set_subtitle(self, text: str, state: str = "") -> None:
        """设置字幕区显示文字（带状态色和图标）

        Args:
            text: 显示文字（空字符串则清空）
            state: 状态标识，控制字幕颜色和图标
                "" - 默认（白色）
                "listening" - 正在听（蓝色高亮 + 🎙）
                "recognized" - 已识别（白色 + 💬）
                "thinking" - AI 思考中（黄色斜体 + 💭）
                "speaking" - AI 朗读中（绿色 + 🔊）
                "error" - 错误（红色 + ⚠️）
        """
        self._subtitle_text = text or ""
        self._subtitle_state = state
        try:
            bar = self.query_one("#vd-subtitle-bar", Static)
            # 清除所有状态类
            for cls in ("active", "thinking", "speaking", "error"):
                bar.remove_class(cls)
            # 根据状态添加类
            if state == "listening":
                bar.add_class("active")
            elif state == "thinking":
                bar.add_class("thinking")
            elif state == "speaking":
                bar.add_class("speaking")
            elif state == "error":
                bar.add_class("error")
            # 截断过长字幕（3 行显示，允许更多文字）
            display = self._subtitle_text
            if len(display) > 200:
                display = display[:199] + "…"
            # 根据状态显示不同图标
            icon_map = {
                "listening": "🎙",
                "recognized": "💬",
                "thinking": "💭",
                "speaking": "🔊",
                "error": "⚠️",
            }
            icon = icon_map.get(state, "")
            if display:
                bar.update(f"{icon} {display}" if icon else display)
            else:
                bar.update("")
        except Exception:
            pass

    # ═══ 附件：图片加入待发送队列 ═══
    def _add_pending_image(self, image_path: str) -> tuple[bool, str]:
        """把图片加入 _dialog_pending_images 队列

        Returns:
            (success, message)
        """
        try:
            b64 = read_image(image_path)
            if not b64.startswith("data:"):
                return False, b64  # 错误信息
            self._dialog_pending_images.append(b64)
            # 累积到导出行
            self._export_lines.append(("system", f"[已附加图片 {Path(image_path).name}]"))
            # 在内容区显示提示气泡
            idx = len(self._dialog_pending_images)
            try:
                self._append_system_bubble(f"📎 [Image {idx}] 已就绪：{Path(image_path).name}（下次提问时附带）")
            except Exception:
                pass
            return True, f"已附加 {idx} 张图片"
        except Exception as e:
            return False, f"附件失败：{e}"

    def _get_pending_images(self) -> list:
        """获取并清空待发送图片队列"""
        imgs = list(self._dialog_pending_images)
        self._dialog_pending_images = []
        return imgs

    # ═══ 附件：路径输入（复用 vd-text-input，避免动态 Container） ═══
    def action_attach_image(self) -> None:
        """附件按钮：复用打字输入框收集图片路径

        策略：把输入框 placeholder 改为图片路径提示，
        用户输入路径回车后，on_input_submitted 检测到 _attach_mode 标志，
        走图片加载逻辑而不是发送给 AI。
        """
        if self._attach_prompt_open:
            return
        self._attach_prompt_open = True
        try:
            inp = self.query_one("#vd-text-input", Input)
            inp.placeholder = "📎 输入图片路径后回车（Esc 取消）"
            inp.add_class("visible")
            inp.value = ""
            inp.focus()
            self._text_input_active = True
            self._is_listening = False
            self._update_status("idle", "附件模式：输入图片路径，回车确认")
        except Exception as e:
            self._append_system_bubble(f"⚠️ 打开附件失败：{e}")
            self._attach_prompt_open = False

    # ═══ 音色菜单（循环切换，避免动态 Container） ═══
    VOICE_OPTIONS = [
        ("zh-CN-XiaoxiaoNeural", "女声·晓晓（默认·温柔）"),
        ("zh-CN-YunxiNeural", "男声·云希（清爽）"),
        ("zh-CN-YunjianNeural", "男声·云健（新闻）"),
        ("zh-CN-XiaoyiNeural", "女声·晓伊（活力）"),
        ("zh-CN-YunyangNeural", "男声·云扬（播音）"),
    ]

    def action_open_voice_menu(self) -> None:
        """长按静音按钮 → 循环切换音色（不弹 Container，直接切换并提示）"""
        try:
            current = self.app_instance._tts_voice
        except Exception:
            current = "zh-CN-XiaoxiaoNeural"
        # 找到当前音色在列表中的位置
        voice_ids = [vid for vid, _ in self.VOICE_OPTIONS]
        try:
            idx = voice_ids.index(current)
            next_idx = (idx + 1) % len(voice_ids)
        except ValueError:
            next_idx = 0
        next_id, next_label = self.VOICE_OPTIONS[next_idx]
        self._select_voice(next_id)

    def _select_voice(self, voice_id: str) -> None:
        """切换音色并更新提示"""
        try:
            self.app_instance._tts_voice = voice_id
        except Exception:
            pass
        label = next((lbl for vid, lbl in self.VOICE_OPTIONS if vid == voice_id), voice_id)
        self._append_system_bubble(f"🎤 音色已切换：{label}")

    def _close_voice_menu(self) -> None:
        """兼容旧调用（音色菜单已改为循环切换，无需关闭）"""
        self._voice_menu_open = False

    # ═══ 静音按钮长按检测 ═══
    def on_mouse_down(self, event) -> None:
        """记录静音按钮 mouse_down 时间戳（用于长按检测）"""
        try:
            widget = event.widget
            if widget is not None and getattr(widget, "id", None) == "vd-btn-mute":
                self._mute_mouse_down_at = time.time()
        except Exception:
            pass

    def on_mouse_up(self, event) -> None:
        """mouse_up 时判断是单击还是长按"""
        try:
            widget = event.widget
            if widget is None or getattr(widget, "id", None) != "vd-btn-mute":
                return
            if self._mute_mouse_down_at is None:
                return
            held = time.time() - self._mute_mouse_down_at
            self._mute_mouse_down_at = None
            if held >= self._mute_long_press_threshold:
                # 长按 → 弹出音色菜单（拦截 on_click 中的单击切换）
                self._suppress_next_mute_click = True
                self.action_open_voice_menu()
            else:
                # 短按 → 允许 on_click 处理（切换静音）
                self._suppress_next_mute_click = False
        except Exception:
            pass

    def _append_user_bubble(self, text: str) -> None:
        """添加用户问题气泡（右对齐，RichLog 支持 Markdown）

        【缺陷修复 2026-09-16】原实现先 `row.mount(log)` 再 `content.mount(row)`。
        Textual 的 Widget.mount() 明确要求父节点已挂载：

            if not self.is_attached:
                raise MountError(f"Can't mount widget(s) before {self!r} is mounted")

        于是 row.mount(log) 必然抛 MountError；而整个方法体被
        `except Exception: pass` 包住，异常被静默吞掉 ——
        **用户气泡从未渲染出来过**（实测调用后 #vd-content 内 .vd-row-user = 0）。

        误区记录：仅把两行调换顺序（content.mount(row) 放前面）**同样失败**，
        因为 row 虽已提交挂载，却尚未真正 attach，实测仍是同一个 MountError。
        必须等 row 挂载完成后再挂 log。

        本方法保持同步签名（调用方大量使用 call_from_thread / call_later），
        因此采用 call_next 延后一帧挂子节点：row 先进入已挂载的 content，
        下一轮消息循环时 row 已 attached，此时再 row.mount(log) 即合法。
        """
        try:
            content = self.query_one("#vd-content", VerticalScroll)
            row = Horizontal(classes="vd-row-user")
            content.mount(row)  # ① 父先挂到已挂载的 content 上
            log = RichLog(
                highlight=True,
                markup=True,
                max_lines=200,
                wrap=True,
                classes="vd-log-user",
            )
            # ② 等 row 真正挂载后再挂 log（父 → 子，不可颠倒）
            self.call_next(lambda: self._mount_bubble_log(row, log, "user", text))
        except Exception:
            pass

    def _ai_header_text(self) -> str:
        """AI 气泡标题行文本（「┌─ AI · 模型名」）。

        【修复 2026-09-16】抽成方法是因为标题现在要在**两个**地方写入：
          1. 气泡挂载时（_mount_bubble_log）
          2. 每次流式更新时（_update_ai_bubble 会先 clear() 再写，
             而 clear() 会把标题一起清掉 —— 实测首帧后标题消失，
             而用户气泡的「┌─ 你」却保留，造成左右不对称）
        两处必须用同一份文本，否则会漂移。
        """
        try:
            model_label = self.app_instance.model_key
        except Exception:
            model_label = "AI"
        return f"┌─ AI · {model_label}"

    def _mount_bubble_log(self, row, log, kind: str, text: str = "") -> None:
        """把 log 挂到已挂载的 row 上并写入内容（由 call_next 调用）。

        kind: "user" | "ai"

        参数 text：kind=="user" 时为用户原文；kind=="ai" 时忽略
        （AI 内容统一从 self._ai_text_buffer 取，以兼容挂载前已到达的流式片段）。
        """
        try:
            row.mount(log)
            from rich.text import Text as RichText
            from rich.markdown import Markdown as RichMarkdown

            if kind == "user":
                log.write(RichText("┌─ 你", style=f"bold {C_ACCENT}"))
                try:
                    log.write(RichMarkdown(text))
                except Exception:
                    log.write(text)
                self._export_lines.append(("user", text))
            else:
                # 先写标题行，再回放缓冲区中已到达的流式内容
                log.write(RichText(self._ai_header_text(), style=f"bold {C_GREEN}"))
                buffered = getattr(self, "_ai_text_buffer", "") or ""
                if buffered.strip():
                    try:
                        log.write(RichMarkdown(buffered))
                    except Exception:
                        log.write(buffered)
                # 暴露给流式更新使用；设置状态标记
                self._ai_log = log
                self._ai_thinking_log = log  # 思考中也用这个 log
                self._ai_was_interrupted = False  # 标记本轮是否被打断
                self._ai_export_recorded = False  # 标记是否已记录到 export
            content = self.query_one("#vd-content", VerticalScroll)
            content.scroll_end(animate=False)
        except Exception:
            pass

    def _append_ai_placeholder(self) -> None:
        """添加 AI 回答占位气泡（RichLog 流式渲染）

        【缺陷修复 2026-09-16】同 _append_user_bubble：原先 row 未挂载就
        row.mount(log)，抛 MountError 后被 except 吞掉，
        AI 气泡同样从未渲染（实测调用后 .vd-log-ai = 0）。

        【追加修复 2026-09-16】必须在**每轮开始时同步清空** _ai_text_buffer，
        否则上一轮残留的文本会在本轮气泡挂载时被回放（跨轮内容泄漏）。
        注意要同步清，不能放到 call_next 回调里 —— 否则同帧到达的首个
        流式片段会先写进缓冲区、又被回调清掉。
        """
        try:
            # ① 本轮开始：清空上轮残留状态（必须同步，先于任何挂载）
            self._ai_text_buffer = ""
            self._ai_log = None
            self._ai_thinking_log = None
            self._ai_was_interrupted = False
            self._ai_export_recorded = False

            content = self.query_one("#vd-content", VerticalScroll)
            row = Horizontal(classes="vd-row-ai")
            content.mount(row)  # ② 父先挂
            log = RichLog(
                highlight=True,
                markup=True,
                max_lines=500,
                wrap=True,
                classes="vd-log-ai",
            )
            # ③ 等 row 挂载后再挂 log
            self.call_next(lambda: self._mount_bubble_log(row, log, "ai"))
        except Exception:
            pass

    def _update_ai_bubble(self, text: str) -> None:
        """更新 AI 气泡内容（流式 Markdown 打字机效果）

        【竞态加固 2026-09-16】气泡的 log 是延后一帧才挂载并赋值给
        self._ai_log 的（见 _append_ai_placeholder）。若第一段流式内容
        在挂载完成前就到达，旧写法会命中 `if self._ai_log is None: return`
        而**静默丢弃**该段内容（对抗性测试 ATK-3 实测复现）。

        现在无论 log 是否就绪，都先把文本记进 _ai_text_buffer；
        log 未就绪时直接返回，待 _mount_bubble_log 挂载后用缓冲区回放。
        这样正确性与"哪一帧到达"解耦。

        【追加修复 2026-09-16】clear() 会把标题行一起清掉，因此清空后
        必须重新写入标题（用 _ai_header_text()），否则首个流式片段到达后
        AI 气泡就丢了「┌─ AI · …」，而用户气泡的「┌─ 你」仍在 —— 左右不对称。
        """
        try:
            self._ai_text_buffer = text  # 先记录，保证不丢
            if not hasattr(self, "_ai_log") or self._ai_log is None:
                return  # 尚未挂载：内容已进缓冲区，挂载时回放
            # 清空 log 后重新写入（保证 Markdown 解析正确）
            self._ai_log.clear()
            from rich.text import Text as RichText
            # 标题行必须补回（clear 已将其清除）
            self._ai_log.write(RichText(self._ai_header_text(), style=f"bold {C_GREEN}"))
            try:
                from rich.markdown import Markdown as RichMarkdown
                self._ai_log.write(RichMarkdown(text))
            except Exception:
                self._ai_log.write(text)
            # 自动滚动到底部（如果用户没手动上滚查看历史）
            content = self.query_one("#vd-content", VerticalScroll)
            # 仅当滚动条已经在底部时才自动滚动，避免打扰用户查看历史
            if content.scroll_y >= content.max_scroll_y - 2:
                content.scroll_end(animate=False)
        except Exception:
            pass

    def _mark_ai_interrupted(self) -> None:
        """在 AI 气泡末尾追加 '⏸ 已打断' 标记（保留已显示内容）"""
        try:
            if not hasattr(self, "_ai_log") or self._ai_log is None:
                return
            self._ai_was_interrupted = True
            # 在已显示内容下方追加一行提示（保持 RichLog wrap）
            try:
                from rich.text import Text
                from rich.markdown import Markdown as RichMarkdown
                # 写一行 dim 标记
                self._ai_log.write(Text("  ⏸ 已打断", style="dim italic"))
            except Exception:
                try:
                    self._ai_log.write("  ⏸ 已打断")
                except Exception:
                    pass
            # 记录到 export
            partial = (self._ai_text_buffer or "").strip()
            if partial and not getattr(self, "_ai_export_recorded", False):
                self._export_lines.append(("assistant", partial + "  _(⏸ 已打断)_"))
                self._ai_export_recorded = True
        except Exception:
            pass

    def _append_thinking_indicator(self) -> None:
        """在 AI 气泡开头显示思考动画"""
        try:
            if not hasattr(self, "_ai_log") or self._ai_log is None:
                return
            # 在 log 顶部写入"思考中..."
            self._ai_log.write("[dim]💭 思考中...[/dim]")
        except Exception:
            pass

    def _finalize_ai_export(self) -> None:
        """AI 完整回答后记录到 export_lines（仅在未中断/未记录时）"""
        try:
            if getattr(self, "_ai_was_interrupted", False):
                return
            if getattr(self, "_ai_export_recorded", False):
                return
            text = (self._ai_text_buffer or "").strip()
            if text:
                self._export_lines.append(("assistant", text))
                self._ai_export_recorded = True
        except Exception:
            pass

    def _dialog_loop(self) -> None:
        """语音对话主循环（后台线程）

        状态机：
          IDLE（等待）→ LISTENING（录音中）→ RECOGNIZING（识别中）
            → THINKING（AI 思考）→ SPEAKING（朗读中）→ IDLE
          任何状态都可被"用户说话"打断回到 LISTENING
        """
        import time as _t
        import threading as _th
        # 连续无输入计数（用于提示用户"请说话"）
        empty_count = 0
        while self._dialog_active:
            try:
                # ═══ 1. 等待用户说话（录音） ═══
                self._is_listening = True
                self._current_ai_text = ""
                self._current_ai_displayed = ""
                # 显示"正在听..."提示气泡（带动画）
                self.app.call_from_thread(self._show_listening_indicator)
                self.app.call_from_thread(self._update_status, "listening", "请说话…")
                # 字幕区：提示用户正在听
                self.app.call_from_thread(self._set_subtitle, "正在听… 说吧", "listening")
                # 录音前短暂停顿（避免 TTS 回声）
                _t.sleep(0.1)
                if not self._dialog_active:
                    break
                # 录音 + 识别（改进版 VAD：动态阈值 + 前置静音过滤 + 归一化）
                try:
                    text = listen_asr(max_seconds=10, silence_seconds=1.0)
                except Exception as e:
                    text = f"错误：{e}"
                self._is_listening = False
                if not self._dialog_active:
                    break
                # 移除"正在听..."提示
                self.app.call_from_thread(self._hide_listening_indicator)
                # ═══ 2. 处理识别结果 ═══
                # 空输入或识别失败
                is_error = text.startswith("错误") or text.startswith("（未")
                is_empty = (not text.strip()) or text.strip() in ("（未识别到内容）", "（未录到声音）")
                if is_error or is_empty:
                    self.app.call_from_thread(self._set_subtitle, text.strip() or "（未识别）", "error")
                    empty_count += 1
                    if empty_count >= 3:
                        # 连续 3 次没听清，给出明显提示
                        self.app.call_from_thread(self._append_system_bubble, "🎤 连续未听清，请清晰说话，或按 Ctrl+T 打字输入")
                        empty_count = 0
                    else:
                        self.app.call_from_thread(self._append_system_bubble, "🎤 没听清，请再说一遍")
                    _t.sleep(0.3)
                    continue
                # 有效输入，重置计数 + 字幕区显示识别文本
                empty_count = 0
                self.app.call_from_thread(self._set_subtitle, text.strip(), "recognized")
                # 退出指令
                if text.strip() in ("停止", "退出", "结束对话", "退出对话", "stop", "结束", "关闭"):
                    self._dialog_active = False
                    self.app.call_from_thread(self._append_user_bubble, text)
                    self.app.call_from_thread(self._append_system_bubble, "👋 对话已结束")
                    _t.sleep(0.5)
                    self.app.call_from_thread(self.action_close_dialog)
                    return
                # ═══ 3. 显示用户气泡 + AI 占位 ═══
                self._current_user_text = text
                self.app.call_from_thread(self._append_user_bubble, text)
                # 取出待发送图片（仅当用户有附加时）
                pending_imgs = self._get_pending_images()
                if pending_imgs:
                    self.app.call_from_thread(self._append_system_bubble, f"📎 已附带 {len(pending_imgs)} 张图片")
                self.app.call_from_thread(self._append_ai_placeholder)
                self.app.call_from_thread(self._update_status, "thinking", "AI 正在思考…")
                self.app.call_from_thread(self._set_subtitle, "AI 正在思考…", "thinking")
                self._is_generating = True
                # ═══ 4. 启动 AI 流式生成（支持多模态图片） ═══
                ai_done_event = _th.Event()
                ai_result = {"text": "", "error": None}
                def _run_ai():
                    try:
                        result_text = self._generate_ai_stream(
                            text,
                            lambda chunk: self._on_ai_chunk(chunk),
                            images=pending_imgs,
                        )
                        ai_result["text"] = result_text
                    except Exception as e:
                        ai_result["error"] = str(e)
                    finally:
                        ai_done_event.set()
                ai_thread = _th.Thread(target=_run_ai, daemon=True)
                ai_thread.start()
                # 等待 AI 生成完成
                ai_done_event.wait()
                self._is_generating = False
                if ai_result["error"]:
                    self.app.call_from_thread(self._append_system_bubble, f"⚠️ 生成失败：{ai_result['error'][:50]}")
                    continue
                if not ai_result["text"].strip():
                    self.app.call_from_thread(self._append_system_bubble, "⚠️ AI 未返回内容")
                    continue
                # AI 完整生成成功 → 记录到 export
                self.app.call_from_thread(self._finalize_ai_export)
                # ═══ 5. TTS 朗读（可被用户说话打断） ═══
                if self._mute_enabled:
                    _t.sleep(0.2)
                    continue
                self._is_speaking = True
                self.app.call_from_thread(self._update_status, "speaking", "AI 正在回答（说话可打断）")
                # 字幕显示 AI 回答摘要
                ai_preview = (self._current_ai_text or "").strip().replace("\n", " ")
                if len(ai_preview) > 80:
                    ai_preview = ai_preview[:79] + "…"
                self.app.call_from_thread(self._set_subtitle, ai_preview, "speaking")
                # 在独立线程朗读（分段朗读 + 可打断）
                interrupt_event = _th.Event()
                self._current_interrupt = interrupt_event

                def _is_interrupted() -> bool:
                    """对话循环侧的中断检测：用户开始录音 / 对话已结束 → 停止朗读"""
                    if not self._dialog_active:
                        return True
                    if self._is_listening:
                        return True
                    return False

                def _do_speak():
                    try:
                        reply = self._current_ai_text
                        voice = self.app_instance._tts_voice
                        rate = self.app_instance._tts_rate
                        if reply.strip():
                            # 分段朗读（interrupt_check 回调用于在每段之间 / 段内打断）
                            speak_tts(
                                reply,
                                voice=voice,
                                rate=rate,
                                interrupt_check=_is_interrupted,
                                segment_max_chars=200,
                            )
                    except Exception:
                        pass
                    finally:
                        interrupt_event.set()
                speak_thread = _th.Thread(target=_do_speak, daemon=True)
                speak_thread.start()
                # 等待朗读完成或用户打断
                interrupted_by_user = False
                while not interrupt_event.is_set():
                    _t.sleep(0.2)
                    if not self._dialog_active:
                        # 对话结束 → 强制停止 TTS
                        try:
                            import pygame
                            pygame.mixer.music.stop()
                        except Exception:
                            pass
                        break
                    # 用户说话（_is_listening 变 True）→ 停止 TTS
                    if self._is_listening:
                        try:
                            import pygame
                            pygame.mixer.music.stop()
                        except Exception:
                            pass
                        self.app.call_from_thread(self._append_system_bubble, "⏹ 已打断，正在听你说…")
                        # 在 AI 气泡上追加"⏸ 已打断"标记（保留已显示内容）
                        self.app.call_from_thread(self._mark_ai_interrupted)
                        interrupted_by_user = True
                        break
                self._is_speaking = False
            except Exception as e:
                self.app.call_from_thread(self._append_system_bubble, f"⚠️ 循环异常：{e}")
                _t.sleep(0.3)
        # 循环结束
        self.app.call_from_thread(self._set_subtitle, "", "")
        self.app.call_from_thread(self._append_system_bubble, "对话已结束")

    def _show_listening_indicator(self) -> None:
        """显示'正在听...'动画提示气泡"""
        try:
            content = self.query_one("#vd-content", VerticalScroll)
            # 如果已存在则先移除
            try:
                old = self.query_one("#vd-listening-indicator", Static)
                old.remove()
            except Exception:
                pass
            # 动态点数（1-3 循环）
            dots = "." * ((int(time.time() * 2) % 3) + 1)
            indicator = Static(f"  🎤 正在听{dots}", id="vd-listening-indicator", classes="vd-thinking")
            content.mount(indicator)
            content.scroll_end(animate=False)
        except Exception:
            pass

    def _hide_listening_indicator(self) -> None:
        """移除'正在听...'提示气泡"""
        try:
            old = self.query_one("#vd-listening-indicator", Static)
            old.remove()
        except Exception:
            pass

    def _append_system_bubble(self, text: str) -> None:
        """添加系统提示气泡（居中灰色）"""
        try:
            content = self.query_one("#vd-content", VerticalScroll)
            line = Static(f"  {text}", classes="vd-thinking")
            content.mount(line)
            content.scroll_end(animate=False)
            # 累积到导出（跳过自动状态提示，避免污染 Markdown）
            if not text.startswith(("🎤 ", "🔇 ", "👋 ", "⏹ ", "💬 ")):
                # 仅记录真正的事件型提示（如"音色已切换"）
                if any(kw in text for kw in ("已切换", "已结束", "已打断", "导出", "错误", "失败")):
                    self._export_lines.append(("system", text.strip()))
        except Exception:
            pass

    def _on_ai_chunk(self, chunk: str) -> None:
        """AI 流式输出的回调（每收到一段文字就更新气泡和字幕）"""
        self._current_ai_text += chunk
        # 流式期间不过滤标签，避免 O(n²) 正则扫描累积文本；最终展示前统一过滤
        # 打字机效果：先记录完整文本，再通过定时器逐字显示
        # 简化：直接显示（Textual 内部已经是流式的，足够流畅）
        self.app.call_from_thread(self._update_ai_bubble, self._current_ai_text)
        # 字幕实时显示 AI 正在输出的文本（前 150 字符，避免过长）
        preview = self._current_ai_text.strip().replace("\n", " ")
        if len(preview) > 150:
            preview = preview[:149] + "…"
        if preview:
            self.app.call_from_thread(self._set_subtitle, preview, "thinking")

    def _generate_ai_stream(self, user_text: str, on_chunk, images: list = None) -> str:
        """同步生成 AI 回答（带流式回调）

        通过在 ZeroAI 主 App 中启动一个一次性任务，把每次 chunk 传给 on_chunk

        Args:
            user_text: 用户问题文本
            on_chunk: 流式 chunk 回调
            images: 可选，附加图片的 base64 data URI 列表
        """
        # 在主线程异步执行 _run_turn
        # 由于 _run_turn 是 async 协程，且在主事件循环中运行
        # 这里使用 run_worker 提交到主 App 的事件循环
        import threading
        result_holder = {"text": "", "done": False, "error": None}
        def _on_main():
            # 在主线程事件循环中执行
            import asyncio
            self.app_instance.run_worker(
                self._async_generate_ai(user_text, on_chunk, result_holder, images or []),
                exclusive=False,
            )
        # 由于 _generate_ai_stream 已在子线程，调度到主线程
        self.app_instance.call_from_thread(_on_main)
        # 等待完成
        while not result_holder["done"]:
            time.sleep(0.1)
        return result_holder.get("text", "")

    async def _async_generate_ai(self, user_text: str, on_chunk, result_holder, images: list = None):
        """在主 App 事件循环中执行 AI 生成（支持多模态图片）"""
        images = images or []
        try:
            import os
            from openai import OpenAI

            # 构造消息：纯文本 vs 多模态
            if images:
                content_parts = [{"type": "text", "text": user_text}]
                for url in images:
                    content_parts.append({"type": "image_url", "image_url": {"url": url}})
                user_msg = {"role": "user", "content": content_parts}
            else:
                user_msg = {"role": "user", "content": user_text}

            # 把 user_msg 注入主 App 的消息列表
            self.app_instance.messages.append(user_msg)

            # 【修复 2026-09-16】此前此处直接 `OpenAI(base_url=cfg["base_url"],
            # api_key=cfg["api_key"], ...)`，绕过了 secrets._make_openai_sync_client，
            # 后果与 llm.py 当年完全相同：**代理模式形同虚设** —— 用户开启代理后
            # 语音对话框仍直连上游，而代理模式下本地不配真实 Key，请求必然失败。
            # 现与 llm.py / app.py 对齐：工厂决定"代理 or 直连"，
            # 仅在本地模式下覆盖 timeout / max_retries。
            model_key = self.app_instance.model_key
            # 有图片时强制用 vision 模型（glm-v）以保证多模态识别
            if images and model_key != "glm-v":
                model_key = "glm-v"
            cfg = MODEL_CONFIGS[model_key]
            client = _make_openai_sync_client(model_key)
            if not _is_proxy_enabled():
                client = OpenAI(
                    base_url=cfg["base_url"],
                    api_key=cfg["api_key"],
                    timeout=180.0,
                    max_retries=2,
                )
            response = client.chat.completions.create(
                model=cfg["model"],
                messages=self.app_instance.messages,
                stream=True,
                stream_options={"include_usage": True},
                temperature=self.app_instance.temperature,
                timeout=180,
            )
            full_text = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    piece = chunk.choices[0].delta.content
                    full_text += piece
                    on_chunk(piece)
            # 流式结束后再一次性过滤模型内部特殊标签（<|observation|> <|system|> 等）
            full_text = _strip_model_tokens(full_text)
            # 同时把回答追加到主 App 的消息历史
            self.app_instance.messages.append({"role": "assistant", "content": full_text})
            result_holder["text"] = full_text
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            result_holder["done"] = True

    # ── 手动录音控制（点左侧"说话"按钮开始/停止录音）──
    def _start_manual_recording(self) -> None:
        """开始手动录音（后台线程录音，主线程不阻塞）"""
        if self._is_generating or self._is_speaking:
            # AI 正在生成/朗读，先停止
            self._stop_generation_and_tts()
        import threading
        self._manual_listening = True
        self._manual_audio_buf = []
        # 更新 UI
        self._is_listening = True
        self._current_ai_text = ""
        self._current_ai_displayed = ""
        self._show_listening_indicator()
        self._update_status("listening", "正在录音…点'说话'停止")
        self._set_subtitle("🔴 正在录音…点'说话'按钮停止", "listening")
        try:
            btn = self.query_one("#vd-btn-mute", Static)
            btn.update("⏹停止")
            btn.add_class("recording")
        except Exception:
            pass
        # 启动后台录音线程
        def _rec():
            try:
                import sounddevice as sd
                import numpy as np
                sr = 16000
                block = 1024
                with sd.InputStream(samplerate=sr, channels=1, blocksize=block, dtype='float32') as stream:
                    while self._manual_listening and self._dialog_active:
                        data, _ = stream.read(block)
                        self._manual_audio_buf.append(data.copy())
            except Exception as e:
                self._manual_audio_buf = None
                self.app.call_from_thread(self._set_subtitle, f"录音失败: {e}", "error")
        self._manual_rec_thread = threading.Thread(target=_rec, daemon=True)
        self._manual_rec_thread.start()

    def _stop_manual_recording(self) -> None:
        """停止手动录音并识别"""
        self._manual_listening = False
        self._is_listening = False
        # 等录音线程结束
        if self._manual_rec_thread and self._manual_rec_thread.is_alive():
            self._manual_rec_thread.join(timeout=1.0)
        # 恢复按钮
        try:
            btn = self.query_one("#vd-btn-mute", Static)
            btn.update("说话")
            btn.remove_class("recording")
        except Exception:
            pass
        self._hide_listening_indicator()
        # 合并音频
        if not self._manual_audio_buf:
            self._set_subtitle("未录到声音", "error")
            self._update_status("idle", "点左侧'说话'按钮开始录音")
            return
        import numpy as np
        try:
            audio = np.concatenate(self._manual_audio_buf, axis=0).flatten().astype(np.float32)
        except Exception:
            self._set_subtitle("音频合并失败", "error")
            return
        self._manual_audio_buf = None
        # 音量检查
        vol = float(np.abs(audio).mean())
        if vol < 0.005:
            self._set_subtitle("音量太低，请靠近麦克风重试", "error")
            self._update_status("idle", "点左侧'说话'按钮开始录音")
            return
        # 识别
        self._set_subtitle("识别中…", "thinking")
        self._update_status("thinking", "识别中…")
        import threading
        def _recognize():
            text = ""
            try:
                # 复用 zeroai.tools.voice.recognize_audio：
                # 此前的内联实现与 listen_asr 的识别阶段**逐字重复**（同样的
                # from_sense_voice 参数、同样 create_stream/accept_waveform/
                # decode_stream 流程），且各自 `global` 各自模块的 _ASR_MODEL。
                # 两个模块各加载一份 SenseVoice 模型（int8 约 220MB），
                # 先开语音对话屏再调用 listen_asr 就会重复加载。
                # 现已把唯一初始化点收敛到 voice.get_asr_model()，
                # 本处与 listen_asr 共用同一单例。
                from zeroai.tools.voice import recognize_audio
                text = recognize_audio(audio)
            except Exception as e:
                text = f"错误：{e}"
            self.app.call_from_thread(self._on_manual_recognized, text)
        threading.Thread(target=_recognize, daemon=True).start()

    def _on_manual_recognized(self, text: str) -> None:
        """手动录音识别完成回调"""
        is_error = text.startswith("错误")
        is_empty = (not text.strip()) or text.strip() in ("（未识别到内容）", "（未录到声音）")
        if is_error or is_empty:
            self._set_subtitle(text.strip() or "（未识别）", "error")
            self._update_status("idle", "点左侧'说话'按钮开始录音")
            return
        self._set_subtitle(text.strip(), "recognized")
        # 退出指令
        if text.strip() in ("停止", "退出", "结束对话", "退出对话", "stop", "结束", "关闭"):
            self._dialog_active = False
            self._append_user_bubble(text)
            self._append_system_bubble("👋 对话已结束")
            self.action_close_dialog()
            return
        # 发送给 AI
        self._current_user_text = text
        self._append_user_bubble(text)
        pending_imgs = self._get_pending_images()
        if pending_imgs:
            self._append_system_bubble(f"📎 已附带 {len(pending_imgs)} 张图片")
        # 启动 AI 回复线程
        import threading
        def _ai_reply():
            try:
                self._dialog_loop_single_turn(text)
            except Exception as e:
                self.app.call_from_thread(self._set_subtitle, f"AI 回复失败: {e}", "error")
        threading.Thread(target=_ai_reply, daemon=True).start()

    def _stop_generation_and_tts(self) -> None:
        """停止当前 AI 生成和 TTS 朗读"""
        try:
            self.app_instance._stop_generation = True
        except Exception:
            pass
        try:
            import pygame
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._is_speaking = False
        self._is_generating = False

    def _dialog_loop_single_turn(self, text: str) -> None:
        """手动模式：处理单轮对话（AI 回复 + TTS 朗读）

        从原 _dialog_loop 提取第 3-5 步逻辑：
        3. 显示用户气泡 + AI 占位
        4. AI 流式生成
        5. TTS 朗读
        """
        import time as _t
        import threading as _th
        # 【修复 2026-09-16】本轮开始清空上一轮的 AI 文本，理由同 _handle_text：
        # _on_ai_chunk 是 `+=` 累加，不重置会跨轮拼接。
        self._current_ai_text = ""
        # ═══ 3. 显示 AI 占位 ═══
        self.app.call_from_thread(self._append_ai_placeholder)
        self.app.call_from_thread(self._update_status, "thinking", "AI 正在思考…")
        self.app.call_from_thread(self._set_subtitle, "AI 正在思考…", "thinking")
        self._is_generating = True
        # ═══ 4. AI 流式生成 ═══
        ai_done_event = _th.Event()
        ai_result = {"text": "", "error": None}
        pending_imgs = self._get_pending_images()
        def _run_ai():
            try:
                result_text = self._generate_ai_stream(
                    text,
                    lambda chunk: self._on_ai_chunk(chunk),
                    images=pending_imgs,
                )
                ai_result["text"] = result_text
            except Exception as e:
                ai_result["error"] = str(e)
            finally:
                ai_done_event.set()
        ai_thread = _th.Thread(target=_run_ai, daemon=True)
        ai_thread.start()
        ai_done_event.wait()
        self._is_generating = False
        if ai_result["error"]:
            self.app.call_from_thread(self._append_system_bubble, f"⚠️ 生成失败：{ai_result['error'][:50]}")
            self.app.call_from_thread(self._update_status, "idle", "点左侧'说话'按钮开始录音")
            return
        if not ai_result["text"].strip():
            self.app.call_from_thread(self._append_system_bubble, "⚠️ AI 未返回内容")
            self.app.call_from_thread(self._update_status, "idle", "点左侧'说话'按钮开始录音")
            return
        self.app.call_from_thread(self._finalize_ai_export)
        # ═══ 5. TTS 朗读 ═══
        if self._mute_enabled:
            self.app.call_from_thread(self._update_status, "idle", "点左侧'说话'按钮开始录音")
            return
        self._is_speaking = True
        self.app.call_from_thread(self._update_status, "speaking", "AI 正在回答…")
        ai_preview = (self._current_ai_text or "").strip().replace("\n", " ")
        if len(ai_preview) > 80:
            ai_preview = ai_preview[:79] + "…"
        self.app.call_from_thread(self._set_subtitle, ai_preview, "speaking")
        interrupt_event = _th.Event()
        self._current_interrupt = interrupt_event
        def _is_interrupted() -> bool:
            if not self._dialog_active:
                return True
            if self._is_listening:
                return True
            return False
        def _do_speak():
            try:
                reply = self._current_ai_text
                voice = self.app_instance._tts_voice
                rate = self.app_instance._tts_rate
                if reply.strip():
                    speak_tts(
                        reply, voice=voice, rate=rate,
                        interrupt_check=_is_interrupted, segment_max_chars=200,
                    )
            except Exception:
                pass
            finally:
                interrupt_event.set()
        speak_thread = _th.Thread(target=_do_speak, daemon=True)
        speak_thread.start()
        while not interrupt_event.is_set():
            _t.sleep(0.2)
            if not self._dialog_active:
                interrupt_event.set()
        self._is_speaking = False
        # 朗读结束，恢复待命
        if self._dialog_active:
            self.app.call_from_thread(self._update_status, "idle", "点左侧'说话'按钮开始录音")
            self.app.call_from_thread(self._set_subtitle, "点左侧'说话'按钮继续", "")

    # ── 事件处理 ──
    def on_click(self, event: events.Click) -> None:
        """处理底部按钮点击（Textual 通用 click 事件）"""
        widget = event.widget
        if widget is None:
            return
        widget_id = getattr(widget, "id", "") or ""

        if widget_id == "vd-btn-mic":
            # 中央大圆麦克风按钮：关闭对话
            self.action_close_dialog()
        elif widget_id == "vd-btn-attach":
            # 右侧附件按钮：打开路径输入（复用打字输入框）
            self.action_attach_image()
        elif widget_id == "vd-btn-mute":
            # 长按被拦截（已弹出音色菜单），跳过本次点击
            if getattr(self, "_suppress_next_mute_click", False):
                self._suppress_next_mute_click = False
                return
            # 左侧"说话"按钮：手动录音控制（点击开始/停止录音）
            if self._manual_listening:
                # 正在录音 → 停止录音并识别
                self._stop_manual_recording()
            else:
                # 未录音 → 开始录音
                self._start_manual_recording()
        elif widget_id == "vd-subtitle-btn":
            self._subtitle_enabled = not self._subtitle_enabled
            label = "字幕" if self._subtitle_enabled else "字幕关"
            try:
                self.query_one("#vd-subtitle-btn", Static).update(label)
            except Exception:
                pass
            if not self._subtitle_enabled:
                self._set_subtitle("", "")
        elif widget_id == "vd-close-btn":
            self.action_close_dialog()

    # ── 输入框提交（打字 + 附件路径统一入口） ──
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """统一处理 vd-text-input 提交

        通过 _attach_prompt_open 标志区分：
          - True  → 附件路径模式：加载图片
          - False → 正常打字模式：发送给 AI
        """
        if event.input.id != "vd-text-input":
            return
        text = event.value.strip()
        event.input.value = ""

        # 附件路径模式
        if self._attach_prompt_open:
            self._attach_prompt_open = False
            # 恢复输入框
            event.input.remove_class("visible")
            event.input.placeholder = "打字输入问题，回车发送"
            self._text_input_active = False
            if not text:
                self._update_status("listening", "请说话…")
                return
            # 去掉前后引号
            text = text.strip("\"'")
            ok, msg = self._add_pending_image(text)
            if not ok:
                self._append_system_bubble(f"⚠️ {msg}")
            self._update_status("listening", "请说话…")
            return

        # 正常打字模式
        if not text:
            return
        # 隐藏输入框，回到语音模式
        event.input.remove_class("visible")
        event.input.placeholder = "打字输入问题，回车发送"
        self._text_input_active = False
        # 退出指令
        if text in ("停止", "退出", "结束对话", "stop", "结束", "关闭"):
            self.action_close_dialog()
            return
        # 在后台线程处理这轮对话
        import threading
        pending_imgs = self._get_pending_images()
        def _handle_text():
            self._current_user_text = text
            # 【修复 2026-09-16】本轮开始必须清空上一轮的 AI 文本。
            # _on_ai_chunk 用 `self._current_ai_text += chunk` 累加，
            # 而打字输入路径（本函数）此前从不重置它 —— 于是第二轮的回答
            # 会拼在第一轮后面，并且这个拼接结果会被 _finalize_ai_export
            # 当作本轮回答写入导出记录（实测确证）。
            self._current_ai_text = ""
            self.app.call_from_thread(self._set_subtitle, text, "recognized")
            self.app.call_from_thread(self._append_user_bubble, text)
            if pending_imgs:
                self.app.call_from_thread(self._append_system_bubble, f"📎 已附带 {len(pending_imgs)} 张图片")
            self.app.call_from_thread(self._append_ai_placeholder)
            self.app.call_from_thread(self._update_status, "thinking", "AI 正在思考…")
            self._is_generating = True
            import threading as _th
            ai_done_event = _th.Event()
            ai_result = {"text": "", "error": None}
            def _run_ai():
                try:
                    result_text = self._generate_ai_stream(
                        text,
                        lambda chunk: self._on_ai_chunk(chunk),
                        images=pending_imgs,
                    )
                    ai_result["text"] = result_text
                except Exception as e:
                    ai_result["error"] = str(e)
                finally:
                    ai_done_event.set()
            ai_thread = _th.Thread(target=_run_ai, daemon=True)
            ai_thread.start()
            ai_done_event.wait()
            self._is_generating = False
            if ai_result["error"]:
                self.app.call_from_thread(self._append_system_bubble, f"⚠️ 生成失败：{ai_result['error'][:50]}")
                return
            self.app.call_from_thread(self._finalize_ai_export)
            # TTS 朗读
            if not self._mute_enabled and ai_result["text"].strip():
                self._is_speaking = True
                self.app.call_from_thread(self._update_status, "speaking", "AI 正在回答")
                try:
                    # 【修复 2026-09-16】原先此处是 `from tui_agent import speak_tts`，
                    # 属于**函数级反向依赖** —— 模块级检查看不到它。
                    # 实测 tui_agent.speak_tts 与 zeroai.tools.voice.speak_tts
                    # 并非同一对象（是两份独立实现），但除一行 docstring 外
                    # 源码逐字节相同，故改指 canonical 来源是安全的。
                    from zeroai.tools.voice import speak_tts as _speak
                    def _is_interrupted() -> bool:
                        if not self._dialog_active:
                            return True
                        if self._is_listening:
                            return True
                        return False
                    _speak(
                        ai_result["text"],
                        voice=self.app_instance._tts_voice,
                        rate=self.app_instance._tts_rate,
                        interrupt_check=_is_interrupted,
                        segment_max_chars=200,
                    )
                except Exception:
                    pass
                self._is_speaking = False
        t = threading.Thread(target=_handle_text, daemon=True)
        t.start()

    def _close_attach_prompt(self) -> None:
        """关闭附件模式（恢复输入框）"""
        self._attach_prompt_open = False
        try:
            inp = self.query_one("#vd-text-input", Input)
            inp.remove_class("visible")
            inp.placeholder = "打字输入问题，回车发送"
        except Exception:
            pass

    # ── Ctrl+S：导出对话为 Markdown ──
    def action_export_dialog(self) -> None:
        """Ctrl+S：把当前对话导出为 Markdown 文件"""
        try:
            from pathlib import Path as _P
            # 默认导出到用户目录的 .trae-cn/voice_dialogs/
            save_dir = _P.home() / ".trae-cn" / "voice_dialogs"
            save_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime
            ts = datetime.fromtimestamp(self._session_started_at).strftime("%Y%m%d_%H%M%S")
            path = save_dir / f"dialog_{ts}.md"
            md = self._build_export_markdown()
            path.write_text(md, encoding="utf-8")
            self._append_system_bubble(f"💾 对话已导出：{path}")
            # 同步写入 export_lines
            self._export_lines.append(("system", f"已导出到 {path}"))
        except Exception as e:
            self._append_system_bubble(f"⚠️ 导出失败：{e}")

    def _build_export_markdown(self) -> str:
        """构造可导出的 Markdown 文本"""
        from datetime import datetime
        lines = []
        lines.append(f"# ZeroAI 语音对话记录")
        lines.append("")
        lines.append(f"- 开始时间：{datetime.fromtimestamp(self._session_started_at).strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"- 模型：{self.app_instance.model_key} ({self.app_instance.get_current_model()})")
        try:
            lines.append(f"- 音色：{self.app_instance._tts_voice}")
        except Exception:
            pass
        lines.append(f"- 消息条数：{sum(1 for r, _ in self._export_lines if r in ('user', 'assistant'))}")
        lines.append("")
        lines.append("---")
        lines.append("")
        for role, text in self._export_lines:
            text = (text or "").strip()
            if not text:
                continue
            if role == "user":
                lines.append("## 🙋 你")
                lines.append("")
                lines.append(text)
                lines.append("")
            elif role == "assistant":
                lines.append("## 🤖 AI")
                lines.append("")
                lines.append(text)
                lines.append("")
            elif role == "system":
                lines.append(f"> {text}")
                lines.append("")
        return "\n".join(lines)

    def action_close_dialog(self) -> None:
        """关闭讨论助手"""
        self._dialog_active = False
        # 停止手动录音
        self._manual_listening = False
        # 停止 AI 生成
        try:
            self.app_instance._stop_generation = True
        except Exception:
            pass
        # 停止 TTS 播放（当前段立即停止）
        try:
            import pygame
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        self._is_speaking = False
        self._is_generating = False
        self._is_listening = False
        self.dismiss(None)

    def action_manual_input(self) -> None:
        """Ctrl+T：切换打字输入模式"""
        try:
            inp = self.query_one("#vd-text-input", Input)
            if inp.has_class("visible"):
                # 已显示 → 隐藏
                inp.remove_class("visible")
                self._text_input_active = False
            else:
                # 显示并聚焦
                inp.add_class("visible")
                inp.focus()
                self._text_input_active = True
                # 暂停录音循环（让用户能安静打字）
                self._is_listening = False
                self._update_status("idle", "打字模式：回车发送，Esc 回到语音")
        except Exception as e:
            self._append_system_bubble(f"⚠️ 切换失败：{e}")


def is_loaded_from_tui_agent() -> bool:
    """自检：实现是否已迁移到本模块（False = 已迁移，True = 仍在转发）"""
    return AddModelScreen.__module__ == "tui_agent"


def get_module_info() -> dict:
    """返回模块信息（用于自检和迁移进度跟踪）"""
    return {
        "mode": "wrapper" if is_loaded_from_tui_agent() else "standalone",
        "source": "tui_agent.py" if is_loaded_from_tui_agent() else "zeroai.tui.screens",
        "exports": list(__all__),
        "count": len(__all__),
        # 本模块已无待搬迁组件（三个模态类均已落地）
        "pending": [],
    }
