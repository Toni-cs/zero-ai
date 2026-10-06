# -*- coding: utf-8 -*-
"""VoiceDialogScreen 运行时回归测试（2026-09-16 新增）

覆盖四个**真实存在过**的缺陷。它们此前全部不可观测，因为：
  - 气泡根本不渲染（mount 顺序错）
  - 即使渲染，生产路径也到不了（call_from_thread 不存在于 Screen）
所以只有"挂起真实界面 + 走真实路径"才测得出来。
"""
import asyncio
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCREENS = _ROOT / "zeroai" / "tui" / "screens.py"


class _FakeAppInstance:
    model_key = "stub-model"
    client = None
    work_mode = "expert"
    history: list = []
    context_tokens = 0
    _tts_voice = "zh-CN-XiaoxiaoNeural"
    _tts_rate = "+0%"


def _run(fn):
    """在真实 run_test 环境挂载 VoiceDialogScreen，执行 fn(scr, pilot)。"""
    from textual.app import App
    from zeroai.tui.screens import VoiceDialogScreen

    holder = {}

    class _T(App):
        def on_mount(self):
            scr = VoiceDialogScreen(_FakeAppInstance())
            holder["scr"] = scr
            self.push_screen(scr)

    async def _main():
        app = _T()
        async with app.run_test(size=(100, 45)) as pilot:
            await pilot.pause()
            scr = holder["scr"]
            for _ in range(30):
                try:
                    scr.query_one("#vd-content")
                    break
                except Exception:
                    await asyncio.sleep(0.05)
                    await pilot.pause()
            return await fn(scr, pilot)

    return asyncio.run(_main())


def _text_of(log):
    return "\n".join(str(seg) for line in log.lines for seg in [line])


# ══════════════════════════════════════════════════════════════════
# F4：Screen 没有 call_from_thread —— 必须用 self.app.call_from_thread
# ══════════════════════════════════════════════════════════════════
def test_screen_has_no_call_from_thread_method():
    """记录 Textual 事实：call_from_thread 只在 App 上，Screen 上没有。

    这条不是"要求"，而是给下面那条测试提供依据。
    """
    from textual.app import App

    async def _check(scr, pilot):
        return (hasattr(scr, "call_from_thread"), hasattr(scr.app, "call_from_thread"))

    scr_has, app_has = _run(_check)
    assert scr_has is False, "Textual 行为变化：Screen 上居然有 call_from_thread 了，请复核本文件"
    assert app_has is True


def test_no_bare_self_call_from_thread_in_screens():
    """静态断言：screens.py 内不得再出现裸 `self.call_from_thread(`。

    52 处这种写法在 Screen 上全部抛 AttributeError，且异常发生在工作线程里
    （多被 except 吞掉，或直接杀死线程），导致语音对话的生产路径完全不可达。
    """
    src = _SCREENS.read_text(encoding="utf-8")
    # 排除注释与 docstring 里的说明性提及
    import ast

    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "call_from_thread"
                and isinstance(f.value, ast.Name) and f.value.id == "self"):
            offenders.append(node.lineno)
    assert not offenders, (
        f"仍存在裸 self.call_from_thread(...) 调用：行 {offenders[:10]}；"
        "Screen 上无此方法，应改为 self.app.call_from_thread(...)"
    )


# ══════════════════════════════════════════════════════════════════
# F1：AI 文本缓冲不得跨轮泄漏
# ══════════════════════════════════════════════════════════════════
def test_ai_buffer_does_not_leak_across_turns():
    """第二轮 AI 气泡不得回放第一轮的答案。"""
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        content = scr.query_one("#vd-content", VerticalScroll)
        # 第一轮：占位 + 流式
        scr._append_ai_placeholder()
        for _ in range(4):
            await pilot.pause()
        scr._update_ai_bubble("第一轮的答案")
        for _ in range(4):
            await pilot.pause()
        # 第二轮：只占位，不送任何 chunk
        scr._append_ai_placeholder()
        for _ in range(6):
            await pilot.pause()
        logs = list(content.query(".vd-log-ai"))
        assert logs, "第二轮没有 .vd-log-ai 节点"
        return _text_of(logs[-1])

    t2 = _run(_check)
    assert "第一轮的答案" not in t2, (
        f"第二轮气泡回放了第一轮内容（缓冲跨轮泄漏）：{t2!r}"
    )


def test_ai_buffer_reset_synchronously():
    """_append_ai_placeholder 必须**同步**清空缓冲。

    若改到 call_next 回调里清，同帧到达的首个流式片段会被清掉 —— 那是另一个 bug。
    """
    async def _check(scr, pilot):
        scr._ai_text_buffer = "上一轮残留"
        scr._append_ai_placeholder()   # 不得 await，检查同步效果
        return scr._ai_text_buffer

    val = _run(_check)
    assert val == "", f"占位后缓冲应被同步清空，实际 {val!r}"


# ══════════════════════════════════════════════════════════════════
# F3：流式更新不得抹掉 AI 标题行
# ══════════════════════════════════════════════════════════════════
def test_ai_header_survives_stream_update():
    """_update_ai_bubble 会 clear()，必须把标题补回来，否则左右气泡不对称。"""
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        content = scr.query_one("#vd-content", VerticalScroll)
        scr._append_ai_placeholder()
        for _ in range(4):
            await pilot.pause()
        scr._update_ai_bubble("流式内容 ABC")
        for _ in range(4):
            await pilot.pause()
        logs = list(content.query(".vd-log-ai"))
        assert logs
        return _text_of(logs[-1])

    text = _run(_check)
    assert "┌─ AI" in text, f"流式更新后标题丢失：{text!r}"
    assert "流式内容 ABC" in text, f"流式内容未写入：{text!r}"


# ══════════════════════════════════════════════════════════════════
# 反向依赖：screens.py 不得在函数内 import tui_agent
# ══════════════════════════════════════════════════════════════════
def test_screens_has_no_tui_agent_import():
    """函数级（惰性）反向依赖同样要禁 —— 模块级检查发现不了它们。"""
    import ast

    tree = ast.parse(_SCREENS.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tui_agent"):
            bad.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("tui_agent"):
                    bad.append((node.lineno, a.name))
    assert not bad, f"screens.py 仍存在对 tui_agent 的导入（含函数内）：{bad}"
