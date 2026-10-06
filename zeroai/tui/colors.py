"""ZeroAI TUI 配色常量（Tokyo Night 风格）

这是**唯一定义处**。tui_agent.py 不再自己定义这些常量，而是从这里转发。
（历史：本文件曾是 tui_agent.py 的字面复制副本，两份副本会静默漂移，
 导致 TUI 渲染出错颜色却不报错。2026-09-15 改为单一真源。）

配色方案：纯黑背景 + 灰文字 + 彩色工具标签
"""

# ====== 配色：纯黑 + 灰文字 + 彩色工具标签 ======
C_BG = "#000000"        # 纯黑（主背景）
C_BG2 = "#0A0A0A"       # 接近黑（卡片背景）
C_FG = "#C8C8D0"        # 柔灰白（主文字，不刺眼）
C_DIM = "#6B6B75"       # 灰色（次要文字/说明）
C_BORDER = "#1F1F1F"    # 极深灰（边框，几乎看不见）
C_BLUE = "#7AA2F7"      # 蓝（强调）
C_PURPLE = "#BB9AF7"    # 紫
C_RED = "#F7768E"       # 红（工具/告警）
C_GREEN = "#9ECE6A"     # 绿（成功/进行中）
C_YELLOW = "#E0AF68"    # 黄（思考/提示）
C_CYAN = "#7DCFFF"      # 青
C_ORANGE = "#FF9E64"    # 橙（重点）
C_ACCENT = "#7AA2F7"    # 主强调色（蓝）
C_USER_BUBBLE = "#000000"  # 用户气泡背景（纯黑）
C_AI_BUBBLE = "#000000"    # AI气泡背景（纯黑）

__all__ = [
    "C_BG", "C_BG2", "C_FG", "C_DIM", "C_BORDER",
    "C_BLUE", "C_PURPLE", "C_RED", "C_GREEN", "C_YELLOW",
    "C_CYAN", "C_ORANGE", "C_ACCENT",
    "C_USER_BUBBLE", "C_AI_BUBBLE",
]
