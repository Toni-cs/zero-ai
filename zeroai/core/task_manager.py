"""任务/TODO 管理系统

参考 OpenCode 的 task management 特性，提供 Agent 内置的任务追踪能力：

使用方式：
    mgr = TaskManager()
    task = mgr.create_task("实现登录功能", "包括前端和后端",
                          subtasks=["设计API", "写前端", "写后端", "测试"])
    mgr.complete_task(task.id)
    print(mgr.format_status())
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ============================================================================
# 枚举定义
# ============================================================================

class TaskStatus(Enum):
    """任务状态"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """任务优先级（数值越大优先级越高，用于排序）"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> int:
        """排序权重：CRITICAL > HIGH > NORMAL > LOW"""
        return {
            TaskPriority.LOW: 0,
            TaskPriority.NORMAL: 1,
            TaskPriority.HIGH: 2,
            TaskPriority.CRITICAL: 3,
        }[self]


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class SubTask:
    """子任务（可独立勾选完成）"""

    id: str
    title: str
    completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SubTask":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            completed=d.get("completed", False),
        )


@dataclass
class Task:
    """任务"""

    id: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    subtasks: List[SubTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        # 枚举值无效时优雅降级到默认值，避免抛出 ValueError
        try:
            status = TaskStatus(d.get("status", TaskStatus.PENDING.value))
        except ValueError:
            status = TaskStatus.PENDING

        try:
            priority = TaskPriority(d.get("priority", TaskPriority.NORMAL.value))
        except ValueError:
            priority = TaskPriority.NORMAL

        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            status=status,
            priority=priority,
            subtasks=[SubTask.from_dict(st) for st in d.get("subtasks", [])],
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            completed_at=d.get("completed_at"),
        )

    @property
    def subtask_progress(self) -> tuple:
        """返回 (已完成数, 总数)"""
        total = len(self.subtasks)
        done = sum(1 for st in self.subtasks if st.completed)
        return (done, total)


@dataclass
class TaskProgress:
    """任务进度统计"""

    total: int
    pending: int
    in_progress: int
    completed: int
    cancelled: int
    completion_rate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "pending": self.pending,
            "in_progress": self.in_progress,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "completion_rate": self.completion_rate,
        }


# ============================================================================
# ANSI 颜色常量（用于 format_status 彩色输出）
# ============================================================================

_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_RED = "\033[31m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_BLUE = "\033[34m"
_ANSI_MAGENTA = "\033[35m"
_ANSI_CYAN = "\033[36m"
_ANSI_GRAY = "\033[90m"

# 状态对应的颜色与符号
_STATUS_STYLE: Dict[TaskStatus, tuple] = {
    TaskStatus.PENDING: (_ANSI_YELLOW, "○"),
    TaskStatus.IN_PROGRESS: (_ANSI_BLUE, "◐"),
    TaskStatus.COMPLETED: (_ANSI_GREEN, "●"),
    TaskStatus.CANCELLED: (_ANSI_GRAY, "✕"),
}

# 优先级对应的颜色
_PRIORITY_STYLE: Dict[TaskPriority, str] = {
    TaskPriority.LOW: _ANSI_GRAY,
    TaskPriority.NORMAL: _ANSI_CYAN,
    TaskPriority.HIGH: _ANSI_YELLOW,
    TaskPriority.CRITICAL: _ANSI_RED,
}


# ============================================================================
# 任务管理器
# ============================================================================

class TaskManager:
    """任务/TODO 管理器

    线程安全。提供任务的创建、更新、完成、取消、查询与进度统计能力。
    任务 ID 格式为 ``task_NNN``（自增序号，至少 3 位，超出自动扩展）。
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}
        self._counter: int = 0
        self._lock = threading.RLock()

    # ----------------------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------------------

    def _next_id(self) -> str:
        """生成下一个任务 ID（task_NNN 格式，至少 3 位）"""
        self._counter += 1
        return f"task_{self._counter:03d}"

    @staticmethod
    def _now() -> float:
        return time.time()

    def _sorted_tasks(self, tasks: List[Task]) -> List[Task]:
        """按优先级降序排序（CRITICAL > HIGH > NORMAL > LOW），同优先级按创建时间升序"""
        return sorted(
            tasks,
            key=lambda t: (-t.priority.weight, t.created_at),
        )

    # ----------------------------------------------------------------------
    # 创建 / 更新 / 完成 / 取消
    # ----------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        subtasks: Optional[List[str]] = None,
    ) -> Task:
        """创建一个新任务

        Args:
            title: 任务标题
            description: 任务描述（可选）
            priority: 优先级，默认 NORMAL
            subtasks: 子任务标题列表（可选），每个元素为子任务标题字符串

        Returns:
            创建的 Task 对象
        """
        with self._lock:
            task_id = self._next_id()
            now = self._now()
            subtask_list: List[SubTask] = []
            if subtasks:
                for idx, st_title in enumerate(subtasks, start=1):
                    subtask_list.append(
                        SubTask(
                            id=f"{task_id}.sub_{idx:02d}",
                            title=str(st_title),
                            completed=False,
                        )
                    )
            task = Task(
                id=task_id,
                title=title,
                description=description,
                status=TaskStatus.PENDING,
                priority=priority,
                subtasks=subtask_list,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            self._tasks[task_id] = task
            return task

    def update_task(self, task_id: str, **kwargs: Any) -> Optional[Task]:
        """更新任务属性

        支持的可更新字段：
            - title: str
            - description: str
            - priority: TaskPriority
            - status: TaskStatus
            - subtasks: List[str]（用新标题列表替换现有子任务）

        未识别的字段将被忽略。返回更新后的 Task，若任务不存在返回 None。
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            changed = False
            if "title" in kwargs and kwargs["title"] is not None:
                task.title = str(kwargs["title"])
                changed = True
            if "description" in kwargs and kwargs["description"] is not None:
                task.description = str(kwargs["description"])
                changed = True
            if "priority" in kwargs and kwargs["priority"] is not None:
                prio = kwargs["priority"]
                if isinstance(prio, str):
                    prio = TaskPriority(prio)
                task.priority = prio
                changed = True
            if "status" in kwargs and kwargs["status"] is not None:
                status = kwargs["status"]
                if isinstance(status, str):
                    status = TaskStatus(status)
                task.status = status
                if status == TaskStatus.COMPLETED and task.completed_at is None:
                    task.completed_at = self._now()
                if status != TaskStatus.COMPLETED:
                    task.completed_at = None
                changed = True
            if "subtasks" in kwargs and kwargs["subtasks"] is not None:
                new_titles = kwargs["subtasks"]
                new_list: List[SubTask] = []
                # 构建已有子任务标题到完成状态的映射，保留已完成的子任务状态
                existing_completed = {st.title: st.completed for st in task.subtasks}
                for idx, st_title in enumerate(new_titles, start=1):
                    # 如果子任务已存在且已完成，保留完成状态；否则默认未完成
                    is_completed = existing_completed.get(str(st_title), False)
                    new_list.append(
                        SubTask(
                            id=f"{task.id}.sub_{idx:02d}",
                            title=str(st_title),
                            completed=is_completed,
                        )
                    )
                task.subtasks = new_list
                changed = True

            if changed:
                task.updated_at = self._now()

            return task

    def complete_task(self, task_id: str) -> bool:
        """将任务标记为已完成

        Returns:
            True 表示成功，False 表示任务不存在
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            now = self._now()
            task.status = TaskStatus.COMPLETED
            task.completed_at = now
            task.updated_at = now
            return True

    def cancel_task(self, task_id: str) -> bool:
        """将任务标记为已取消

        Returns:
            True 表示成功，False 表示任务不存在
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            now = self._now()
            task.status = TaskStatus.CANCELLED
            task.completed_at = None  # 取消任务时清除完成时间，避免语义矛盾
            task.updated_at = now
            return True

    # ----------------------------------------------------------------------
    # 子任务管理
    # ----------------------------------------------------------------------

    def complete_subtask(self, task_id: str, subtask_id: str) -> bool:
        """将子任务标记为完成

        Returns:
            True 表示成功，False 表示任务或子任务不存在
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            for st in task.subtasks:
                if st.id == subtask_id:
                    if not st.completed:
                        st.completed = True
                        task.updated_at = self._now()
                    return True
            return False

    def uncomplete_subtask(self, task_id: str, subtask_id: str) -> bool:
        """将子任务标记为未完成

        Returns:
            True 表示成功，False 表示任务或子任务不存在
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            for st in task.subtasks:
                if st.id == subtask_id:
                    if st.completed:
                        st.completed = False
                        task.updated_at = self._now()
                    return True
            return False

    def toggle_subtask(self, task_id: str, subtask_id: str) -> bool:
        """切换子任务完成状态

        Returns:
            True 表示成功，False 表示任务或子任务不存在
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            for st in task.subtasks:
                if st.id == subtask_id:
                    st.completed = not st.completed
                    task.updated_at = self._now()
                    return True
            return False

    # ----------------------------------------------------------------------
    # 查询
    # ----------------------------------------------------------------------

    def get_task(self, task_id: str) -> Optional[Task]:
        """获取单个任务，不存在返回 None"""
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Task]:
        """列出任务，可按状态过滤，结果按优先级降序排序"""
        with self._lock:
            tasks = list(self._tasks.values())
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return self._sorted_tasks(tasks)

    def list_pending(self) -> List[Task]:
        """列出未完成任务（PENDING 与 IN_PROGRESS），按优先级降序排序"""
        with self._lock:
            tasks = [
                t for t in self._tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
            ]
        return self._sorted_tasks(tasks)

    def list_completed(self) -> List[Task]:
        """列出已完成任务，按完成时间降序排序"""
        with self._lock:
            tasks = [
                t for t in self._tasks.values()
                if t.status == TaskStatus.COMPLETED
            ]
        return sorted(
            tasks,
            key=lambda t: -(t.completed_at or t.updated_at),
        )

    # ----------------------------------------------------------------------
    # 进度统计
    # ----------------------------------------------------------------------

    def get_progress(self) -> TaskProgress:
        """返回任务进度统计"""
        with self._lock:
            tasks = list(self._tasks.values())

        total = len(tasks)
        pending = sum(1 for t in tasks if t.status == TaskStatus.PENDING)
        in_progress = sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS)
        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        cancelled = sum(1 for t in tasks if t.status == TaskStatus.CANCELLED)

        # 完成率：已完成 / (总数 - 已取消)，避免已取消任务拉低完成率
        effective = total - cancelled
        completion_rate = (completed / effective) if effective > 0 else 0.0

        return TaskProgress(
            total=total,
            pending=pending,
            in_progress=in_progress,
            completed=completed,
            cancelled=cancelled,
            completion_rate=completion_rate,
        )

    # ----------------------------------------------------------------------
    # 格式化输出
    # ----------------------------------------------------------------------

    def format_status(self) -> str:
        """生成带进度条的彩色状态文本（使用 ANSI 转义码）

        输出包含：
            - 总体进度条与完成率
            - 各状态计数
            - 按优先级排序的任务清单（含子任务勾选状态）
        """
        with self._lock:
            progress = self.get_progress()
            pending_tasks = self.list_pending()
            completed_tasks = self.list_completed()

        lines: List[str] = []

        # ---- 标题 ----
        lines.append(f"{_ANSI_BOLD}{_ANSI_CYAN}任务进度{_ANSI_RESET}{_ANSI_BOLD}{_ANSI_RESET}")

        # ---- 进度条 ----
        bar_width = 24
        effective = progress.total - progress.cancelled
        filled = int(round(progress.completion_rate * bar_width)) if effective > 0 else 0
        empty = bar_width - filled
        bar = f"{_ANSI_GREEN}{'█' * filled}{_ANSI_RESET}{_ANSI_GRAY}{'░' * empty}{_ANSI_RESET}"
        pct = progress.completion_rate * 100.0
        lines.append(
            f"  {bar}  {_ANSI_BOLD}{pct:5.1f}%{_ANSI_RESET}"
            f"  ({progress.completed}/{effective if effective > 0 else 0})"
        )

        # ---- 状态计数 ----
        count_parts = [
            f"{_ANSI_YELLOW}待办 {progress.pending}{_ANSI_RESET}",
            f"{_ANSI_BLUE}进行中 {progress.in_progress}{_ANSI_RESET}",
            f"{_ANSI_GREEN}已完成 {progress.completed}{_ANSI_RESET}",
            f"{_ANSI_GRAY}已取消 {progress.cancelled}{_ANSI_RESET}",
        ]
        lines.append(f"  {' · '.join(count_parts)}")
        lines.append("")

        # ---- 待办任务清单 ----
        if pending_tasks:
            lines.append(f"{_ANSI_BOLD}待办任务{_ANSI_RESET}")
            for task in pending_tasks:
                lines.append(self._format_task_line(task))
            lines.append("")

        # ---- 已完成任务清单 ----
        if completed_tasks:
            lines.append(f"{_ANSI_BOLD}已完成任务{_ANSI_RESET}")
            for task in completed_tasks:
                lines.append(self._format_task_line(task))
            lines.append("")

        if not pending_tasks and not completed_tasks:
            lines.append(f"{_ANSI_DIM}（暂无任务）{_ANSI_RESET}")

        return "\n".join(lines)

    @staticmethod
    def _format_task_line(task: Task) -> str:
        """格式化单个任务行（含子任务）"""
        color, symbol = _STATUS_STYLE[task.status]
        prio_color = _PRIORITY_STYLE[task.priority]

        # 优先级标签
        prio_label = f"{prio_color}[{task.priority.value.upper()}]{_ANSI_RESET}"

        # 子任务进度
        sub_info = ""
        done, total = task.subtask_progress
        if total > 0:
            sub_color = _ANSI_GREEN if done == total else _ANSI_YELLOW
            sub_info = f"  {sub_color}({done}/{total}){_ANSI_RESET}"

        header = (
            f"  {color}{symbol}{_ANSI_RESET} "
            f"{_ANSI_DIM}{task.id}{_ANSI_RESET}  "
            f"{task.title}  {prio_label}{sub_info}"
        )

        lines = [header]
        # 子任务列表
        if task.subtasks:
            for st in task.subtasks:
                if st.completed:
                    mark = f"{_ANSI_GREEN}[x]{_ANSI_RESET}"
                    title_str = f"{_ANSI_DIM}{st.title}{_ANSI_RESET}"
                else:
                    mark = f"{_ANSI_GRAY}[ ]{_ANSI_RESET}"
                    title_str = st.title
                lines.append(f"      {mark} {_ANSI_DIM}{st.id}{_ANSI_RESET}  {title_str}")

        return "\n".join(lines)

    # ----------------------------------------------------------------------
    # 序列化
    # ----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的字典"""
        with self._lock:
            return {
                "tasks": [t.to_dict() for t in self._tasks.values()],
                "counter": self._counter,
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskManager":
        """从字典反序列化重建 TaskManager"""
        mgr = cls()
        with mgr._lock:
            mgr._counter = int(data.get("counter", 0))
            for t_dict in data.get("tasks", []):
                task = Task.from_dict(t_dict)
                mgr._tasks[task.id] = task
            # 防御：若 counter 与实际任务数不一致，取较大值以保证后续 ID 不冲突
            if mgr._tasks:
                # 从已有 ID 中解析最大序号
                max_seq = 0
                for tid in mgr._tasks.keys():
                    if tid.startswith("task_"):
                        try:
                            seq = int(tid[5:])
                            if seq > max_seq:
                                max_seq = seq
                        except ValueError:
                            pass
                if max_seq > mgr._counter:
                    mgr._counter = max_seq
        return mgr

    # ----------------------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def __contains__(self, task_id: object) -> bool:
        with self._lock:
            return task_id in self._tasks

    def __repr__(self) -> str:
        with self._lock:
            return f"TaskManager(total={len(self._tasks)}, counter={self._counter})"


__all__ = [
    "TaskStatus",
    "TaskPriority",
    "SubTask",
    "Task",
    "TaskProgress",
    "TaskManager",
]
