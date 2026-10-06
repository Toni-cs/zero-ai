"""审计日志落盘回归测试。

背景（2026-09-16 审计发现）
--------------------------------------------------------------------------
`MCPAuditLogger.record()` 原先这样调度磁盘写入：

    try:
        asyncio.ensure_future(self._write_to_disk(record))
    except RuntimeError:
        pass          # "没有事件循环（同步上下文），跳过磁盘写入"

实测有两个缺陷：
  1. **同步上下文静默丢记录**：线程内无事件循环时 ensure_future 抛 RuntimeError，
     被吞掉后协程对象从未 await —— 记录只进内存、永不落盘。
     这正是 pytest 里那条 `RuntimeWarning: coroutine ... was never awaited` 的来源。
  2. **Task 无强引用**：asyncio 只持弱引用，任务可能在完成前被 GC 回收
     （"Task was destroyed but it is pending!"），同样丢记录。

审计日志是安全相关产物，静默丢记录不可接受。
现改为：有运行中的循环 -> create_task + 强引用；否则 -> 同步落盘兜底。
"""
import asyncio
import gc
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zeroai.mcp.audit import MCPAuditLogger  # noqa: E402


def _mk(tmp_path, name="a") -> MCPAuditLogger:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return MCPAuditLogger(log_dir=d)


def _records(logger: MCPAuditLogger):
    return sorted(logger._log_dir.glob("*.jsonl"))


def _emit(logger: MCPAuditLogger, tool="t"):
    return logger.record(
        server_name="s", tool_name=tool, full_tool_name=tool,
        arguments={"a": 1}, success=True, duration=0.1,
        result_length=3, error_message="", result_preview="abc",
        caller="test",
    )


# ───────────────────── T1：同步上下文必须落盘（核心缺陷） ─────────────────────

def test_sync_context_writes_to_disk(tmp_path):
    """T1 无事件循环时也必须落盘 —— 这是原先静默丢记录的那条路径"""
    lg = _mk(tmp_path, "sync")
    _emit(lg)
    assert len(lg._buffer) == 1, "内存缓冲未记录"
    files = _records(lg)
    assert files, "同步上下文下审计记录未落盘（缺陷复现）"
    assert "s" in files[0].read_text(encoding="utf-8")


def test_sync_context_emits_no_never_awaited_warning(tmp_path):
    """T2 同步路径不得再产生 'coroutine ... was never awaited' 警告"""
    lg = _mk(tmp_path, "warn")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _emit(lg)
        gc.collect()
    bad = [str(w.message) for w in caught if "never awaited" in str(w.message)]
    assert not bad, f"仍存在未 await 的协程警告: {bad}"


# ───────────────────── T3：异步上下文 ─────────────────────

def test_async_context_writes_to_disk(tmp_path):
    """T3 有运行中的事件循环时正常落盘（生产 Textual 常态）"""
    lg = _mk(tmp_path, "async")

    async def run():
        _emit(lg)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert _records(lg), "异步上下文下审计记录未落盘"


def test_pending_task_is_strongly_referenced(tmp_path):
    """T4 在途 Task 必须被强引用，避免被 GC 回收导致丢记录"""
    lg = _mk(tmp_path, "ref")

    async def run():
        _emit(lg)
        # 调度后立刻检查：应有一个在途 Task 被持有
        assert lg._pending, "未持有在途 Task 的强引用"
        await asyncio.sleep(0.05)
        # 完成后应从集合中移除（回调 discard），避免无界增长
        assert not lg._pending, f"完成的 Task 未清理: {lg._pending}"

    asyncio.run(run())
    assert _records(lg)


def test_no_task_destroyed_warning(tmp_path):
    """T5 不得出现 'Task was destroyed but it is pending!'"""
    lg = _mk(tmp_path, "destroyed")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _emit(lg)
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        loop.close()
    assert _records(lg), "记录未落盘"


# ───────────────────── T6：既有行为不能回归 ─────────────────────

def test_multi_records_all_persist(tmp_path):
    """T6 多条记录全部落盘（含异步顺序）"""
    lg = _mk(tmp_path, "multi")

    async def run():
        for i in range(5):
            _emit(lg, tool=f"tool{i}")
        await asyncio.sleep(0.1)

    asyncio.run(run())
    content = "".join(p.read_text(encoding="utf-8") for p in _records(lg))
    for i in range(5):
        assert f"tool{i}" in content, f"tool{i} 未落盘"


def test_disk_failure_does_not_raise(tmp_path, monkeypatch):
    """T7 落盘失败不得影响主流程（审计不阻断业务）"""
    lg = _mk(tmp_path, "fail")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    _emit(lg)  # 不应抛出
    assert len(lg._buffer) == 1


def test_rotation_still_works(tmp_path):
    """T8 文件轮转逻辑在重构后仍生效"""
    lg = MCPAuditLogger(log_dir=tmp_path / "rot", max_file_size_mb=0.0001)
    (tmp_path / "rot").mkdir(parents=True, exist_ok=True)
    # 写入足够多的记录以触发轮转
    for i in range(30):
        _emit(lg, tool=f"x{i}" * 50)
    assert len(_records(lg)) >= 1, "轮转后找不到日志文件"
