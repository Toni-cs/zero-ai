"""SessionManager 单元测试

测试 zeroai/core/session.py 的核心功能：
- save_session / load_session 保存与加载
- save_session 后自动设为当前会话
- list_sessions 列出所有会话
- delete_session 删除会话
"""
import os
import tempfile

import pytest

from zeroai.core.session import SessionManager, SessionData, SessionInfo


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def session_mgr():
    """创建使用临时目录的 SessionManager 实例"""
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SessionManager(sessions_dir=tmpdir)
        yield mgr


# ============================================================================
# 测试用例
# ============================================================================

def test_save_and_load(session_mgr):
    """保存会话后加载，验证内容一致"""
    sid = session_mgr.create_session_id()
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你的？"},
    ]
    executed_steps = [{"tool": "search", "result": "ok"}]
    metadata = {"model": "gpt-4o", "temperature": 0.7}

    result = session_mgr.save_session(
        sid,
        messages=messages,
        executed_steps=executed_steps,
        metadata=metadata,
    )
    assert result.success, f"保存失败：{result.message}"

    data = session_mgr.load_session(sid)
    assert data is not None, "加载会话不应返回 None"
    assert isinstance(data, SessionData)
    assert data.session_id == sid
    assert data.messages == messages, "加载的 messages 应与保存的一致"
    assert data.executed_steps == executed_steps, "加载的 executed_steps 应与保存的一致"
    assert data.metadata == metadata, "加载的 metadata 应与保存的一致"


def test_save_sets_current(session_mgr):
    """保存会话后验证该会话被设为当前会话"""
    sid = session_mgr.create_session_id()
    result = session_mgr.save_session(
        sid, messages=[{"role": "user", "content": "测试消息"}]
    )
    assert result.success, f"保存失败：{result.message}"

    # 通过 _read_current 读取当前会话 ID
    current = session_mgr._read_current()
    assert current == sid, f"当前会话应为 {sid}，实际 {current}"

    # 也通过 get_or_create_current 验证（不应创建新会话）
    current2 = session_mgr.get_or_create_current()
    assert current2 == sid, f"get_or_create_current 应返回 {sid}，实际 {current2}"


def test_list_sessions(session_mgr):
    """创建多个会话，验证列表返回正确"""
    sid1 = session_mgr.create_session_id()
    session_mgr.save_session(sid1, messages=[{"role": "user", "content": "第一条"}])

    sid2 = session_mgr.create_session_id()
    session_mgr.save_session(sid2, messages=[{"role": "user", "content": "第二条"}])

    sid3 = session_mgr.create_session_id()
    session_mgr.save_session(sid3, messages=[{"role": "user", "content": "第三条"}])

    sessions = session_mgr.list_sessions()
    assert len(sessions) == 3, f"应有 3 个会话，实际 {len(sessions)}"

    # 验证返回的是 SessionInfo 对象
    for info in sessions:
        assert isinstance(info, SessionInfo)

    # 验证所有会话 ID 都在列表中
    session_ids = {info.session_id for info in sessions}
    assert sid1 in session_ids
    assert sid2 in session_ids
    assert sid3 in session_ids


def test_delete_session(session_mgr):
    """删除会话，验证文件不存在"""
    sid = session_mgr.create_session_id()
    result = session_mgr.save_session(
        sid, messages=[{"role": "user", "content": "待删除"}]
    )
    assert result.success

    # 确认会话存在
    data = session_mgr.load_session(sid)
    assert data is not None, "删除前会话应存在"

    # 删除
    deleted = session_mgr.delete_session(sid)
    assert deleted, "delete_session 应返回 True"

    # 验证会话已不存在
    data_after = session_mgr.load_session(sid)
    assert data_after is None, "删除后会话应不存在"

    # 验证文件确实被删除
    file_path = session_mgr._session_file(sid)
    assert not file_path.exists(), f"会话文件应已删除：{file_path}"
