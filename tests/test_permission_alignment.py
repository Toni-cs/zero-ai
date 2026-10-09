# -*- coding: utf-8 -*-
"""权限对齐测试：ssh_exec / code_execute / CodeSandbox 受 PERMISSION_LEVEL 管辖

背景（2026-10-09）：
  改动前存在两处不一致——
  1. 本地 run_command 在 full 模式下对危险命令"记 warning 后放行"，
     而 ssh_exec **无条件拦截**并要求 confirm_dangerous=true，
     同一条 shutdown 本地能跑、远程被拦；拦截返回值还会被 _ssh_cmd_ok
     判为失败，进而中断 ssh_deploy 的运维流水线。
  2. code_execute 硬编码 allow_network=False + AST 拦截，
     与"全权限模式"语义相反。

  现统一到同一个 PERMISSION_LEVEL 开关：
    full（全权限，当前默认）→ 全部放行，危险命令写审计日志
    restricted（受限）      → 保留原有拦截
  隔离手段（子进程 / 超时 / 工作目录限制）不属于权限闸门，始终保留。
"""
from __future__ import annotations

import pytest

import zeroai.core.constants as constants_mod
import zeroai.tools.command_exec as command_exec_mod
import zeroai.tools.ssh_ops as ssh_ops


# ─────────────────────────── CodeSandbox ───────────────────────────

class TestCodeSandboxSwitch:
    """CodeSandbox 新增 check_safety 开关，默认行为必须保持不变。"""

    def test_default_still_checks_safety(self):
        """默认 check_safety=True：未改动的调用方继续被拦截。"""
        from zeroai.core.sandbox import CodeSandbox

        sb = CodeSandbox()
        assert sb.check_safety is True
        r = sb.execute("import os\nos.system('echo hi')")
        assert r["returncode"] == -2
        assert r["issues"], "默认模式必须给出安全问题清单"

    def test_check_safety_false_skips_ast(self):
        from zeroai.core.sandbox import CodeSandbox

        sb = CodeSandbox(check_safety=False, allow_network=True)
        r = sb.execute(
            "import subprocess\n"
            "print(subprocess.run(['echo', 'ok'],"
            " capture_output=True, text=True).stdout.strip())"
        )
        assert r["success"] is True
        assert r["issues"] == []

    def test_allow_network_controls_socket_patch(self):
        """allow_network 决定是否注入 socket 屏蔽（不是靠关键字匹配，看真实产物）。"""
        from zeroai.core.sandbox import CodeSandbox

        assert "_blocked_socket" not in CodeSandbox(allow_network=True)._wrap_code("pass", "/")
        assert "_blocked_socket" in CodeSandbox(allow_network=False)._wrap_code("pass", "/")

    def test_isolation_survives_when_safety_off(self):
        """放开权限≠拆掉隔离：超时/工作目录限制必须保留。"""
        from zeroai.core.sandbox import CodeSandbox

        sb = CodeSandbox(check_safety=False, allow_network=True, timeout=5)
        assert sb.timeout == 5
        assert "os.chdir" in sb._wrap_code("pass", "C:\\tmp")


# ─────────────────────────── code_execute ───────────────────────────

class TestCodeExecuteFullPower:
    """code_execute 走 PERMISSION_LEVEL，full 模式放开网络与 AST 拦截。"""

    def test_full_mode_allows_subprocess_and_socket(self, monkeypatch):
        monkeypatch.setattr(command_exec_mod, "PERMISSION_LEVEL", "full")
        out = command_exec_mod.code_execute(
            "import socket\n"
            "s = socket.socket()\n"
            "print('created', s is not None)\n"
            "s.close()"
        )
        assert "created True" in out
        assert "安全检查问题" not in out
        assert "代码安全检查未通过" not in out

    def test_restricted_mode_blocks_dangerous_calls(self, monkeypatch):
        monkeypatch.setattr(command_exec_mod, "PERMISSION_LEVEL", "restricted")
        out = command_exec_mod.code_execute("import os\nos.system('echo hi')")
        assert "安全检查问题" in out or "代码安全检查未通过" in out

    def test_empty_code_still_rejected(self):
        assert "错误" in command_exec_mod.code_execute("   ")


# ─────────────────────────── ssh_exec 闸门 ───────────────────────────

class _FakeConn:
    """伪连接：够 ssh_exec 走完前置检查，真正执行时立刻报错，避免误连真机。"""

    def run(self, *args, **kwargs):
        raise AssertionError("测试不应真正发起远程执行")

    def is_closed(self):
        return False


@pytest.fixture
def fake_conn():
    ssh_ops._SSH_CONNECTIONS["pytest_fake"] = {
        "conn": _FakeConn(),
        "host": "192.0.2.10",   # TEST-NET-1，文档保留地址
        "user": "root",
        "connected_at": 0,
    }
    audit_before = len(ssh_ops._SSH_AUDIT_LOG)
    yield "pytest_fake"
    ssh_ops._SSH_CONNECTIONS.pop("pytest_fake", None)
    del ssh_ops._SSH_AUDIT_LOG[audit_before:]


class TestSshExecDangerousGate:
    """ssh_exec 的危险命令闸门必须与 run_command 同源。"""

    def test_full_mode_does_not_block(self, fake_conn, monkeypatch):
        monkeypatch.setattr(constants_mod, "PERMISSION_LEVEL", "full")
        res = ssh_ops.ssh_exec(
            "reboot", conn_id=fake_conn, confirm_dangerous=False, _internal=True
        )
        assert "检测到危险命令" not in res, "full 模式不得拦截"

    def test_full_mode_writes_audit_trail(self, fake_conn, monkeypatch):
        """放行必须留痕，否则"谁执行过危险命令"将无从追溯。"""
        monkeypatch.setattr(constants_mod, "PERMISSION_LEVEL", "full")
        ssh_ops.ssh_exec("reboot", conn_id=fake_conn, _internal=True)
        assert any("全权限放行" in e for e in ssh_ops._SSH_AUDIT_LOG)

    def test_restricted_mode_blocks(self, fake_conn, monkeypatch):
        monkeypatch.setattr(constants_mod, "PERMISSION_LEVEL", "restricted")
        res = ssh_ops.ssh_exec(
            "reboot", conn_id=fake_conn, confirm_dangerous=False, _internal=True
        )
        assert "检测到危险命令" in res

    def test_restricted_mode_allows_explicit_confirm(self, fake_conn, monkeypatch):
        monkeypatch.setattr(constants_mod, "PERMISSION_LEVEL", "restricted")
        res = ssh_ops.ssh_exec(
            "reboot", conn_id=fake_conn, confirm_dangerous=True, _internal=True
        )
        assert "检测到危险命令" not in res

    def test_harmless_command_unaffected_in_any_mode(self, fake_conn, monkeypatch):
        for level in ("full", "restricted"):
            monkeypatch.setattr(constants_mod, "PERMISSION_LEVEL", level)
            res = ssh_ops.ssh_exec(
                "systemctl status nginx",
                conn_id=fake_conn,
                confirm_dangerous=False,
                _internal=True,
            )
            assert "检测到危险命令" not in res, f"{level} 模式下普通命令不应受影响"

    def test_missing_connection_still_reported(self):
        res = ssh_ops.ssh_exec("ls", conn_id="no_such_conn", _internal=True)
        assert "不存在" in res


class TestSshCmdOkSemantics:
    """_ssh_cmd_ok 语义：拦截返回值必须判为失败，阻断 ssh_deploy 假成功。"""

    def test_blocked_result_is_failure(self):
        blocked = (
            "⚠️ 检测到危险命令（匹配模式: \\breboot\\b）\n"
            "命令: reboot\n"
            "如确认要执行，请重新调用并设置 confirm_dangerous=true"
        )
        assert ssh_ops._ssh_cmd_ok(blocked) is False

    def test_nonzero_exit_is_failure(self):
        assert ssh_ops._ssh_cmd_ok("started ok\n[退出码: 3]") is False

    def test_clean_output_is_success(self):
        assert ssh_ops._ssh_cmd_ok("nginx is running\nactive (running)") is True

    def test_empty_is_failure(self):
        assert ssh_ops._ssh_cmd_ok("") is False


# ─────────────────────────── 对齐性 ───────────────────────────

class TestAlignmentInvariant:
    """本地与远程必须由同一个开关管辖——这是本次改动的核心不变式。"""

    def test_permission_level_defaults_to_full(self):
        assert constants_mod.PERMISSION_LEVEL == "full"

    def test_ssh_exec_reads_permission_level_lazily(self):
        """ssh_exec 必须在**调用期**读 PERMISSION_LEVEL，否则测试与运行期切换都会失效。"""
        import inspect

        src = inspect.getsource(ssh_ops.ssh_exec)
        assert "from zeroai.core.constants import PERMISSION_LEVEL" in src, (
            "ssh_exec 需函数内导入，才能感知运行期的模式切换"
        )

    def test_module_level_import_absent(self):
        """模块导入期不得引入 zeroai.core（保持 ssh_ops 自包含的既定约束）。"""
        assert not hasattr(ssh_ops, "PERMISSION_LEVEL")

    def test_dangerous_patterns_still_populated(self):
        """放开的是"闸门"，不是"识别能力"——restricted 模式仍需能认出危险命令。"""
        for cmd in ("rm -rf /", "mkfs.ext4 /dev/sda1", "shutdown -h now", "reboot"):
            dangerous, pattern = ssh_ops._ssh_check_dangerous(cmd)
            assert dangerous, f"{cmd!r} 应被识别为危险命令"
            assert pattern
