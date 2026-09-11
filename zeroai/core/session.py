"""会话持久化与恢复系统

参考 OpenCode 的 session management 特性，提供完整的会话状态保存和恢复：

使用方式：
    mgr = SessionManager()
    sid = mgr.create_session_id()
    mgr.save_session(sid, messages=[{"role": "user", "content": "你好"}])
    # ... 程序重启 ...
    data = mgr.load_session(sid)
    messages = data.messages  # 恢复对话历史
"""
from __future__ import annotations

import json
import os
import random
import string
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================================
# 常量
# ============================================================================

# 会话文件格式版本（支持未来格式升级）
SESSION_VERSION = "1.0"

# 默认会话存储目录：~/.zeroai/sessions/
DEFAULT_SESSIONS_DIR = str(Path.home() / ".zeroai" / "sessions")

# 当前会话 ID 文件名（存储在 sessions 目录下）
CURRENT_SESSION_FILE = ".current"

# 工具输出截断阈值：保留前 N 字符 + 截断标记
TOOL_OUTPUT_MAX_LEN = 2000

# 截断标记（追加在被截断的字符串末尾）
TRUNCATION_MARKER = "\n...[truncated]..."

# session_id 随机部分长度
SESSION_ID_RANDOM_LEN = 6

# session_id 前缀
SESSION_ID_PREFIX = "sess"

# 预览文本最大长度
PREVIEW_MAX_LEN = 100

# 跨实例保护 .current 文件写入的全局锁
_global_lock = threading.RLock()


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class SessionData:
    """完整会话数据（load_session 的返回类型）"""
    session_id: str
    messages: List[Dict[str, Any]]
    executed_steps: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    created_at: float
    updated_at: float
    version: str


@dataclass
class SessionInfo:
    """会话摘要信息（list_sessions 的返回元素）"""
    session_id: str
    created_at: float
    updated_at: float
    message_count: int
    preview: str
    metadata: Dict[str, Any]


@dataclass
class SaveResult:
    """保存操作结果"""
    success: bool
    session_id: str
    file_path: str
    message: str


# ============================================================================
# 内部工具函数
# ============================================================================

def _truncate_tool_output(obj: Any, max_len: int = TOOL_OUTPUT_MAX_LEN) -> Any:
    """递归截断过长的字符串（主要用于工具输出）。

    深度遍历 dict / list 结构，对超过 max_len 的字符串值截断，
    保留前 max_len 字符并追加截断标记。不修改原对象。

    Args:
        obj: 任意 JSON 可序列化对象
        max_len: 字符串最大保留长度

    Returns:
        截断后的对象（深拷贝）
    """
    if isinstance(obj, str):
        if len(obj) > max_len:
            return obj[:max_len] + TRUNCATION_MARKER
        return obj
    if isinstance(obj, dict):
        return {k: _truncate_tool_output(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_tool_output(item, max_len) for item in obj]
    return obj


def _generate_session_id() -> str:
    """生成会话 ID：sess_YYYYMMDD_HHMMSS_随机6字符"""
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    alphabet = string.ascii_lowercase + string.digits
    random_part = "".join(random.choices(alphabet, k=SESSION_ID_RANDOM_LEN))
    return f"{SESSION_ID_PREFIX}_{timestamp}_{random_part}"


def _extract_preview(messages: List[Dict[str, Any]], max_len: int = PREVIEW_MAX_LEN) -> str:
    """从消息列表提取预览文本（第一条 user 消息的 content）。

    兼容纯文本 content 和多模态 content（list of parts）。

    Args:
        messages: 对话历史
        max_len: 预览最大长度

    Returns:
        预览文本；无 user 消息时返回空串
    """
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # 多模态消息：拼接所有 text 类型部分
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    parts.append(part)
            text = " ".join(parts)
        else:
            continue
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text
    return ""


# ============================================================================
# SessionManager
# ============================================================================

class SessionManager:
    """会话持久化管理器

    提供完整的会话状态保存和恢复能力，参考 OpenCode 的 session management 特性。

    特性：
    - 会话文件存储为 JSON：~/.zeroai/sessions/{session_id}.json
    - 当前会话 ID 存储在 ~/.zeroai/sessions/.current
    - 原子写入（临时文件 + os.replace），线程安全
    - 自动截断过长的工具输出（保留前 2000 字符 + 截断标记）
    - 支持中文消息内容（ensure_ascii=False）
    - list_sessions 按更新时间倒序

    使用方式：
        mgr = SessionManager()
        sid = mgr.create_session_id()
        mgr.save_session(sid, messages=[{"role": "user", "content": "你好"}])
        data = mgr.load_session(sid)
    """

    def __init__(self, sessions_dir: Optional[str] = None):
        """初始化会话管理器。

        Args:
            sessions_dir: 会话存储目录，默认 ~/.zeroai/sessions/
        """
        if sessions_dir is None:
            sessions_dir = DEFAULT_SESSIONS_DIR
        self._sessions_dir = Path(sessions_dir)
        # 确保目录存在
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        # 实例级锁（保护本实例并发调用）；全局锁保护跨实例的 .current 写入
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 路径属性
    # ------------------------------------------------------------------

    @property
    def sessions_dir(self) -> Path:
        """会话存储目录"""
        return self._sessions_dir

    @property
    def current_file(self) -> Path:
        """当前会话 ID 文件路径"""
        return self._sessions_dir / CURRENT_SESSION_FILE

    def _session_file(self, session_id: str) -> Path:
        """获取指定会话的文件路径"""
        return self._sessions_dir / f"{session_id}.json"

    # ------------------------------------------------------------------
    # 原子写入
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write(file_path: Path, content: str) -> None:
        """原子写入文本文件（临时文件 + os.replace）。

        在目标文件同目录创建临时文件（保证同一文件系统，rename 才原子），
        写入完成后用 os.replace 原子覆盖目标文件。os.replace 在 Windows 和
        Unix 上均能原子地覆盖已存在的目标。

        Args:
            file_path: 目标文件路径
            content: 文本内容

        Raises:
            OSError: 写入失败
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=".tmp_",
            dir=str(file_path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            # os.replace 原子覆盖（跨平台支持目标已存在）
            os.replace(tmp_path, str(file_path))
        except Exception:
            # 失败时清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _atomic_write_json(self, file_path: Path, data: Dict[str, Any]) -> None:
        """原子写入 JSON 文件。

        Args:
            file_path: 目标文件路径
            data: 要写入的字典
        """
        content = json.dumps(data, ensure_ascii=False, indent=2)
        self._atomic_write(file_path, content)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def create_session_id(self) -> str:
        """生成新的会话 ID。

        Returns:
            格式为 sess_YYYYMMDD_HHMMSS_随机6字符 的会话 ID
        """
        return _generate_session_id()

    def save_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        executed_steps: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SaveResult:
        """保存会话到磁盘。

        - 自动截断过长的工具输出（保留前 2000 字符 + 截断标记）
        - 原子写入，线程安全
        - 若会话已存在，保留原 created_at；否则用当前时间
        - 保存成功后自动将其设为当前会话

        Args:
            session_id: 会话 ID
            messages: 对话历史（role/content）
            executed_steps: 已执行步骤列表（可选）
            metadata: 额外元数据（可选）

        Returns:
            SaveResult 包含成功状态、文件路径和消息
        """
        if not session_id:
            return SaveResult(
                success=False,
                session_id="",
                file_path="",
                message="session_id 不能为空",
            )

        with self._lock:
            now = time.time()
            file_path = self._session_file(session_id)

            # 读取已有会话以保留 created_at
            created_at = now
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        created_at = float(existing.get("created_at", now))
                except (OSError, json.JSONDecodeError, ValueError, TypeError):
                    created_at = now

            # 截断过长的工具输出（深拷贝，不修改入参）
            truncated_messages = _truncate_tool_output(messages)
            truncated_steps = _truncate_tool_output(executed_steps or [])
            truncated_metadata = _truncate_tool_output(metadata or {})

            payload = {
                "session_id": session_id,
                "messages": truncated_messages,
                "executed_steps": truncated_steps,
                "metadata": truncated_metadata,
                "created_at": created_at,
                "updated_at": now,
                "version": SESSION_VERSION,
            }

            try:
                self._atomic_write_json(file_path, payload)
            except OSError as e:
                return SaveResult(
                    success=False,
                    session_id=session_id,
                    file_path=str(file_path),
                    message=f"保存失败: {e}",
                )
            except Exception as e:  # noqa: BLE001 - 兜底，保证不抛出
                return SaveResult(
                    success=False,
                    session_id=session_id,
                    file_path=str(file_path),
                    message=f"保存失败: {e}",
                )

            # 保存成功后自动将其设为当前会话（兑现 docstring 承诺）
            self._write_current(session_id)

            return SaveResult(
                success=True,
                session_id=session_id,
                file_path=str(file_path),
                message="保存成功",
            )

    def load_session(self, session_id: str) -> Optional[SessionData]:
        """从磁盘加载会话。

        Args:
            session_id: 会话 ID

        Returns:
            SessionData；文件不存在或损坏时返回 None
        """
        if not session_id:
            return None

        with self._lock:
            file_path = self._session_file(session_id)
            if not file_path.exists():
                return None
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                return None

            if not isinstance(data, dict):
                return None

            try:
                return SessionData(
                    session_id=data.get("session_id", session_id),
                    messages=data.get("messages", []) or [],
                    executed_steps=data.get("executed_steps", []) or [],
                    metadata=data.get("metadata", {}) or {},
                    created_at=float(data.get("created_at", 0.0)),
                    updated_at=float(data.get("updated_at", 0.0)),
                    version=str(data.get("version", "unknown")),
                )
            except (TypeError, ValueError):
                return None

    def list_sessions(self) -> List[SessionInfo]:
        """列出所有会话，按更新时间倒序排列（最新的在前）。

        跳过隐藏文件（以 . 开头）和非 .json 文件。损坏的文件会被静默跳过。

        Returns:
            SessionInfo 列表
        """
        with self._lock:
            infos: List[SessionInfo] = []
            try:
                entries = list(self._sessions_dir.iterdir())
            except OSError:
                return infos

            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    if entry.name.startswith(".") or not entry.name.endswith(".json"):
                        continue
                    with open(entry, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                try:
                    messages = data.get("messages", [])
                    if not isinstance(messages, list):
                        messages = []
                    meta = data.get("metadata", {})
                    if not isinstance(meta, dict):
                        meta = {}
                    infos.append(SessionInfo(
                        session_id=data.get("session_id", entry.stem),
                        created_at=float(data.get("created_at", 0.0)),
                        updated_at=float(data.get("updated_at", 0.0)),
                        message_count=len(messages),
                        preview=_extract_preview(messages),
                        metadata=meta,
                    ))
                except (TypeError, ValueError):
                    continue

            # 按更新时间倒序
            infos.sort(key=lambda x: x.updated_at, reverse=True)
            return infos

    def delete_session(self, session_id: str) -> bool:
        """删除会话文件。

        若被删除的是当前会话，同时清除 .current 文件。

        Args:
            session_id: 会话 ID

        Returns:
            True 删除成功；False 文件不存在或删除失败
        """
        if not session_id:
            return False

        with self._lock:
            file_path = self._session_file(session_id)
            if not file_path.exists():
                return False
            try:
                file_path.unlink()
            except OSError:
                return False

            # 若删除的是当前会话，清除 .current
            try:
                if self.current_file.exists():
                    current_id = self.current_file.read_text(encoding="utf-8").strip()
                    if current_id == session_id:
                        self.current_file.unlink()
            except OSError:
                pass
            return True

    def get_or_create_current(self) -> str:
        """获取当前会话 ID；若不存在或对应文件已丢失则创建新会话并设为当前。

        Returns:
            当前会话 ID
        """
        with self._lock:
            session_id = self._read_current()
            if session_id and self._session_file(session_id).exists():
                return session_id
            # 创建新会话并设为当前
            new_id = self.create_session_id()
            self._write_current(new_id)
            return new_id

    def update_current(self, session_id: str) -> None:
        """设置当前会话 ID。

        Args:
            session_id: 要设为当前的会话 ID
        """
        if not session_id:
            return
        with self._lock:
            self._write_current(session_id)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _read_current(self) -> str:
        """读取当前会话 ID（不存在或读取失败返回空串）"""
        try:
            if self.current_file.exists():
                return self.current_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        return ""

    def _write_current(self, session_id: str) -> None:
        """原子写入当前会话 ID（纯文本）"""
        with _global_lock:
            try:
                self._atomic_write(self.current_file, session_id)
            except OSError:
                # 回退：直接写入（尽力而为）
                try:
                    self.current_file.write_text(session_id, encoding="utf-8")
                except OSError:
                    pass


__all__ = [
    "SessionManager",
    "SessionData",
    "SessionInfo",
    "SaveResult",
    "SESSION_VERSION",
    "DEFAULT_SESSIONS_DIR",
    "CURRENT_SESSION_FILE",
    "TOOL_OUTPUT_MAX_LEN",
    "TRUNCATION_MARKER",
]
