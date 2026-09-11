"""TaskManager 单元测试

测试 zeroai/core/task_manager.py 的核心功能：
- create_task 创建任务
- update_task 更新任务状态
- complete_task 完成任务
- cancel_task 取消任务（清除 completed_at）
- update_task 更新子任务时保留已有子任务的 completed 状态
- Task.from_dict 无效枚举值回退到默认值
"""
import pytest

from zeroai.core.task_manager import (
    TaskManager,
    Task,
    SubTask,
    TaskStatus,
    TaskPriority,
)


# ============================================================================
# 测试用例
# ============================================================================

def test_create_task():
    """创建任务，验证返回有效 task_id"""
    mgr = TaskManager()
    task = mgr.create_task("实现登录功能", "包括前端和后端")

    assert task.id, "task_id 不应为空"
    assert task.id.startswith("task_"), f"task_id 应以 'task_' 开头，实际 {task.id}"
    assert task.title == "实现登录功能"
    assert task.description == "包括前端和后端"
    assert task.status == TaskStatus.PENDING, "新任务状态应为 PENDING"
    assert task.priority == TaskPriority.NORMAL, "默认优先级应为 NORMAL"
    assert task.completed_at is None, "新任务 completed_at 应为 None"
    assert task.created_at > 0


def test_update_task():
    """更新任务状态，验证状态变更"""
    mgr = TaskManager()
    task = mgr.create_task("测试任务")

    # 更新状态为 IN_PROGRESS
    updated = mgr.update_task(task.id, status=TaskStatus.IN_PROGRESS)
    assert updated is not None, "update_task 应返回更新后的任务"
    assert updated.status == TaskStatus.IN_PROGRESS

    # 更新标题
    updated = mgr.update_task(task.id, title="新标题")
    assert updated.title == "新标题"

    # 更新优先级
    updated = mgr.update_task(task.id, priority=TaskPriority.HIGH)
    assert updated.priority == TaskPriority.HIGH

    # 验证 get_task 返回最新状态
    t = mgr.get_task(task.id)
    assert t.status == TaskStatus.IN_PROGRESS
    assert t.title == "新标题"
    assert t.priority == TaskPriority.HIGH


def test_complete_task():
    """完成任务，验证 status=completed 和 completed_at 不为 None"""
    mgr = TaskManager()
    task = mgr.create_task("待完成任务")

    result = mgr.complete_task(task.id)
    assert result, "complete_task 应返回 True"

    t = mgr.get_task(task.id)
    assert t.status == TaskStatus.COMPLETED, "状态应为 COMPLETED"
    assert t.completed_at is not None, "completed_at 不应为 None"
    assert t.completed_at > 0, "completed_at 应为有效时间戳"


def test_cancel_task():
    """取消任务，验证 status=cancelled 和 completed_at 为 None"""
    mgr = TaskManager()
    task = mgr.create_task("待取消任务")

    # 先标记为完成，再取消
    mgr.complete_task(task.id)
    t = mgr.get_task(task.id)
    assert t.completed_at is not None, "完成后 completed_at 应有值"

    # 取消
    result = mgr.cancel_task(task.id)
    assert result, "cancel_task 应返回 True"

    t = mgr.get_task(task.id)
    assert t.status == TaskStatus.CANCELLED, "状态应为 CANCELLED"
    assert t.completed_at is None, "取消后 completed_at 应为 None"


def test_update_subtasks_preserve():
    """更新任务的 subtasks，已有子任务的 completed 状态应保留"""
    mgr = TaskManager()
    task = mgr.create_task(
        "带子任务的任务",
        subtasks=["子任务A", "子任务B", "子任务C"],
    )

    # 完成第一个子任务
    first_subtask_id = task.subtasks[0].id
    mgr.complete_subtask(task.id, first_subtask_id)

    # 确认子任务已完成
    t = mgr.get_task(task.id)
    assert t.subtasks[0].completed is True, "子任务A 应已完成"

    # 更新子任务列表（保留相同标题，新增一个）
    updated = mgr.update_task(
        task.id,
        subtasks=["子任务A", "子任务B", "子任务C", "子任务D"],
    )
    assert updated is not None

    # 验证子任务A 的 completed 状态被保留
    t = mgr.get_task(task.id)
    assert len(t.subtasks) == 4, f"应有 4 个子任务，实际 {len(t.subtasks)}"
    assert t.subtasks[0].title == "子任务A"
    assert t.subtasks[0].completed is True, "子任务A 的 completed 状态应被保留"
    # 新增的子任务D 应为未完成
    assert t.subtasks[3].title == "子任务D"
    assert t.subtasks[3].completed is False, "新增子任务应为未完成"


def test_from_dict_invalid_enum():
    """Task.from_dict 传入无效枚举值，验证回退到默认值而非抛异常"""
    data = {
        "id": "task_001",
        "title": "测试任务",
        "description": "描述",
        "status": "invalid_status_value",  # 无效状态
        "priority": "invalid_priority_value",  # 无效优先级
        "subtasks": [],
        "created_at": 1234567890.0,
        "updated_at": 1234567890.0,
        "completed_at": None,
    }

    # 不应抛出异常
    task = Task.from_dict(data)
    assert task.status == TaskStatus.PENDING, "无效状态应回退到 PENDING"
    assert task.priority == TaskPriority.NORMAL, "无效优先级应回退到 NORMAL"
    assert task.id == "task_001"
    assert task.title == "测试任务"
