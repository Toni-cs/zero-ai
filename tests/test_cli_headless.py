# -*- coding: utf-8 -*-
"""ZeroAI 无头模式验收测试

设计原则（可证伪、无网络、无外部依赖）：
- 全部用例要么不触网，要么把真正的 Agent 执行替换成假实现；
- 每个用例断言的是**可观测契约**：退出码、stdout 的 JSON 信封结构、耗时上界；
- 关键回归项：`test_no_tty_does_not_hang` 与
  `test_sync_blocking_tool_respects_timeout` —— 这两条正是"完工"之前
  真实存在的两个致命问题的守门测试。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_cli_headless.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from zeroai import cli  # noqa: E402

SENTINEL = "已达到最大步数，未能完成任务。"


def run_module(args, timeout=60, stdin=subprocess.DEVNULL):
    """在子进程里跑 `python -m zeroai <args>`，返回 (rc, stdout, stderr, elapsed)。"""
    cmd = [sys.executable, "-m", "zeroai", *args]
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, timeout=timeout,
                          stdin=stdin)
    elapsed = time.monotonic() - started
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
            elapsed)


def run_cli(args, timeout=60):
    """在子进程里跑 `python -m zeroai.cli <args>`。"""
    cmd = [sys.executable, "-m", "zeroai.cli", *args]
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)
    elapsed = time.monotonic() - started
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
            elapsed)


# ---------------------------------------------------------------------------
# 1. 参数契约：缺任务 / 空任务 / 不存在模型
# ---------------------------------------------------------------------------
def test_missing_task_returns_usage_error():
    rc, out, err, _ = run_cli([])
    assert rc == cli.EXIT_USAGE, f"期望 2，实际 {rc}\n{out}\n{err}"


def test_empty_task_returns_usage_error():
    rc, out, err, _ = run_cli(["--task", "   ", "--json"])
    assert rc == cli.EXIT_USAGE
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "empty_task"


def test_unknown_model_is_rejected_even_in_dry_run():
    """--dry-run 也必须校验模型名，否则等于没有配置校验。"""
    rc, out, err, _ = run_cli(["--task", "hi", "--model", "no-such-model",
                               "--dry-run", "--json"])
    assert rc == cli.EXIT_USAGE
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["error"]["code"] == "credential_error"
    assert "no-such-model" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 2. --check / --dry-run：无网络、返回结构稳定
# ---------------------------------------------------------------------------
def test_check_is_offline_and_structured():
    rc, out, err, elapsed = run_cli(["--check", "--json"], timeout=90)
    assert rc == cli.EXIT_OK, f"自检应通过，实际 {rc}\n{out}\n{err}"
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["action"] == "check"
    for key in ("checks", "problems", "warnings", "exit_code"):
        assert key in payload, f"信封缺少字段 {key}"
    names = {c["name"] for c in payload["checks"]}
    assert {"python", "package:zeroai", "models"} <= names
    # 自检不该把 UI 引擎拖起来
    assert elapsed < 60, f"自检耗时异常：{elapsed:.1f}s"


def test_dry_run_does_not_touch_network():
    rc, out, err, _ = run_cli(["--task", "列一下目录", "--dry-run", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["action"] == "dry-run"
    assert payload["would_run"] == "zeroai.core.agent.AgentLoop.run"
    assert payload["task"] == "列一下目录"


# ---------------------------------------------------------------------------
# 3. Agent 执行契约（把真正的执行替换掉，保证确定性）
# ---------------------------------------------------------------------------
def _patch_run_agent(monkeypatch, coro_factory):
    async def _fake(*a, **kw):
        return await coro_factory()
    monkeypatch.setattr(cli, "_run_agent", _fake)
    # 绕开凭据要求，专测执行契约
    monkeypatch.setattr(cli, "_resolve_credentials", lambda *a, **k: None)


def test_successful_task_envelope(monkeypatch, capsys):
    async def _ok():
        return {"answer": "42", "steps": [{"thought": "t", "action_type": "final_answer"}],
                "cost_report": None}
    _patch_run_agent(monkeypatch, _ok)

    rc = cli.main(["--task", "生命的意义", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["answer"] == "42"
    assert payload["steps_count"] == 1
    assert payload["exit_code"] == cli.EXIT_OK
    assert payload["elapsed_sec"] >= 0


def test_max_steps_returns_failure_exit_code(monkeypatch, capsys):
    async def _stuck():
        return {"answer": SENTINEL, "steps": [], "cost_report": None}
    _patch_run_agent(monkeypatch, _stuck)

    rc = cli.main(["--task", "不可能的任务", "--json", "--max-steps", "1"])
    assert rc == cli.EXIT_TASK_FAILED
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "max_steps_exceeded"


def test_timeout_returns_124(monkeypatch, capsys):
    async def _slow():
        await asyncio.sleep(5)
        return {"answer": "never", "steps": [], "cost_report": None}
    _patch_run_agent(monkeypatch, _slow)

    # 超时路径会调用 _hard_exit（生产环境是 os._exit）。这里替换成记录器，
    # 否则测试进程会把自己干掉。
    exits = []
    monkeypatch.setattr(cli, "_hard_exit", lambda code: exits.append(code))

    started = time.monotonic()
    rc = cli.main(["--task", "慢任务", "--json", "--timeout", "0.4"])
    elapsed = time.monotonic() - started

    assert rc == cli.EXIT_TIMEOUT
    assert exits == [cli.EXIT_TIMEOUT], "硬超时兜底没有被触发"
    assert elapsed < 3.0, f"超时未及时生效，耗时 {elapsed:.2f}s"
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["error"]["code"] == "timeout"


# ---------------------------------------------------------------------------
# 3b. 参数边界：这些以前全不校验，错误会被推迟到运行期并给出错误退出码
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_args, expect_code", [
    (["--max-steps", "0"], "invalid_argument"),
    (["--max-steps", "-3"], "invalid_argument"),
    (["--timeout", "0"], "invalid_argument"),
    (["--timeout", "-5"], "invalid_argument"),
    (["--cwd", "no/such/dir/at/all"], "invalid_argument"),
])
def test_invalid_boundaries_rejected(bad_args, expect_code, capsys):
    rc = cli.main(["--task", "x", "--dry-run", "--json", *bad_args])
    assert rc == cli.EXIT_USAGE, f"{bad_args} 应被拒绝，实际 rc={rc}"
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["error"]["code"] == expect_code


def test_hard_timeout_survives_blocking_sync_worker(monkeypatch, capsys):
    """核心回归：Agent 线程被同步阻塞时，--timeout 仍必须是硬保证。

    这里让假的 _run_agent 里真的跑一个阻塞 5s 的同步调用（模拟
    command_exec 的 subprocess.run），断言主线程在 0.5s 左右就返回，
    而不是被拖到 5s。
    """
    async def _blocking():
        time.sleep(5)
        return {"answer": "never", "steps": [], "cost_report": None}
    _patch_run_agent(monkeypatch, _blocking)

    exits = []
    monkeypatch.setattr(cli, "_hard_exit", lambda code: exits.append(code))

    started = time.monotonic()
    rc = cli.main(["--task", "阻塞任务", "--json", "--timeout", "0.5"])
    elapsed = time.monotonic() - started

    assert rc == cli.EXIT_TIMEOUT
    assert exits == [cli.EXIT_TIMEOUT]
    assert elapsed < 2.5, (f"阻塞工具把超时拖长了：{elapsed:.2f}s。"
                           "说明硬超时没有生效（又退化成软超时）。")


def test_runtime_exception_returns_1(monkeypatch, capsys):
    async def _boom():
        raise RuntimeError("炸了")
    _patch_run_agent(monkeypatch, _boom)

    rc = cli.main(["--task", "炸一下", "--json"])
    assert rc == cli.EXIT_TASK_FAILED
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["error"]["code"] == "runtime_error"
    assert "RuntimeError" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# 4. 两个致命问题的守门测试
# ---------------------------------------------------------------------------
def test_no_tty_does_not_hang():
    """回归：无 TTY 时 `python -m zeroai` 曾经永久挂死。

    修复后必须**快速失败**并给出可操作的提示，而不是吊死进程或 CI。
    """
    rc, out, err, elapsed = run_module([], timeout=30)
    assert rc == cli.EXIT_USAGE, f"期望退出码 2，实际 {rc}\n{err}"
    assert elapsed < 25, f"仍在挂死：耗时 {elapsed:.1f}s"
    assert "--task" in err or "--check" in err, f"未给出可操作提示：\n{err}"


def test_sync_blocking_tool_respects_timeout():
    """回归：同步阻塞工具曾会堵死事件循环，导致 asyncio.wait_for 超时失效。

    修复方式是把同步工具放到线程池执行。本用例用 `time.sleep` 模拟阻塞工具，
    断言 wait_for 能在远小于阻塞时长的前提下如期抛出 TimeoutError。
    """
    from zeroai.core.agent import AgentLoop
    from zeroai.core import agent as agent_mod

    class _FakePlanner:
        class _LLM:
            model = "glm-4.7-flash"
            cost_tracker = None
        llm = _LLM()

    def _blocking_tool():
        time.sleep(2.0)   # 阻塞工具：卡住整个线程
        return "done"

    loop = AgentLoop(
        planner=_FakePlanner(),
        tool_map={"blocking": _blocking_tool},
        tools_schema=[],
        max_steps=1,
        enable_checkpoint=False,
        enable_cost_tracking=False,
        enable_session=False,
        enable_task_manager=False,
    )

    async def _scenario():
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(loop._execute_tool("blocking", {}), timeout=0.3)
        return time.monotonic() - started

    elapsed = asyncio.run(_scenario())
    assert elapsed < 1.5, (f"同步工具仍在阻塞事件循环：耗时 {elapsed:.2f}s，"
                           "说明 asyncio.to_thread 修复未生效")


# ---------------------------------------------------------------------------
# 5. Action Fusion 与 diff 审批（SoL-Pi 对标 / OpenCode 对标）
# ---------------------------------------------------------------------------
def _make_fake_planner(actions):
    """按顺序返回预置 plan 的伪规划器"""
    from zeroai.core.agent import ReActPlanner  # noqa: F401  确认可导入

    class _FakePlanner:
        class _LLM:
            model = "glm-4.7-flash"
            cost_tracker = None
        llm = _LLM()

        def __init__(self, acts):
            self._acts = list(acts)
            self._n = 0

        async def plan_next(self, **kwargs):
            self._n += 1
            return self._acts[min(self._n, len(self._acts)) - 1]

    return _FakePlanner(actions)


def _make_loop(planner, tool_map, **extra):
    from zeroai.core.agent import AgentLoop
    return AgentLoop(
        planner=planner, tool_map=tool_map, tools_schema=[], max_steps=4,
        enable_checkpoint=False, enable_cost_tracking=False,
        enable_session=False, enable_task_manager=False, **extra)


def test_action_fusion_merges_follow_up_command():
    """fusion 开启时：edit 工具携带 follow_up_command → 同步内执行并合并结果"""
    executed = []

    def edit_file(**kwargs):
        return "写入成功: x.txt"

    def run_command(command, skip_translate=False):
        executed.append(command)
        return "[exit 0] hi"

    plan_edit = {"thought": "写文件", "task_complete": False,
                 "next_action": {"type": "tool_call", "tool": "edit_file",
                                 "args": {"path": "x.txt", "content": "hi",
                                          "follow_up_command": "echo hi"}}}
    plan_done = {"thought": "完成", "task_complete": True,
                 "next_action": {"type": "final_answer", "answer": "ok"}}
    loop = _make_loop(_make_fake_planner([plan_edit, plan_done]),
                      {"edit_file": edit_file, "run_command": run_command},
                      enable_action_fusion=True)

    async def _scenario():
        return await loop.run("写个文件", [])

    answer, steps = asyncio.run(_scenario())
    assert answer == "ok"
    assert executed == ["echo hi"], f"跟进命令未执行: {executed}"
    fused = [s for s in steps if "[Action Fusion 跟进验证-通过]" in str(s.get("result", ""))]
    assert fused, "验证结果未合并进同一次 observation"


def test_action_fusion_disabled_by_default():
    """fusion 默认关闭：follow_up_command 被当作普通幻觉参数过滤，不执行"""
    executed = []

    def edit_file(**kwargs):
        return "写入成功: x.txt"

    def run_command(command, skip_translate=False):
        executed.append(command)
        return "[exit 0] hi"

    plan_edit = {"thought": "写文件", "task_complete": False,
                 "next_action": {"type": "tool_call", "tool": "edit_file",
                                 "args": {"path": "x.txt", "content": "hi",
                                          "follow_up_command": "echo hi"}}}
    plan_done = {"thought": "完成", "task_complete": True,
                 "next_action": {"type": "final_answer", "answer": "ok"}}
    loop = _make_loop(_make_fake_planner([plan_edit, plan_done]),
                      {"edit_file": edit_file, "run_command": run_command})

    async def _scenario():
        return await loop.run("写个文件", [])

    answer, steps = asyncio.run(_scenario())
    assert answer == "ok"
    assert executed == [], "fusion 默认关闭时不应执行跟进命令"
    assert all("[Action Fusion" not in str(s.get("result", "")) for s in steps)


def test_diff_review_fail_closed_in_noninteractive(monkeypatch):
    """diff 审批回调：非交互终端必须 fail-closed 自动拒绝"""
    import zeroai.cli as cli
    import zeroai.main as zmain

    monkeypatch.setattr(zmain, "_is_console_stream", lambda stream: False)
    cb = cli._make_diff_review_callback(quiet=True)
    assert cb("edit_file", {"path": "secret.txt"}) is False


def test_diff_review_prompt_approve_and_deny(monkeypatch):
    """diff 审批回调：交互终端下 y 批准 / 其他拒绝"""
    import builtins
    import zeroai.cli as cli
    import zeroai.main as zmain

    monkeypatch.setattr(zmain, "_is_console_stream", lambda stream: True)
    cb = cli._make_diff_review_callback(quiet=True)

    monkeypatch.setattr(builtins, "input", lambda *a, **k: "y")
    assert cb("edit_file", {"path": "a.txt"}) is True

    monkeypatch.setattr(builtins, "input", lambda *a, **k: "n")
    assert cb("edit_file", {"path": "a.txt"}) is False

    def _boom(*a, **k):
        raise EOFError
    monkeypatch.setattr(builtins, "input", _boom)
    assert cb("edit_file", {"path": "a.txt"}) is False


def test_observation_pack_archives_large_output(tmp_path):
    """pack 开启时：大输出完整落盘，observation 替换为预览+归档路径"""
    big = "L" * 5000
    plan_tool = {"thought": "跑命令", "task_complete": False,
                 "next_action": {"type": "tool_call", "tool": "run_command",
                                 "args": {"command": "big_dump"}}}
    plan_done = {"thought": "完成", "task_complete": True,
                 "next_action": {"type": "final_answer", "answer": "done"}}
    loop = _make_loop(_make_fake_planner([plan_tool, plan_done]),
                      {"run_command": lambda command, skip_translate=False: big},
                      enable_observation_pack=True)
    loop.workspace_root = str(tmp_path)

    async def _scenario():
        return await loop.run("看大输出", [])

    answer, steps = asyncio.run(_scenario())
    assert answer == "done"
    packed = [s for s in steps if "[ObservationPack]" in str(s.get("result", ""))]
    assert packed, "大输出未被替换为归档句柄"
    packed_result = packed[0]["result"]
    assert "完整归档" in packed_result and "read_file" in packed_result
    # 头部预览 + 归档路径
    assert packed_result.startswith("L")
    # 归档文件存在且**零截断**
    archive_line = [l for l in packed_result.splitlines() if l.startswith("已完整归档") or "已完整归档: " in l]
    assert archive_line, "未给出归档路径"
    import re
    m = re.search(r"已完整归档: (\S+\.txt)", packed_result)
    assert m, f"归档路径解析失败:\n{packed_result[-300:]}"
    archived = open(m.group(1), encoding="utf-8").read()
    assert archived == big, f"归档不完整: {len(archived)} vs {len(big)}"


def test_observation_pack_disabled_by_default(tmp_path):
    """pack 默认关闭：大输出原样进入 observation"""
    big = "K" * 5000
    plan_tool = {"thought": "跑命令", "task_complete": False,
                 "next_action": {"type": "tool_call", "tool": "run_command",
                                 "args": {"command": "big_dump"}}}
    plan_done = {"thought": "完成", "task_complete": True,
                 "next_action": {"type": "final_answer", "answer": "done"}}
    loop = _make_loop(_make_fake_planner([plan_tool, plan_done]),
                      {"run_command": lambda command, skip_translate=False: big})
    loop.workspace_root = str(tmp_path)

    async def _scenario():
        return await loop.run("看大输出", [])

    answer, steps = asyncio.run(_scenario())
    assert answer == "done"
    assert all("[ObservationPack]" not in str(s.get("result", "")) for s in steps)
    assert not (tmp_path / ".zeroai" / "observation_pack").exists()


def test_context_compact_compresses_old_results():
    """compact 开启 + 窗口压力：旧工具结果被压缩为收据，完整原文留在 executed_steps"""
    big = "M" * 4000
    plans = [
        {"thought": "跑命令1", "task_complete": False,
         "next_action": {"type": "tool_call", "tool": "run_command",
                         "args": {"command": "dump1"}}},
        {"thought": "跑命令2", "task_complete": False,
         "next_action": {"type": "tool_call", "tool": "run_command",
                         "args": {"command": "dump2"}}},
        {"thought": "跑命令3", "task_complete": False,
         "next_action": {"type": "tool_call", "tool": "run_command",
                         "args": {"command": "dump3"}}},
        {"thought": "完成", "task_complete": True,
         "next_action": {"type": "final_answer", "answer": "done"}},
    ]
    loop = _make_loop(_make_fake_planner(plans),
                      {"run_command": lambda command, skip_translate=False: big},
                      enable_context_compact=True,
                      context_compact_threshold=3000)

    async def _scenario():
        messages = []
        return await loop.run("跑三条命令", messages), messages

    (answer, steps), messages = asyncio.run(_scenario())
    assert answer == "done"
    tool_steps = [s for s in steps if s.get("action_type") == "tool_call"]
    assert len(tool_steps) == 3
    # 完整原文在 executed_steps 中（证据红线）
    assert all(s["result"] == big for s in tool_steps), "executed_steps 必须保留完整原文"
    # messages 中较旧结果被压缩，最近 4 条不动
    receipt = [m for m in messages if "[ContextCompact]" in str(m.get("content", ""))]
    assert receipt, "旧工具结果未被压缩"
    full_kept = [m for m in messages if str(m.get("content", "")) == f"[工具结果 run_command] {big}"]
    assert len(full_kept) <= 2, "最近 4 条消息之外不应保留完整结果"


def test_context_compact_disabled_by_default():
    """compact 默认关闭：无压缩收据"""
    big = "M" * 4000
    plans = [
        {"thought": "跑命令1", "task_complete": False,
         "next_action": {"type": "tool_call", "tool": "run_command",
                         "args": {"command": "dump1"}}},
        {"thought": "跑命令2", "task_complete": False,
         "next_action": {"type": "tool_call", "tool": "run_command",
                         "args": {"command": "dump2"}}},
        {"thought": "完成", "task_complete": True,
         "next_action": {"type": "final_answer", "answer": "done"}},
    ]
    loop = _make_loop(_make_fake_planner(plans),
                      {"run_command": lambda command, skip_translate=False: big})

    async def _scenario():
        messages = []
        return await loop.run("跑两条命令", messages), messages

    (answer, steps), messages = asyncio.run(_scenario())
    assert answer == "done"
    assert all("[ContextCompact]" not in str(m.get("content", "")) for m in messages)
