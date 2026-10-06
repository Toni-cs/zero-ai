"""MCP 客户端健壮性回归测试。

背景（2026-09-16，红队审查发现）
==========================================================================
三个次生缺陷，均由对抗式审查挖出，且都不是"猜的"，都有实测证据：

1. **读循环 EOF 不标记断开** → ``is_connected`` 仍为 True：
   - 包装函数里的 ``if not client.is_connected: await client.connect()``
     永不触发 → **永不重连**；
   - 在途 Future 永不 set_result → 每个调用卡满默认 60s 超时。

2. **读循环在管道关闭后忙等自旋** → ``readline()`` 持续抛
   ``ValueError: I/O operation on closed pipe``，而旧代码无条件 ``continue``，
   循环变成忙等，实测把 stdout 刷屏淹没、掩盖真正的验证输出。

3. **退出清理缺失**（此条经实测**部分证伪**，见下）→ ``shutdown_mcp_tools``
   只在 ``/mcp disconnect`` 里调用；TUI 退出路径完全没有清理。
   实测 Textual 8.2.8 的 App **没有 on_unmount 钩子**（连 ``Unmount`` 消息都
   不存在），所以"在 on_unmount 里清理"这个常见写法在本项目里是**死代码**。
   改用 ``atexit`` 作为与 UI 框架无关的兜底。

   诚实修正：红队原本指控"退出后残留 uvx 子进程"。A/B 对照实测**未能复现** ——
   启用与禁用清理两种情况下，uvx 的 PID 在父进程退出 3 秒后都是 DEAD；
   进程快照差分也无净增（274 → 274）。原因是 uvx 的 stdin 是管道，父进程退出
   即关闭管道，uvx 收到 EOF 自行退出。
   因此 ``atexit`` 清理是**针对不响应 stdin EOF 的 MCP 服务器的防御性兜底**，
   而不是"修复了一个已复现的泄漏"。测试只覆盖其正确性（幂等、不抛异常），
   不声称它解决了某个已复现缺陷。
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from zeroai.mcp.client import MCPClient, MCPClientError  # noqa: E402
from zeroai.mcp.config import MCPServerConfig  # noqa: E402
from tui_sources import read_tui_app_sources  # noqa: E402


def _client() -> MCPClient:
    return MCPClient(MCPServerConfig(name="t", transport="stdio", command="x"))


# ═══════════════ T1：断开标记与在途请求唤醒 ═══════════════

def test_mark_disconnected_resets_flags_and_fails_pending():
    """T1a _mark_disconnected 必须重置状态并唤醒所有在途请求"""
    c = _client()
    c._connected = True
    c._initialized = True

    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        c._pending["req-1"] = fut

        c._mark_disconnected("boom")

        assert c.is_connected is False, "状态未重置"
        assert c._pending == {}, "在途请求表未清空"
        assert fut.done(), "在途 Future 未被唤醒（会卡满 60s 超时）"
        exc = fut.exception()  # 取出异常，避免 "never retrieved" 警告
        assert isinstance(exc, MCPClientError)
        assert "boom" in str(exc)
    finally:
        loop.close()


def test_mark_disconnected_is_idempotent():
    """T1b 重复标记不应抛异常"""
    c = _client()
    c._mark_disconnected("a")
    c._mark_disconnected("b")
    assert c.is_connected is False


# ═══════════════ T2：读循环 ═══════════════

def test_read_loop_eof_marks_disconnected():
    """T2a 【决定性】EOF 后必须标记断开，否则永不重连"""
    c = _client()
    c._connected = True
    c._initialized = True

    async def fake_read():
        return None  # EOF

    c._read_one_message = fake_read
    asyncio.run(c._read_loop())

    assert c.is_connected is False, "EOF 后 is_connected 仍为 True → 永不重连"


def test_read_loop_stops_spinning_on_persistent_errors():
    """T2b 【决定性】持续读失败必须**有界**退出，不能忙等自旋"""
    c = _client()
    c._connected = True
    c._initialized = True
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise ValueError("I/O operation on closed pipe")

    c._read_one_message = boom
    asyncio.run(c._read_loop())

    assert calls["n"] == 3, f"应在 3 次后放弃，实际尝试 {calls['n']} 次（忙等自旋）"
    assert c.is_connected is False


def test_read_loop_resets_error_counter_on_success():
    """T2c 成功读取应重置失败计数（偶发错误不该累积成断开）"""
    c = _client()
    c._connected = True
    c._initialized = True
    seq = {"i": 0}

    async def flaky():
        seq["i"] += 1
        if seq["i"] == 1:
            raise ValueError("transient")
        if seq["i"] == 2:
            return "not-json"  # parse_message 返回 None → continue
        return None  # 第 3 次 EOF

    c._read_one_message = flaky
    asyncio.run(c._read_loop())

    assert c._read_errors == 0, f"成功读取后计数未重置: {c._read_errors}"
    assert c.is_connected is False  # 最终因 EOF 断开


# ═══════════════ T3：退出清理 ═══════════════

def test_kill_sync_without_process_returns_false():
    """T3a 无子进程时 kill_sync 返回 False，不抛异常"""
    assert _client().kill_sync() is False


def test_force_kill_mcp_tools_sync_exported_and_safe():
    """T3b force_kill_mcp_tools_sync 必须对外可用，且空注册表下安全"""
    from zeroai.mcp import force_kill_mcp_tools_sync
    from zeroai.mcp import registry as reg

    assert "force_kill_mcp_tools_sync" in reg.__all__
    assert "force_kill_mcp_tools_sync" in reg.MCPRegistry.__dict__ or hasattr(
        reg.MCPRegistry, "force_kill_sync"
    ), "注册器缺少同步强杀入口"
    assert isinstance(force_kill_mcp_tools_sync(), int)


def test_atexit_registration_is_idempotent(monkeypatch):
    """T3c atexit 清理只能注册一次（避免重复注册拖慢退出）"""
    import atexit

    from zeroai.mcp import registry as reg

    calls = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: calls.append(fn))
    monkeypatch.setattr(reg, "_atexit_registered", False, raising=False)

    reg._register_atexit_cleanup()
    reg._register_atexit_cleanup()

    assert len(calls) == 1, f"atexit 清理被重复注册 {len(calls)} 次"
    assert calls[0] is reg._atexit_cleanup


def test_atexit_cleanup_swallows_errors(monkeypatch):
    """T3d atexit 清理绝不能抛异常（否则污染解释器退出）"""
    from zeroai.mcp import registry as reg

    class Boom:
        def force_kill_sync(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(reg, "get_mcp_registry", lambda: Boom())
    reg._atexit_cleanup()  # 不抛即通过


# ═══════════════ T4：TUI 不再静默失败 ═══════════════

def test_tui_reports_mcp_failures():
    """T4 MCP 初始化失败必须提示用户，不能静默"""
    # app.py 已于 2026-10-06 拆出 app_mcp.py，故读全部承载 ZeroAI 的文件
    src = read_tui_app_sources()
    assert "_notify_mcp_error" in src, "缺少失败提示方法"
    assert "self._notify_mcp_init(result)" in src, "初始化结果未提示"
    assert "pass  # 静默失败，不影响主程序" not in src, "仍存在静默吞掉 MCP 初始化异常"


def test_textual_app_has_no_on_unmount_hook():
    """T4' 固化事实：Textual App 没有 on_unmount 钩子。

    本测试的意义是**防止后来者再写一遍 on_unmount 死代码**。
    若某天 Textual 新增了该钩子，本测试会失败，提示可以改用更优雅的方式。
    """
    from textual.app import App

    assert not hasattr(App, "_on_unmount"), (
        "Textual 现在支持 on_unmount 了 —— 可以改用钩子替代 atexit，请更新实现与本测试"
    )
