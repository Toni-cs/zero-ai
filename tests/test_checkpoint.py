"""CheckpointManager 单元测试

测试 zeroai/core/checkpoint.py 的核心功能：
- 创建检查点
- 回滚到检查点
- 列出检查点
- untracked 文件捕获与恢复
- 回滚后不进入 detached HEAD 状态
"""
import os
import subprocess
import tempfile
import time

import pytest

from zeroai.core.checkpoint import CheckpointManager


# ============================================================================
# 辅助函数
# ============================================================================

def _run_git(args, cwd):
    """在指定目录执行 git 命令，返回 CompletedProcess"""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _init_git_repo(path):
    """在 path 初始化一个 git 仓库，配置 user 并创建初始 commit"""
    _run_git(["init"], path).check_returncode()
    _run_git(["config", "user.email", "test@example.com"], path).check_returncode()
    _run_git(["config", "user.name", "Test User"], path).check_returncode()
    # 创建初始文件并提交，确保 HEAD 存在
    init_file = os.path.join(path, "README.md")
    with open(init_file, "w", encoding="utf-8") as f:
        f.write("initial content\n")
    _run_git(["add", "-A"], path).check_returncode()
    _run_git(["commit", "-m", "initial commit"], path).check_returncode()


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def git_workspace():
    """创建一个临时 git 仓库工作目录，测试结束后自动清理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        _init_git_repo(tmpdir)
        yield tmpdir


@pytest.fixture
def manager(git_workspace):
    """基于临时 git 仓库创建 CheckpointManager 实例"""
    return CheckpointManager(git_workspace)


# ============================================================================
# 测试用例
# ============================================================================

def test_create_checkpoint(manager):
    """创建检查点，验证返回有效的 checkpoint_id"""
    result = manager.create_checkpoint("测试检查点")
    assert result.success, f"创建失败：{result.message}"
    assert result.checkpoint_id, "checkpoint_id 不应为空"
    assert result.checkpoint_id.startswith("cp-"), (
        f"checkpoint_id 应以 'cp-' 开头，实际：{result.checkpoint_id}"
    )


def test_rollback(manager, git_workspace):
    """创建检查点后修改文件，然后回滚，验证文件内容恢复"""
    # 在初始状态创建检查点
    cp = manager.create_checkpoint("修改前")
    assert cp.success, f"创建检查点失败：{cp.message}"

    # 修改已跟踪文件
    file_path = os.path.join(git_workspace, "README.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("modified content\n")

    # 确认修改已生效
    with open(file_path, "r", encoding="utf-8") as f:
        assert f.read() == "modified content\n"

    # 回滚
    result = manager.rollback(cp.checkpoint_id)
    assert result.success, f"回滚失败：{result.message}"

    # 验证文件内容已恢复
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert content == "initial content\n", f"回滚后内容不正确：{content!r}"


def test_list_checkpoints(manager):
    """创建多个检查点，验证列表返回正确数量和顺序（按时间倒序）"""
    cp1 = manager.create_checkpoint("第一个")
    assert cp1.success
    time.sleep(0.05)  # 确保时间戳不同

    cp2 = manager.create_checkpoint("第二个")
    assert cp2.success
    time.sleep(0.05)

    cp3 = manager.create_checkpoint("第三个")
    assert cp3.success

    checkpoints = manager.list_checkpoints()
    assert len(checkpoints) == 3, f"应有 3 个检查点，实际 {len(checkpoints)}"

    # 按时间戳倒序排列（最新的在前）
    assert checkpoints[0].id == cp3.checkpoint_id, "第一个应为最新创建的"
    assert checkpoints[1].id == cp2.checkpoint_id, "第二个应为中间创建的"
    assert checkpoints[2].id == cp1.checkpoint_id, "第三个应为最早创建的"


def test_untracked_files(manager, git_workspace):
    """创建检查点时存在 untracked 文件，验证回滚后 untracked 文件被正确恢复"""
    # 创建一个 untracked 文件
    untracked_path = os.path.join(git_workspace, "untracked.txt")
    untracked_content = "untracked file content\n"
    with open(untracked_path, "w", encoding="utf-8") as f:
        f.write(untracked_content)

    # 创建检查点（此时 untracked 文件应被 stash create 捕获）
    cp = manager.create_checkpoint("含 untracked 文件")
    assert cp.success, f"创建检查点失败：{cp.message}"
    # 确认检查点检测到了变更
    assert "untracked.txt" in cp.files_changed, (
        f"检查点应捕获 untracked 文件，files_changed={cp.files_changed}"
    )

    # 删除 untracked 文件
    os.remove(untracked_path)
    assert not os.path.exists(untracked_path)

    # 回滚
    result = manager.rollback(cp.checkpoint_id)
    assert result.success, f"回滚失败：{result.message}"

    # 验证 untracked 文件已被恢复
    assert os.path.exists(untracked_path), "回滚后 untracked 文件应存在"
    with open(untracked_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert content == untracked_content, (
        f"回滚后 untracked 文件内容不正确：{content!r}"
    )


def test_no_detached_head(manager, git_workspace):
    """回滚后验证 git 不处于 detached HEAD 状态"""
    # 创建检查点
    cp = manager.create_checkpoint("回滚前")
    assert cp.success

    # 修改文件以产生变更
    file_path = os.path.join(git_workspace, "README.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("changed\n")

    # 回滚
    result = manager.rollback(cp.checkpoint_id)
    assert result.success, f"回滚失败：{result.message}"

    # 用 git symbolic-ref HEAD 检查是否处于 detached HEAD
    # 非 detached 状态时退出码为 0，detached 时退出码非 0
    r = _run_git(["symbolic-ref", "HEAD"], git_workspace)
    assert r.returncode == 0, (
        f"回滚后 HEAD 处于 detached 状态：stdout={r.stdout!r}, stderr={r.stderr!r}"
    )
    assert "refs/heads/" in r.stdout, (
        f"symbolic-ref 输出应包含 refs/heads/，实际：{r.stdout!r}"
    )
