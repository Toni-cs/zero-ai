"""tests/ 共享 pytest 配置。

## 为什么需要这个文件

部分测试文件是**脚本与 pytest 两用**的：既提供 `if __name__ == "__main__"`
直接运行（内部显式传参，如 `test_tools_from_zeroai(tui_agent)`），
也被 pytest 收集（此时函数签名里的参数会被当作 fixture 名解析）。

`test_regression_tui_agent_wiring.py` 就是这种写法：6 个测试的签名是
`def test_xxx(tui_agent):`，但仓库里**没有**任何地方定义过 `tui_agent`
这个 fixture。后果是这些测试**从未在 pytest 下真正跑过** ——
它们一直报 `fixture 'tui_agent' not found` 的 ERROR，而 `__main__`
入口又能正常通过，于是"测试通过"的印象被维持了下来。

（该文件原名 `test_phase3_regression.py`，2026-09-15 随测试目录整改
一并移入 tests/，才暴露了这个问题。）

在这里补上 fixture，两种入口都能正常工作。
"""

import sys
from pathlib import Path

import pytest

# 项目根加入 sys.path，让各测试文件无需自己重复推导
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def tui_agent():
    """导入并提供 tui_agent 模块。

    用 session 作用域：该模块 1.6 万行、导入成本高，且测试只读取它的
    属性和函数签名，不做修改，无需每个测试重新导入。

    若模块无法导入，直接 fail 而不是 skip —— 对"阶段3 切换"这类
    回归测试来说，导不进来本身就是被测目标失败。
    """
    import tui_agent as _mod
    return _mod
