"""ZeroAI TUI 自定义组件（真实现）

本模块是 InfoBar / HintBar / MessageInput / TokenBar 的**唯一定义处**。
（历史：此前本文件是 `from tui_agent import ...` 的转发壳，
 2026-09-15 把真实实现搬入，tui_agent.py 改为从这里转发。）

搬迁依据：AST 实测这 4 个类**没有任何 global 语句**，只读取以下名字，
且运行时验证这些名字都已经是 zeroai.* 的同一对象（非副本）：
  C_*、MODEL_CONFIGS、CURRENT_MODEL_KEY、WORK_MODE —— 故可直接显式导入。

- InfoBar: 顶部信息栏（模式指示 + 工作目录）
- HintBar: 底部快捷键栏（含动态上下文 token 统计）
- MessageInput: 多行输入框（Shift+Enter 换行 / Enter 提交）
- TokenBar: 右侧状态栏（会话统计 + 上下文 + 工作目录 + LSP）
"""
from textual.widgets import Input, Static, TextArea
from textual.binding import Binding
from rich.text import Text

from zeroai import get_version
from zeroai.core.constants import MODEL_CONFIGS, WORK_MODE
from zeroai.core.model_manager import CURRENT_MODEL_KEY
from zeroai.core.paths import WORK_DIR
from zeroai.tui.colors import (
    C_ACCENT, C_BG, C_BORDER, C_CYAN, C_DIM, C_FG, C_GREEN,
    C_PURPLE, C_RED, C_YELLOW,
)

__all__ = ["InfoBar", "HintBar", "MessageInput", "TokenBar"]

class InfoBar(Static):
    """顶部信息栏（极简灰色）"""
    def render(self):
        # 优先使用 app 实例的 work_mode，避免全局 WORK_MODE 与 self.work_mode 不一致
        app_mode = getattr(self.app, "work_mode", WORK_MODE)
        if app_mode == "expert":
            mode_text = "专家"
            mode_color = C_PURPLE
        elif app_mode == "hybrid":
            mode_text = "混合"
            mode_color = C_CYAN
        else:
            mode_text = f"手动 · {MODEL_CONFIGS.get(CURRENT_MODEL_KEY, {}).get('label', '')}"
            mode_color = C_YELLOW
        return Text.assemble(
            (f"  ● ", mode_color),
            (f"{mode_text}", mode_color),
            ("    ", ""),
            (f"{WORK_DIR}", C_DIM),
        )

class HintBar(Static):
    """底部快捷键栏（极简灰 + 彩色工具）"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ctx_tokens = 0
        self.ctx_window = 128000

    def update_ctx(self, ctx_tokens: int, ctx_window: int):
        self.ctx_tokens = ctx_tokens
        self.ctx_window = ctx_window
        self.refresh()

    def render(self):
        # 动态显示当前上下文 token 数和百分比
        pct = min(100, int(self.ctx_tokens / self.ctx_window * 100)) if self.ctx_window > 0 else 0
        if self.ctx_tokens >= 1000:
            tk_str = f"{self.ctx_tokens // 1000}K"
        else:
            tk_str = str(self.ctx_tokens)
        win_str = f"{self.ctx_window // 1000}K" if self.ctx_window >= 1000 else str(self.ctx_window)
        return Text.assemble(
            ("  ", ""),
            (f"{tk_str} ({pct}%)", C_DIM), ("   ", ""),
            ("tab ", C_DIM), ("切换模式", C_FG), ("   ", ""),
            ("ctrl+p ", C_DIM), ("设置", C_FG), ("   ", ""),
            ("ctrl+t ", C_DIM), ("语音", C_FG), ("   ", ""),
            ("ctrl+g ", C_DIM), ("图片", C_FG), ("   ", ""),
            ("/", C_DIM), ("命令", C_FG), ("   ", ""),
            (" ", ""),
            ("@", C_ACCENT), ("添加文件   ", C_FG),
            ("$", C_YELLOW), ("子智能体   ", C_FG),
            ("#", C_RED), ("唤起命令", C_FG),
            ("       ", ""),
            ("● ", C_GREEN),
            ("ZeroAI", C_FG),
            # 版本号从 zeroai.__version__ 读取。
            # 此前这里硬编码 " 0.1.5"（自 0.1.x 时代留下的字面量），
            # 导致 TUI 底部长期显示错误的版本号（实际已是 1.1.x），
            # 且每次发版都不会更新 —— 改成动态读取，杜绝再次陈旧。
            (f" {get_version()}", C_DIM),
        )

class MessageInput(TextArea):
    """多行输入框：字数多了自动换行，Shift+Enter 换行，Enter 提交"""
    # 自定义样式：无边框、自动高度
    DEFAULT_CSS = """
    MessageInput {
        background: $surface;
        color: $text;
        border: none;
        padding: 0 1;
        height: auto;
        max-height: 12;
        min-height: 1;
    }
    MessageInput:focus {
        border: none;
    }
    MessageInput .cursor {
        background: $primary;
        color: $background;
    }
    """

    # 继承 TextArea 所有 bindings，移除冲突项：
    # - ctrl+a：原为行首，改为全选
    # - ctrl+y：原为重做，改为冒泡到 App 的复制功能
    # - ctrl+c：原为复制选中文本，改为冒泡到 App 的停止/退出
    # - ctrl+w：原为删除前一个单词，改为冒泡到 App 的伴随模式
    # - ctrl+d：原为删除字符，改为冒泡到 App 的语音对话功能
    BINDINGS = [b for b in TextArea.BINDINGS
                if not any(k in {"ctrl+a", "ctrl+y", "ctrl+c", "ctrl+w", "ctrl+d"} for k in b.key.split(","))]
    BINDINGS.append(Binding("ctrl+a", "select_all", "全选", show=False))

    def __init__(self, placeholder: str = "", id: str = None, **kwargs):
        super().__init__(text="", id=id, **kwargs)
        self._placeholder = placeholder

    @property
    def value(self) -> str:
        """兼容 Input.value：返回输入框全部文本"""
        return self.text

    @value.setter
    def value(self, val: str) -> None:
        """兼容 Input.value：设置输入框文本"""
        self.load_text(val)

    @property
    def placeholder(self) -> str:
        return self._placeholder

    def clear(self) -> None:
        """清空输入框"""
        self.load_text("")

    def action_submit(self) -> None:
        """Enter 键提交"""
        self.post_message(Input.Submitted(self, self.value))

    def on_key(self, event) -> None:
        """拦截按键：Enter 提交，Ctrl+J / Shift+Enter 换行，Ctrl+Y 复制"""
        # Ctrl+Y 复制最近回复（防止 TextArea 内部消费为"重做"）
        if event.key == "ctrl+y":
            event.prevent_default()
            event.stop()
            self.app.action_copy_last_reply()
            return
        # Ctrl+D 语音对话（防止 TextArea 内部消费为"删除字符"，让事件冒泡到 App）
        if event.key == "ctrl+d":
            event.prevent_default()
            event.stop()
            self.app.action_voice_dialog()
            return
        # Ctrl+J 换行（最可靠，所有终端支持，发送 \n）
        if event.key == "ctrl+j":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        # Shift+Enter 换行（部分终端支持）
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        # Enter 提交
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.action_submit()

class TokenBar(Static):
    """右侧状态栏（仿 MiMo：会话标题 + Context 统计 + 工作目录 + LSP）"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.total_tokens = 0       # 累计输出 token（本次会话）
        self.input_tokens = 0       # 当前上下文输入 token（估算）
        self.rate = 0.0             # 输出速率 t/s
        self.ctx_window = 128000    # 上下文窗口（动态根据模型）

    def update_stats(self, total_tokens: int, rate: float, input_tokens: int = 0, ctx_window: int = 0):
        self.total_tokens = total_tokens
        self.rate = rate
        if input_tokens > 0:
            self.input_tokens = input_tokens
        if ctx_window > 0:
            self.ctx_window = ctx_window
        self.refresh()

    def render(self):
        # 当前上下文 token = 输入 token + 输出 token
        ctx_tokens = self.input_tokens + self.total_tokens
        rate_str = f"{self.rate:.0f}" if self.rate >= 10 else f"{self.rate:.1f}"
        # 显示：当前上下文 / 窗口大小
        if ctx_tokens >= 100000:
            ctx_str = f"{ctx_tokens // 1000}K"
        else:
            ctx_str = f"{ctx_tokens:,}"
        win_str = f"{self.ctx_window // 1000}K" if self.ctx_window >= 1000 else str(self.ctx_window)
        pct_used = min(100, int(ctx_tokens / self.ctx_window * 100)) if self.ctx_window > 0 else 0
        # 输出 token 统计
        out_str = f"{self.total_tokens:,}" if self.total_tokens < 100000 else f"{self.total_tokens // 1000}K"
        return Text.assemble(
            ("▶\n\n", f"bold {C_DIM}"),
            ("会话统计\n\n", f"bold {C_FG}"),
            ("上下文\n", f"bold {C_FG}"),
            (f"{ctx_str} / {win_str} tokens\n", C_DIM),
            (f"{pct_used}% 已用\n", C_DIM),
            (f"输入：{self.input_tokens:,}\n", C_DIM),
            (f"输出：{out_str}\n", C_DIM),
            (f"{rate_str} t/s\n\n", C_DIM),
            ("工作目录\n", f"bold {C_FG}"),
            (f"{WORK_DIR}\n\n", C_DIM),
            ("LSP\n", f"bold {C_FG}"),
            ("LSP 将在读取文件时自动激活", C_DIM),
        )


def is_loaded_from_tui_agent() -> bool:
    """自检：实现是否已迁移到本模块（False = 已迁移，True = 仍在转发）"""
    return InfoBar.__module__ == "tui_agent"


def get_module_info() -> dict:
    """返回模块信息（用于自检和迁移进度跟踪）"""
    return {
        "mode": "wrapper" if is_loaded_from_tui_agent() else "standalone",
        "source": "tui_agent.py" if is_loaded_from_tui_agent() else "zeroai.tui.widgets",
        "exports": list(__all__),
        "count": len(__all__),
    }
