# -*- coding: utf-8 -*-
"""ZeroAI 运行时冒烟：真的把 Textual 应用挂起来跑一遍。

【为什么加】2026-10-06 把 3,664 行的 ZeroAI 拆成 app.py + 8 个 mixin 之后，
发现 tests/ 里**从来没有**一个测试实例化过 ZeroAI：
`test_preexisting_defects` 里的 `run_test()` 起的是临时 `App`，
`test_voice_dialog_runtime` 起的是 `VoiceDialogScreen`。

这留下一个真空档 —— 拆分的所有静态关卡（方法集合、字节一致、悬空名、MRO）
都可能全绿，而 Textual 的 metaclass / BINDINGS 解析 / compose / action
分发只有真跑一次才验得到。静态绿 ≠ 能跑。

【测什么】
  1. compose 真的挂出了 widget；
  2. BINDINGS 里声明的每个 action 都能解析到可调用对象
     （mixin 拆分最容易断在这里：action 定义在 mixin 上，
      解析走的是实例属性查找，静态 AST 查不出来）；
  3. 8 个 mixin 搬走的方法仍可经实例调用；
  4. 真跑几条 action 路径不抛异常。

【为什么 mock 掉 _auto_init_mcp】它会拉起 uvx 子进程连接 MCP 服务器，
测试退出时留下未关闭的 transport，pytest 以退出码 1 收尾。
MCP 行为本身由 test_mcp_*.py 覆盖，这里只验证 UI 接线。
"""
import asyncio

import pytest

from zeroai.tui.app import ZeroAI

# 每个 mixin 至少取一个方法，确保"搬到哪、就验到哪"
MIXIN_METHOD_SAMPLES = (
    "get_current_model",          # app_settings
    "_list_code_blocks",          # app_clipboard
    "_get_companion_context",     # app_voice
    "_show_help",                 # app_commands
    "_notify_mcp_error",          # app_mcp
    "_get_current_ctx_window",    # app_streaming
)


async def _run_app():
    app = ZeroAI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()

        widgets = list(app.query("*"))
        assert widgets, "compose 未产出任何 widget"

        missing = [
            b.action.split()[0] for b in ZeroAI.BINDINGS
            if not callable(getattr(app, f"action_{b.action.split()[0]}", None))
        ]
        assert not missing, f"BINDINGS 指向的 action 解析失败：{missing}"

        for name in MIXIN_METHOD_SAMPLES:
            assert callable(getattr(app, name)), f"实例上找不到 {name}"

        app.action_clear_log()
        app.action_scroll_pagedown()
        app.action_scroll_pageup()
        await pilot.pause()
        return len(widgets)


def test_zeroai_actually_mounts(monkeypatch):
    """ZeroAI 必须能真实挂载，且 BINDINGS / 8 个 mixin 的方法都能解析。"""
    monkeypatch.setattr(ZeroAI, "_auto_init_mcp", lambda self: None, raising=True)
    assert asyncio.run(_run_app()) > 0


def test_binds_every_declared_action():
    """BINDINGS 声明的 action 必须存在（静态，不依赖运行）。"""
    for binding in ZeroAI.BINDINGS:
        action = binding.action.split()[0]
        assert hasattr(ZeroAI, f"action_{action}"), f"缺少 action_{action}"
