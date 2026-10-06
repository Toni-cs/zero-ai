"""tests/ 共享 pytest 配置。

## 这个文件做过什么

它曾提供一个 `tui_agent` fixture，用来补救一个写法问题：部分测试文件是
**脚本与 pytest 两用**的 —— 既提供 `if __name__ == "__main__"` 直接运行
（内部显式传参，如 `test_tools_from_zeroai(mod)`），也被 pytest 收集
（此时函数签名里的参数会被当作 fixture 名解析）。

`test_regression_tui_agent_wiring.py` 就是这种写法：6 个测试的签名是
`def test_xxx(tui_agent):`，但仓库里当时**没有**定义过 `tui_agent` 这个
fixture。后果是这些测试**从未在 pytest 下真正跑过** —— 它们一直报
`fixture 'tui_agent' not found` 的 ERROR，而 `__main__` 入口又能正常通过，
于是"测试通过"的印象被维持了下来。

（该文件原名 `test_phase3_regression.py`，2026-09-15 随测试目录整改
一并移入 tests/，才暴露了这个问题。）

2026-10-06：`tui_agent.py` 已彻底删除，相关测试改指 `zeroai` 包的
直接 import，不再需要 fixture —— 这个历史问题随之消失。本文件保留
sys.path 设置（各测试文件无需自己重复推导项目根）。
"""

import sys
from pathlib import Path

# 项目根加入 sys.path，让各测试文件无需自己重复推导
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
