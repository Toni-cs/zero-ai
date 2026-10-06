"""ZeroAI TUI 层

从 tui_agent.py 迁移的终端 UI 组件（包装模式）。

设计原则：
- 零反向依赖：本包**不** import tui_agent（方向是 tui_agent 从本包取实现）
- 迁移期兜底：tui_agent.py 保留旧实现，靠 `_ZEROAI_IMPL_ACTIVE` 切换
- 后续可逐步将剩余实现迁到本包，tui_agent.py 保留为薄入口

模块结构：
- colors.py: 配色常量（MiMo Code Agent 风格）
- icons.py: 图标加载工具
- markdown.py: Markdown / LaTeX / 图片预览渲染
- identity.py: 身份泄露过滤
- widgets.py: 自定义组件（InfoBar, HintBar, TokenBar）
- screens.py: 模态对话框（AddModelScreen, SettingsScreen, VoiceDialogScreen）
- app.py: ZeroAI 主应用类（生命周期、消息块骨架、滚动）
- app_*.py: 8 个 mixin，按职责承接 ZeroAI 的其余方法
  （settings / clipboard / voice / commands / mcp / turn_loop / turn_multi / streaming），
  2026-10-06 自 app.py 拆出，方法体逐字节未改
"""
from .colors import (
    C_BG, C_BG2, C_FG, C_DIM, C_BORDER,
    C_BLUE, C_PURPLE, C_RED, C_GREEN, C_YELLOW,
    C_CYAN, C_ORANGE, C_ACCENT,
    C_USER_BUBBLE, C_AI_BUBBLE,
)

# 延迟导入 UI 模块（避免在纯命令行场景下加载 Textual）
# 使用 __getattr__ 实现按需导入，减少启动开销

def __getattr__(name):
    """按需导入 UI 模块（仅在使用时加载 Textual 相关依赖）"""
    if name in ("render_markdown", "_safe_markdown", "render_latex_in_text", "render_image_preview"):
        from .markdown import render_markdown, _safe_markdown, render_latex_in_text, render_image_preview
        return locals()[name]
    if name in ("_IDENTITY_LEAK_PATTERNS", "_IDENTITY_REPLACEMENT", "_sanitize_identity_leak"):
        from .identity import _IDENTITY_LEAK_PATTERNS, _IDENTITY_REPLACEMENT, _sanitize_identity_leak
        return {"_IDENTITY_LEAK_PATTERNS": _IDENTITY_LEAK_PATTERNS,
                "_IDENTITY_REPLACEMENT": _IDENTITY_REPLACEMENT,
                "_sanitize_identity_leak": _sanitize_identity_leak}[name]
    if name in ("InfoBar", "HintBar", "TokenBar", "MessageInput"):
        from .widgets import InfoBar, HintBar, TokenBar, MessageInput
        return {"InfoBar": InfoBar, "HintBar": HintBar,
                "TokenBar": TokenBar, "MessageInput": MessageInput}[name]
    if name in ("AddModelScreen", "SettingsScreen", "VoiceDialogScreen"):
        from .screens import AddModelScreen, SettingsScreen, VoiceDialogScreen
        return {"AddModelScreen": AddModelScreen,
                "SettingsScreen": SettingsScreen,
                "VoiceDialogScreen": VoiceDialogScreen}[name]
    if name == "ZeroAI":
        from .app import ZeroAI
        return ZeroAI
    raise AttributeError(f"module 'zeroai.tui' has no attribute {name!r}")


__all__ = [
    # 配色常量（直接导出，无 Textual 依赖）
    "C_BG", "C_BG2", "C_FG", "C_DIM", "C_BORDER",
    "C_BLUE", "C_PURPLE", "C_RED", "C_GREEN", "C_YELLOW",
    "C_CYAN", "C_ORANGE", "C_ACCENT",
    "C_USER_BUBBLE", "C_AI_BUBBLE",
    # UI 模块（按需导入）
    "render_markdown", "_safe_markdown", "render_latex_in_text", "render_image_preview",
    "_IDENTITY_LEAK_PATTERNS", "_IDENTITY_REPLACEMENT", "_sanitize_identity_leak",
    "InfoBar", "HintBar", "TokenBar", "MessageInput",
    "AddModelScreen", "SettingsScreen", "VoiceDialogScreen",
    "ZeroAI",
]
