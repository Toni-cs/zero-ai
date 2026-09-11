"""Workspace 检查点与回滚系统

参考 OpenCode 的 checkpoint 特性，提供基于 git 的 workspace 快照能力：
- 创建检查点（git commit 或文件复制）
- 回滚到检查点
- 列出和比较检查点

使用方式：
    mgr = CheckpointManager("/path/to/workspace")
    result = mgr.create_checkpoint("修改前")
    # ... 做一些修改 ...
    mgr.rollback(result.checkpoint_id)  # 回滚
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================================
# 常量
# ============================================================================

# 检查点元数据存储目录（位于 workspace_root 下）
_CHECKPOINT_DIR_NAME = ".zeroai"
_CHECKPOINT_SUBDIR = "checkpoints"
_INDEX_FILE_NAME = "index.json"
_SNAPSHOTS_DIR_NAME = "snapshots"

# 文件复制回退模式下，跳过这些目录以避免递归复制和噪声
_SKIP_DIRS = {
    ".git",
    ".zeroai",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
}

# 单文件大小上限（16MB），超过则跳过复制
_MAX_FILE_SIZE = 16 * 1024 * 1024


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class CheckpointInfo:
    """检查点元信息"""
    id: str
    label: str
    timestamp: float
    files_changed: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CheckpointInfo":
        # 容忍字段缺失/多余
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class CheckpointResult:
    """创建检查点的结果"""
    success: bool
    checkpoint_id: str
    message: str
    files_changed: List[str] = field(default_factory=list)


@dataclass
class RollbackResult:
    """回滚结果"""
    success: bool
    message: str
    stashed_changes: bool = False


# ============================================================================
# Git 命令封装
# ============================================================================

class _GitError(Exception):
    """git 命令执行失败"""


def _run_git(
    args: List[str],
    cwd: str,
    *,
    check: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """执行 git 命令并返回结果。

    Args:
        args: git 子命令参数列表（不含 "git" 本身）
        cwd: 工作目录
        check: 为 True 时非零退出码抛 _GitError
        timeout: 超时秒数

    Returns:
        subprocess.CompletedProcess

    Raises:
        _GitError: 当 check=True 且 git 退出码非 0，或命令不存在时
    """
    cmd = ["git"] + args
    try:
        # Windows 下需要关闭 shell，使用列表形式
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as e:
        raise _GitError(f"git 命令未找到：{e}") from e
    except subprocess.TimeoutExpired as e:
        raise _GitError(f"git 命令超时（{timeout}s）：{' '.join(args)}") from e
    except Exception as e:
        raise _GitError(f"git 命令执行异常：{e}") from e

    if check and result.returncode != 0:
        err = (result.stderr or "").strip()
        raise _GitError(f"git {' '.join(args)} 失败（exit={result.returncode}）：{err}")

    return result


def _is_git_repo(workspace_root: str) -> bool:
    """判断目录是否为 git 仓库"""
    try:
        r = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=workspace_root)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except _GitError:
        return False


def _git_has_changes(workspace_root: str) -> bool:
    """工作区是否有未提交变更（含 untracked）"""
    try:
        r = _run_git(
            ["status", "--porcelain"],
            cwd=workspace_root,
            check=True,
        )
        return bool(r.stdout.strip())
    except _GitError:
        return False


def _git_changed_files(workspace_root: str) -> List[str]:
    """获取工作区变更文件列表（含 untracked）"""
    try:
        r = _run_git(
            ["status", "--porcelain"],
            cwd=workspace_root,
            check=True,
        )
        files: List[str] = []
        for line in r.stdout.splitlines():
            if not line:
                continue
            # porcelain 格式：XY filename，X/Y 为状态字母
            # 文件名从第 3 列开始（前 2 列为状态）
            name = line[3:]
            # 处理重命名：R100 old -> new
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            # 去除引号（带空格/特殊字符的路径会被引号包裹）
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            files.append(name)
        return files
    except _GitError:
        return []


def _git_head_commit(workspace_root: str) -> Optional[str]:
    """获取当前 HEAD commit hash，失败返回 None"""
    try:
        r = _run_git(["rev-parse", "HEAD"], cwd=workspace_root)
        if r.returncode == 0:
            return r.stdout.strip() or None
    except _GitError:
        pass
    return None


def _git_stash_create(workspace_root: str) -> Optional[str]:
    """创建匿名 stash（不入栈），返回 commit hash；无变更或失败返回 None。

    通过先 ``git add -A`` 暂存所有变更（含 untracked 文件），再 ``git stash create``，
    最后 ``git reset`` 恢复暂存区，确保 untracked 文件被包含在 stash 中。

    否则 ``git stash create`` 默认不捕获 untracked 文件，当工作区仅有 untracked
    变更时会返回空字符串，导致调用方 fallback 到 HEAD commit 而丢失 untracked 文件。
    """
    try:
        # 先暂存所有变更（包括 untracked），确保 stash create 能捕获
        _run_git(["add", "-A"], cwd=workspace_root, check=True)
        # 创建匿名 stash
        r = _run_git(["stash", "create"], cwd=workspace_root)
        # 恢复暂存区到 HEAD（mixed reset，不影响工作区文件内容）
        # 使被 add 的 untracked 文件变回 untracked 状态，保持工作区原样
        _run_git(["reset"], cwd=workspace_root)
        if r.returncode == 0:
            h = r.stdout.strip()
            return h or None
    except _GitError:
        # 出错时也尝试恢复暂存区，避免残留暂存状态污染工作区
        try:
            _run_git(["reset"], cwd=workspace_root)
        except _GitError:
            pass
    return None


def _git_stash_push(workspace_root: str, message: str = "zeroai-checkpoint-rollback") -> bool:
    """将当前变更入栈 stash，返回是否真的 stash 了变更"""
    try:
        r = _run_git(
            ["stash", "push", "-u", "-m", message],
            cwd=workspace_root,
        )
        # git stash push 在无变更时退出码 0，但输出 "No local changes to save"
        out = (r.stdout or "") + (r.stderr or "")
        if "No local changes to save" in out:
            return False
        return r.returncode == 0
    except _GitError:
        return False


def _git_checkout(workspace_root: str, ref: str) -> bool:
    """checkout 到指定 ref（commit/branch/tag）"""
    try:
        r = _run_git(["checkout", ref], cwd=workspace_root)
        return r.returncode == 0
    except _GitError:
        return False


def _git_current_branch(workspace_root: str) -> Optional[str]:
    """获取当前分支名。

    处于 detached HEAD 状态时返回 None。
    """
    try:
        r = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_root)
        if r.returncode == 0:
            name = r.stdout.strip()
            # detached HEAD 时 abbrev-ref 返回 "HEAD"
            if name and name != "HEAD":
                return name
    except _GitError:
        pass
    return None


def _git_stash_apply(workspace_root: str, ref: str) -> bool:
    """应用指定 stash（不入栈的匿名 stash commit hash 或 stash@{n}）。

    使用 ``git stash apply`` 而非 ``pop``，避免应用成功后删除 stash，
    便于失败时重试。不会进入 detached HEAD 状态。
    """
    try:
        r = _run_git(["stash", "apply", ref], cwd=workspace_root)
        return r.returncode == 0
    except _GitError:
        return False


def _git_reset_hard(workspace_root: str, ref: str) -> bool:
    """硬重置到指定 ref（commit/branch/tag）。

    ``git reset --hard`` 会移动当前分支指针到目标 ref 并重置工作区，
    HEAD 仍指向当前分支，不会进入 detached HEAD 状态。
    """
    try:
        r = _run_git(["reset", "--hard", ref], cwd=workspace_root)
        return r.returncode == 0
    except _GitError:
        return False


def _git_diff(workspace_root: str, ref_a: str, ref_b: str) -> str:
    """返回两个 ref 之间的 diff 文本"""
    try:
        r = _run_git(["diff", ref_a, ref_b], cwd=workspace_root)
        if r.returncode == 0:
            return r.stdout
    except _GitError:
        pass
    return ""


def _git_files_between(workspace_root: str, ref_a: str, ref_b: str) -> List[str]:
    """返回两个 ref 之间变更的文件列表"""
    try:
        r = _run_git(
            ["diff", "--name-only", ref_a, ref_b],
            cwd=workspace_root,
        )
        if r.returncode == 0:
            return [ln for ln in r.stdout.splitlines() if ln.strip()]
    except _GitError:
        pass
    return []


# ============================================================================
# 文件复制回退模式
# ============================================================================

def _copy_workspace_snapshot(src_root: str, dest_dir: str) -> List[str]:
    """将工作区文件复制到快照目录，返回已复制的相对路径列表。

    跳过 .git / .zeroai / __pycache__ 等目录和大文件。
    """
    copied: List[str] = []
    src_root_path = Path(src_root)
    os.makedirs(dest_dir, exist_ok=True)

    for root, dirs, files in os.walk(src_root):
        # 原地修改 dirs 以跳过特定目录（影响 walk 后续行为）
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            src_file = Path(root) / fname
            try:
                rel = src_file.relative_to(src_root_path)
            except ValueError:
                continue
            # 跳过过大文件
            try:
                size = src_file.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_SIZE:
                continue
            dest_file = Path(dest_dir) / rel
            try:
                os.makedirs(str(dest_file.parent), exist_ok=True)
                shutil.copy2(str(src_file), str(dest_file))
                copied.append(str(rel).replace("\\", "/"))
            except OSError:
                continue
    return copied


def _restore_workspace_snapshot(snapshot_dir: str, dest_root: str) -> bool:
    """从快照目录恢复文件到工作区（覆盖），并删除快照后新增的文件。

    真正的回滚应恢复到检查点时的完整文件集合：
    1. 删除工作区中存在但快照中不存在的文件（检查点后新增的文件）
    2. 复制快照文件覆盖工作区文件
    """
    try:
        snapshot_dir_path = Path(snapshot_dir)
        dest_root_path = Path(dest_root)

        # 1. 收集快照中的文件集合（相对路径 posix 风格）
        snapshot_files: set = set()
        for root, _dirs, files in os.walk(snapshot_dir):
            for fname in files:
                src_file = Path(root) / fname
                try:
                    rel = src_file.relative_to(snapshot_dir_path)
                except ValueError:
                    continue
                snapshot_files.add(str(rel).replace("\\", "/"))

        # 2. 删除工作区中存在但快照中不存在的文件（检查点后新增的文件）
        #    跳过 .git / .zeroai / __pycache__ 等目录，避免误删版本控制等关键文件
        for root, dirs, files in os.walk(dest_root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                dest_file = Path(root) / fname
                try:
                    rel = dest_file.relative_to(dest_root_path)
                except ValueError:
                    continue
                rel_posix = str(rel).replace("\\", "/")
                if rel_posix not in snapshot_files:
                    try:
                        os.remove(str(dest_file))
                    except OSError:
                        pass

        # 3. 复制快照文件覆盖工作区文件
        for root, _dirs, files in os.walk(snapshot_dir):
            for fname in files:
                src_file = Path(root) / fname
                rel = src_file.relative_to(snapshot_dir_path)
                dest_file = Path(dest_root) / rel
                os.makedirs(str(dest_file.parent), exist_ok=True)
                shutil.copy2(str(src_file), str(dest_file))
        return True
    except OSError:
        return False


# ============================================================================
# CheckpointManager
# ============================================================================

class CheckpointManager:
    """基于 git 的 workspace 检查点/回滚管理器。

    优先使用 git 创建检查点（git stash create 或 HEAD commit）；
    若 workspace 不是 git 仓库，回退到文件复制方式。

    检查点元数据存储在 {workspace_root}/.zeroai/checkpoints/index.json。
    文件复制快照存储在 {workspace_root}/.zeroai/checkpoints/snapshots/{id}/。
    """

    def __init__(self, workspace_root: str):
        """初始化检查点管理器。

        Args:
            workspace_root: workspace 根目录的绝对路径
        """
        self.workspace_root: str = os.path.abspath(workspace_root)
        self._is_git: bool = _is_git_repo(self.workspace_root)

        # 检查点元数据目录
        self._checkpoint_dir: str = os.path.join(
            self.workspace_root, _CHECKPOINT_DIR_NAME, _CHECKPOINT_SUBDIR
        )
        self._index_file: str = os.path.join(self._checkpoint_dir, _INDEX_FILE_NAME)
        self._snapshots_dir: str = os.path.join(
            self._checkpoint_dir, _SNAPSHOTS_DIR_NAME
        )

        # 确保目录存在
        os.makedirs(self._checkpoint_dir, exist_ok=True)
        os.makedirs(self._snapshots_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 索引文件读写
    # ------------------------------------------------------------------

    def _load_index(self) -> List[CheckpointInfo]:
        """加载检查点索引，返回按时间倒序排列的列表"""
        if not os.path.isfile(self._index_file):
            return []
        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        items = data.get("checkpoints", [])
        result: List[CheckpointInfo] = []
        for it in items:
            if isinstance(it, dict):
                try:
                    result.append(CheckpointInfo.from_dict(it))
                except Exception:
                    continue
        # 按时间戳倒序
        result.sort(key=lambda c: c.timestamp, reverse=True)
        return result

    def _save_index(self, checkpoints: List[CheckpointInfo]) -> bool:
        """保存检查点索引（按时间戳倒序）"""
        # 排序后保存
        sorted_list = sorted(checkpoints, key=lambda c: c.timestamp, reverse=True)
        data = {
            "version": 1,
            "workspace_root": self.workspace_root,
            "updated_at": time.time(),
            "checkpoints": [c.to_dict() for c in sorted_list],
        }
        try:
            with open(self._index_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except OSError:
            return False

    def _append_checkpoint(self, info: CheckpointInfo) -> bool:
        """追加一个检查点到索引"""
        existing = self._load_index()
        # 去重：相同 id 替换
        existing = [c for c in existing if c.id != info.id]
        existing.append(info)
        return self._save_index(existing)

    def _find_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointInfo]:
        """按 id 查找检查点"""
        for c in self._load_index():
            if c.id == checkpoint_id:
                return c
        return None

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def create_checkpoint(self, label: str = "") -> CheckpointResult:
        """创建一个检查点。

        - git 仓库：优先用 `git stash create` 捕获未提交变更得到 commit hash；
          若无变更则用当前 HEAD commit 作为检查点。
        - 非 git 仓库：复制工作区文件到 snapshots/{id}/。

        Args:
            label: 可选标签，支持中文

        Returns:
            CheckpointResult
        """
        checkpoint_id = self._gen_id()
        timestamp = time.time()
        files_changed: List[str] = []
        summary_parts: List[str] = []

        if self._is_git:
            # ---- git 模式 ----
            try:
                has_changes = _git_has_changes(self.workspace_root)
                files_changed = _git_changed_files(self.workspace_root)

                if has_changes:
                    stash_hash = _git_stash_create(self.workspace_root)
                    if stash_hash:
                        # 用 stash commit 作为可 checkout 的 ref
                        ref = stash_hash
                        summary_parts.append("git-stash")
                    else:
                        # stash create 失败，退回 HEAD
                        head = _git_head_commit(self.workspace_root)
                        ref = head or checkpoint_id
                        summary_parts.append("git-head(stash-failed)")
                else:
                    head = _git_head_commit(self.workspace_root)
                    ref = head or checkpoint_id
                    summary_parts.append("git-head(no-changes)")

                # 把 ref 存到 summary 里，便于 rollback 时取出
                # 格式：git-ref:<hash>
                summary_parts.insert(0, f"git-ref:{ref}")
                summary = " | ".join(summary_parts)

                info = CheckpointInfo(
                    id=checkpoint_id,
                    label=label,
                    timestamp=timestamp,
                    files_changed=files_changed,
                    summary=summary,
                )
                ok = self._append_checkpoint(info)
                if not ok:
                    return CheckpointResult(
                        success=False,
                        checkpoint_id="",
                        message="写入检查点索引失败",
                        files_changed=files_changed,
                    )
                msg = f"已创建 git 检查点（{('有变更' if has_changes else '无变更')}，"
                msg += f"变更文件 {len(files_changed)} 个）"
                return CheckpointResult(
                    success=True,
                    checkpoint_id=checkpoint_id,
                    message=msg,
                    files_changed=files_changed,
                )
            except Exception as e:
                return CheckpointResult(
                    success=False,
                    checkpoint_id="",
                    message=f"git 检查点创建异常：{e}",
                    files_changed=files_changed,
                )
        else:
            # ---- 文件复制回退模式 ----
            try:
                snapshot_dir = os.path.join(self._snapshots_dir, checkpoint_id)
                copied = _copy_workspace_snapshot(self.workspace_root, snapshot_dir)
                files_changed = copied
                summary = f"file-copy:{len(copied)}-files"

                info = CheckpointInfo(
                    id=checkpoint_id,
                    label=label,
                    timestamp=timestamp,
                    files_changed=files_changed,
                    summary=summary,
                )
                ok = self._append_checkpoint(info)
                if not ok:
                    return CheckpointResult(
                        success=False,
                        checkpoint_id="",
                        message="写入检查点索引失败",
                        files_changed=files_changed,
                    )
                return CheckpointResult(
                    success=True,
                    checkpoint_id=checkpoint_id,
                    message=f"已创建文件复制检查点（复制 {len(copied)} 个文件）",
                    files_changed=files_changed,
                )
            except Exception as e:
                return CheckpointResult(
                    success=False,
                    checkpoint_id="",
                    message=f"文件复制检查点创建异常：{e}",
                    files_changed=files_changed,
                )

    def rollback(self, checkpoint_id: str) -> RollbackResult:
        """回滚到指定检查点。

        - git 仓库：先把当前未提交变更 stash 入栈（保留），再根据检查点 ref 类型
          恢复工作区——stash 类型用 ``git stash apply``，HEAD commit 类型用
          ``git reset --hard``，两者均不会进入 detached HEAD。
        - 非 git 仓库：从 snapshots/{id}/ 恢复文件覆盖工作区，并删除检查点后新增的文件。

        Args:
            checkpoint_id: 检查点 id

        Returns:
            RollbackResult
        """
        info = self._find_checkpoint(checkpoint_id)
        if info is None:
            return RollbackResult(
                success=False,
                message=f"检查点不存在：{checkpoint_id}",
                stashed_changes=False,
            )

        if self._is_git:
            # ---- git 模式 ----
            ref = self._extract_git_ref(info.summary)
            if not ref:
                return RollbackResult(
                    success=False,
                    message="检查点缺少 git ref 信息，无法回滚",
                    stashed_changes=False,
                )

            # 获取当前分支名，用于兜底防止 detached HEAD
            current_branch = _git_current_branch(self.workspace_root)

            # 先保存当前未提交变更到 stash（含 untracked），保留用户当前工作
            stashed = False
            try:
                if _git_has_changes(self.workspace_root):
                    stashed = _git_stash_push(
                        self.workspace_root,
                        message=f"zeroai-rollback-before:{checkpoint_id}",
                    )
            except Exception:
                stashed = False

            # 根据检查点 ref 类型选择回滚方式，避免进入 detached HEAD：
            # - stash 类型（git stash create 产生的 dangling commit）：
            #   用 git stash apply 恢复工作区内容，不移动 HEAD，不进入 detached
            # - HEAD commit 类型（无变更或 stash create 失败时退回 HEAD）：
            #   用 git reset --hard 回到该 commit，HEAD 仍指向当前分支
            is_stash_ref = self._is_stash_ref(info.summary)
            if is_stash_ref:
                try:
                    ok = _git_stash_apply(self.workspace_root, ref)
                except Exception as e:
                    return RollbackResult(
                        success=False,
                        message=f"git stash apply 异常：{e}",
                        stashed_changes=stashed,
                    )
                failed_cmd = f"git stash apply {ref}"
            else:
                try:
                    ok = _git_reset_hard(self.workspace_root, ref)
                except Exception as e:
                    return RollbackResult(
                        success=False,
                        message=f"git reset --hard 异常：{e}",
                        stashed_changes=stashed,
                    )
                failed_cmd = f"git reset --hard {ref}"

            # 兜底：若仍意外进入 detached HEAD 且已知原分支，尝试切回分支
            if ok and current_branch:
                try:
                    after_branch = _git_current_branch(self.workspace_root)
                    if after_branch is None:
                        # 已进入 detached HEAD，切回原分支以恢复可提交状态
                        _git_checkout(self.workspace_root, current_branch)
                except Exception:
                    pass

            if ok:
                msg = f"已回滚到检查点 {checkpoint_id}"
                if info.label:
                    msg += f"（{info.label}）"
                if stashed:
                    msg += "；当前未提交变更已 stash 保留"
                return RollbackResult(
                    success=True,
                    message=msg,
                    stashed_changes=stashed,
                )
            else:
                return RollbackResult(
                    success=False,
                    message=f"{failed_cmd} 失败",
                    stashed_changes=stashed,
                )
        else:
            # ---- 文件复制回退模式 ----
            snapshot_dir = os.path.join(self._snapshots_dir, checkpoint_id)
            if not os.path.isdir(snapshot_dir):
                return RollbackResult(
                    success=False,
                    message=f"检查点快照目录不存在：{snapshot_dir}",
                    stashed_changes=False,
                )
            try:
                ok = _restore_workspace_snapshot(snapshot_dir, self.workspace_root)
            except Exception as e:
                return RollbackResult(
                    success=False,
                    message=f"文件恢复异常：{e}",
                    stashed_changes=False,
                )
            if ok:
                msg = f"已回滚到检查点 {checkpoint_id}"
                if info.label:
                    msg += f"（{info.label}）"
                return RollbackResult(
                    success=True,
                    message=msg,
                    stashed_changes=False,
                )
            else:
                return RollbackResult(
                    success=False,
                    message="文件恢复失败",
                    stashed_changes=False,
                )

    def list_checkpoints(self) -> List[CheckpointInfo]:
        """列出所有检查点，按时间倒序排列"""
        return self._load_index()

    def diff_checkpoints(self, checkpoint_id_a: str, checkpoint_id_b: str) -> str:
        """比较两个检查点之间的差异。

        - git 仓库：返回 `git diff a b` 的文本。
        - 非 git 仓库：返回变更文件名列表的简单文本摘要。

        Args:
            checkpoint_id_a: 检查点 A 的 id
            checkpoint_id_b: 检查点 B 的 id

        Returns:
            差异文本（多行字符串）。若检查点不存在或无法比较，返回说明字符串。
        """
        info_a = self._find_checkpoint(checkpoint_id_a)
        info_b = self._find_checkpoint(checkpoint_id_b)
        if info_a is None:
            return f"检查点不存在：{checkpoint_id_a}"
        if info_b is None:
            return f"检查点不存在：{checkpoint_id_b}"

        if self._is_git:
            ref_a = self._extract_git_ref(info_a.summary)
            ref_b = self._extract_git_ref(info_b.summary)
            if not ref_a or not ref_b:
                return "检查点缺少 git ref 信息，无法 diff"
            diff_text = _git_diff(self.workspace_root, ref_a, ref_b)
            if not diff_text:
                # 两个 ref 相同或无差异
                if ref_a == ref_b:
                    return f"两个检查点指向同一 git ref（{ref_a}），无差异。"
                return "git diff 无输出（可能无差异或比较失败）。"
            return diff_text
        else:
            # 文件复制模式：比较两个快照的文件集合
            snap_a = os.path.join(self._snapshots_dir, checkpoint_id_a)
            snap_b = os.path.join(self._snapshots_dir, checkpoint_id_b)
            if not os.path.isdir(snap_a):
                return f"快照目录不存在：{snap_a}"
            if not os.path.isdir(snap_b):
                return f"快照目录不存在：{snap_b}"

            files_a = self._list_snapshot_files(snap_a)
            files_b = self._list_snapshot_files(snap_b)
            set_a = set(files_a)
            set_b = set(files_b)

            only_in_a = sorted(set_a - set_b)
            only_in_b = sorted(set_b - set_a)
            common = sorted(set_a & set_b)

            # 内容不同的公共文件
            differ: List[str] = []
            for rel in common:
                pa = os.path.join(snap_a, rel)
                pb = os.path.join(snap_b, rel)
                if not _files_equal(pa, pb):
                    differ.append(rel)

            lines: List[str] = []
            lines.append(f"diff: {checkpoint_id_a} <-> {checkpoint_id_b}")
            lines.append(f"  仅在 A：{len(only_in_a)} 个文件")
            for f in only_in_a[:50]:
                lines.append(f"    + {f}")
            if len(only_in_a) > 50:
                lines.append(f"    ... 还有 {len(only_in_a) - 50} 个")
            lines.append(f"  仅在 B：{len(only_in_b)} 个文件")
            for f in only_in_b[:50]:
                lines.append(f"    - {f}")
            if len(only_in_b) > 50:
                lines.append(f"    ... 还有 {len(only_in_b) - 50} 个")
            lines.append(f"  内容不同：{len(differ)} 个文件")
            for f in differ[:50]:
                lines.append(f"    ~ {f}")
            if len(differ) > 50:
                lines.append(f"    ... 还有 {len(differ) - 50} 个")
            return "\n".join(lines)

    def get_current_checkpoint(self) -> Optional[CheckpointInfo]:
        """获取最近的检查点（时间倒序第一个）"""
        lst = self._load_index()
        return lst[0] if lst else None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _gen_id() -> str:
        """生成检查点 id：时间戳前缀 + 短 uuid"""
        ts = int(time.time())
        short = uuid.uuid4().hex[:8]
        return f"cp-{ts}-{short}"

    @staticmethod
    def _extract_git_ref(summary: str) -> Optional[str]:
        """从 summary 字段中提取 git ref（格式：git-ref:<hash> | ...）"""
        if not summary:
            return None
        for part in summary.split("|"):
            part = part.strip()
            if part.startswith("git-ref:"):
                ref = part[len("git-ref:"):].strip()
                if ref:
                    return ref
        return None

    @staticmethod
    def _is_stash_ref(summary: str) -> bool:
        """判断检查点的 git ref 是否为 stash 类型（由 git stash create 产生）。

        summary 中标记为 ``git-stash`` 的为 stash 类型（dangling commit），
        标记为 ``git-head(...)`` 的为 HEAD commit 类型。
        """
        if not summary:
            return False
        # 精确匹配 "git-stash" 标记，避免误匹配 "git-stash-failed" 等
        for part in summary.split("|"):
            part = part.strip()
            if part == "git-stash":
                return True
        return False

    @staticmethod
    def _list_snapshot_files(snapshot_dir: str) -> List[str]:
        """列出快照目录下所有文件的相对路径（posix 风格）"""
        result: List[str] = []
        for root, _dirs, files in os.walk(snapshot_dir):
            for fname in files:
                full = Path(root) / fname
                try:
                    rel = full.relative_to(snapshot_dir)
                except ValueError:
                    continue
                result.append(str(rel).replace("\\", "/"))
        return result


# ============================================================================
# 模块级便捷函数
# ============================================================================

def _files_equal(path_a: str, path_b: str) -> bool:
    """快速比较两个文件是否相同（先比大小，再比内容）"""
    try:
        sa = os.path.getsize(path_a)
        sb = os.path.getsize(path_b)
        if sa != sb:
            return False
        # 逐块比较
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            chunk = 64 * 1024
            while True:
                ba = fa.read(chunk)
                bb = fb.read(chunk)
                if ba != bb:
                    return False
                if not ba:
                    return True
    except OSError:
        return False


def create_checkpoint_manager(workspace_root: str) -> CheckpointManager:
    """创建 CheckpointManager 实例的工厂函数"""
    return CheckpointManager(workspace_root)


__all__ = [
    "CheckpointInfo",
    "CheckpointResult",
    "RollbackResult",
    "CheckpointManager",
    "create_checkpoint_manager",
]
