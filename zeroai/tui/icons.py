"""ZeroAI TUI 图标加载

这是**唯一定义处**。tui_agent.py 不再自己实现 _load_svg_icon，而是从这里转发。
（历史：本文件曾是 tui_agent.py 的复制副本，两份实现会静默漂移。
 2026-09-15 改为单一真源。）

终端环境无法渲染 SVG，返回等宽文字标签替代。
"""
from pathlib import Path
import sys

# 图标标签映射（终端用文字标签替代 SVG 图标）
# 提升为模块级常量：tui_agent.py 旧实现把它放在函数体内，每次调用都要重建 dict。
ICON_LABELS = {
    "folder": "[DIR]",
    "file": "[FILE]",
    "search": "[SCAN]",
    "check": "[OK]",
    "cross": "[ERR]",
    "warning": "[!]",
    "security": "[SEC]",
    "monitor": "[SCREEN]",
    "download": "[DL]",
    "document": "[DOC]",
    "tool": "[TOOL]",
}


def _get_icons_dir() -> Path:
    """获取图标目录路径（兼容 PyInstaller 打包与源码运行）

    candidates 顺序（【缺陷修复 2026-09-16】已调整）：

      1. zeroai/assets/icons  —— **包内**，本模块位于 zeroai/tui/，
         故 parents[1] 即 zeroai/。这是 pip 安装后的唯一可用位置，
         也是图标被正式声明进 package-data 的位置，因此**优先**。
      2. <仓库根>/assets/icons —— 源码运行时的原始位置。

    历史问题：旧顺序把仓库根放在首位。安装后仓库根不存在，
    而包内副本当时**根本没被打进 wheel**（package-data 的 glob 写的是
    相对包目录的路径，却指向仓库根的实际文件，从未命中），
    于是 _load_svg_icon() 对所有图标一律返回空串 —— 图标功能全程失效。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后，资源在 _MEIPASS 中
        return Path(sys._MEIPASS) / "assets" / "icons"

    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "assets" / "icons",  # zeroai/assets/icons（包内·优先）
        here.parents[2] / "assets" / "icons",  # 仓库根/assets/icons（源码运行）
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _load_svg_icon(name: str) -> str:
    """加载 SVG 图标文件内容，返回纯文本标签

    行为与 tui_agent.py 旧实现保持一致：
    - 图标文件存在 -> 返回 ICON_LABELS[name]（未知 name 返回 ""）
    - 图标文件不存在 -> 返回 ""
    """
    icons_dir = _get_icons_dir()
    svg_path = icons_dir / f"{name}.svg"
    if svg_path.exists():
        return ICON_LABELS.get(name, "")
    return ""


__all__ = ["ICON_LABELS", "_get_icons_dir", "_load_svg_icon"]
