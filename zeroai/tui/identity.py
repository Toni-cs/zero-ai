"""身份泄露过滤

包装模块：从 zeroai.core.response_utils 重新导出实现，
模式常量在本模块内定义（X1 之前是从 tui_agent 借来的）。

原实现保留在 tui_agent.py 中作为备份。

迁移来源：tui_agent.py 行 10120-10154
"""
# 【修复 2026-09-16】原先这里是：
#     try:    from zeroai.core.response_utils import _sanitize_identity_leak
#     except ImportError: from tui_agent import _sanitize_identity_leak
# 那个回退分支是**不可达**的（zeroai.core.response_utils 必然存在），
# 却让本模块保留了最后一条指向 tui_agent 的反向依赖 —— 而模块级检查
# 看不到它（它在 except 分支里）。已删除回退，改为直接导入。
from zeroai.core.response_utils import _sanitize_identity_leak

# 【X1，2026-09-15】原先这里 `from tui_agent import _IDENTITY_LEAK_PATTERNS,
# _IDENTITY_REPLACEMENT`，构成第二条循环边：
#     zeroai.tui.app → zeroai.tui.identity → tui_agent → zeroai.tui.app
# 现改为模块内定义。值与原绑定严格一致（已实测 both == ([], "")）：
# 身份抹除功能已于 2026-09-15 整体删除，tui_agent 侧只留同名兼容空壳，
# 因此两个常量就是「空列表 + 空字符串」这个语义本身。
_IDENTITY_LEAK_PATTERNS: list = []
_IDENTITY_REPLACEMENT: str = ""

__all__ = [
    "_IDENTITY_LEAK_PATTERNS",
    "_IDENTITY_REPLACEMENT",
    "_sanitize_identity_leak",
]
