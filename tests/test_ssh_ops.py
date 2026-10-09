# -*- coding: utf-8 -*-
"""ssh_ops 测试：16 个工具背后的纯逻辑、状态管理与注册一致性

背景（2026-10-09 摸底）：zeroai/tools/ssh_ops.py 共 2312 行、16 个公开工具，
在本轮之前**没有任何测试文件**，是全项目最大的未设防面。

这里只测**不需要真实服务器**的部分：
  - 校验/识别/脱敏等纯函数
  - 连接表、OS 缓存、审计日志这些模块级状态的读写约定
  - 工具注册一致性（防止 schema 漂移）
  - 静默异常处理器的回归守卫

真实网络交互（建链、SFTP、命令回显）不在此测，避免用例依赖外网而变脆。
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import zeroai.tools.registry as registry_mod
import zeroai.tools.ssh_ops as ssh_ops

MODULE_PATH = Path(inspect.getfile(ssh_ops))


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """每个用例前后隔离模块级全局状态，避免用例之间串味。"""
    conns = dict(ssh_ops._SSH_CONNECTIONS)
    audit = list(ssh_ops._SSH_AUDIT_LOG)
    oscache = dict(ssh_ops._SSH_OS_CACHE)
    yield
    ssh_ops._SSH_CONNECTIONS.clear()
    ssh_ops._SSH_CONNECTIONS.update(conns)
    ssh_ops._SSH_AUDIT_LOG.clear()
    ssh_ops._SSH_AUDIT_LOG.extend(audit)
    ssh_ops._SSH_OS_CACHE.clear()
    ssh_ops._SSH_OS_CACHE.update(oscache)


class _FakeConn:
    """够 ssh_list / _ssh_is_conn_closed 使用的假连接，绝不发起真实网络。"""

    def __init__(self, closed: bool = False):
        self.closed = closed

    @property
    def is_closed(self):  # 旧版 asyncssh 形态：属性
        return self.closed


class _FakeConnMethodStyle:
    """新版 asyncssh（2.24+）形态：is_closed 是方法而非属性。"""

    def __init__(self, closed: bool = False):
        self.closed = closed

    def is_closed(self):
        return self.closed


def _add_conn(cid: str = "t", remark: str = "", closed: bool = False):
    ssh_ops._SSH_CONNECTIONS[cid] = {
        "conn": _FakeConn(closed=closed),
        "host": "192.0.2.55",
        "user": "root",
        "port": 22,
        "connected_at": 0,
        "remark": remark,
    }
    return cid


# ══════════════════════════ _ssh_validate_host ══════════════════════════

class TestValidateHost:
    def test_empty_rejected(self):
        ok, msg = ssh_ops._ssh_validate_host("")
        assert ok is False and "不能为空" in msg

    def test_non_string_rejected(self):
        ok, msg = ssh_ops._ssh_validate_host(None)
        assert ok is False and "不能为空" in msg

    @pytest.mark.parametrize("host", ["192.168.1.10", "10.0.0.1", "8.8.8.8"])
    def test_valid_ipv4(self, host):
        assert ssh_ops._ssh_validate_host(host)[0] is True

    def test_invalid_ip_segment_rejected(self):
        ok, msg = ssh_ops._ssh_validate_host("192.168.999.1")
        assert ok is False and "IP地址段无效" in msg

    @pytest.mark.parametrize(
        "host",
        ["example.com", "sub.example.co.uk", "my-server.example.org"],
    )
    def test_valid_domain(self, host):
        assert ssh_ops._ssh_validate_host(host)[0] is True

    def test_garbage_rejected(self):
        ok, msg = ssh_ops._ssh_validate_host("not a host!!")
        assert ok is False and "格式无效" in msg

    def test_protocol_prefix_stripped(self):
        assert ssh_ops._ssh_validate_host("ssh://192.168.1.10")[0] is True
        assert ssh_ops._ssh_validate_host("SSH://192.168.1.10")[0] is True

    def test_port_stripped_before_check(self):
        assert ssh_ops._ssh_validate_host("192.168.1.10:2222")[0] is True
        assert ssh_ops._ssh_validate_host("example.com:2222")[0] is True

    def test_default_allows_private_ip(self):
        """默认 _SSH_BLOCK_PRIVATE_IPS=False：内网地址可连（NAS/内网服务器场景）。"""
        assert ssh_ops._SSH_BLOCK_PRIVATE_IPS is False
        assert ssh_ops._ssh_validate_host("192.168.1.10")[0] is True

    @pytest.mark.parametrize(
        "host,blocked",
        [
            ("10.0.0.1", True),
            ("127.0.0.1", True),
            ("172.16.0.1", True),
            ("172.31.255.255", True),
            ("192.168.0.1", True),
            ("172.32.0.1", False),   # 172.x 但不在 16-31 段
            ("192.1.0.1", False),    # 192.x 但不是 168
        ],
    )
    def test_private_ip_policy(self, host, blocked, monkeypatch):
        monkeypatch.setattr(ssh_ops, "_SSH_BLOCK_PRIVATE_IPS", True)
        ok, msg = ssh_ops._ssh_validate_host(host)
        assert ok is (not blocked), f"{host}: {msg}"


# ══════════════════════════ _ssh_check_dangerous ══════════════════════════

class TestCheckDangerous:
    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda",
            "shutdown -h now",
            "init 0",
            "halt",
            "reboot now",
            "echo x > /dev/sda",
            "iptables -F",
            "chmod -R 777 /",
            ":(){ :|:& };:",
        ],
    )
    def test_dangerous_commands_detected(self, cmd):
        dangerous, pattern = ssh_ops._ssh_check_dangerous(cmd)
        assert dangerous is True, f"{cmd!r} 未被识别"
        assert pattern

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la /opt",
            "systemctl status nginx",
            "df -h",
            "tail -f /var/log/syslog",
            "grep -r foo /etc",
        ],
    )
    def test_benign_commands_pass(self, cmd):
        dangerous, pattern = ssh_ops._ssh_check_dangerous(cmd)
        assert dangerous is False and pattern is None, f"{cmd!r} 被误判"

    def test_case_insensitive(self):
        assert ssh_ops._ssh_check_dangerous("REBOOT now")[0] is True
        assert ssh_ops._ssh_check_dangerous("MKFS /dev/sda")[0] is True


# ══════════════════════════ _ssh_format_prefix ══════════════════════════

class TestFormatPrefix:
    def test_unknown_conn(self):
        assert ssh_ops._ssh_format_prefix("nope") == "[nope]"

    def test_known_conn_without_remark(self):
        _add_conn("a")
        assert ssh_ops._ssh_format_prefix("a") == "[a]"

    def test_known_conn_with_remark(self):
        _add_conn("nas", remark="NAS存储服务器")
        assert ssh_ops._ssh_format_prefix("nas") == "[nas | NAS存储服务器]"

    def test_never_leaks_ip(self):
        """安全设计：前缀只用 conn_id/备注，绝不能出现服务器地址。"""
        _add_conn("x", remark="数据库")
        prefix = ssh_ops._ssh_format_prefix("x")
        assert "192.0.2.55" not in prefix
        assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", prefix)


# ══════════════════════════ _ssh_audit ══════════════════════════

class TestAuditLog:
    def test_masks_ip_to_two_octets(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("192.168.44.77", "root", "ls")
        entry = ssh_ops._SSH_AUDIT_LOG[-1]
        assert "192.168.***.***" in entry
        assert "192.168.44.77" not in entry

    def test_domain_kept_verbatim(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("nas.local", "root", "uptime")
        assert "nas.local" in ssh_ops._SSH_AUDIT_LOG[-1]

    def test_command_truncated_at_200(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("h", "u", "x" * 500)
        assert "x" * 201 not in ssh_ops._SSH_AUDIT_LOG[-1]
        assert ssh_ops._SSH_AUDIT_LOG[-1].count("x") == 200

    def test_summary_truncated_at_100(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("h", "u", "c", "y" * 300)
        assert ssh_ops._SSH_AUDIT_LOG[-1].count("y") == 100

    def test_capped_at_max(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        for i in range(ssh_ops._SSH_AUDIT_MAX + 40):
            ssh_ops._ssh_audit("h", "u", f"cmd{i}")
        assert len(ssh_ops._SSH_AUDIT_LOG) == ssh_ops._SSH_AUDIT_MAX
        # 淘汰的是最旧的
        assert "cmd0" not in ssh_ops._SSH_AUDIT_LOG[0]
        assert f"cmd{ssh_ops._SSH_AUDIT_MAX + 39}" in ssh_ops._SSH_AUDIT_LOG[-1]


# ══════════════════════════ _ssh_is_conn_closed ══════════════════════════

class TestIsConnClosed:
    def test_none_is_closed(self):
        assert ssh_ops._ssh_is_conn_closed(None) is True

    def test_property_style_old_asyncssh(self):
        assert ssh_ops._ssh_is_conn_closed(_FakeConn(closed=False)) is False
        assert ssh_ops._ssh_is_conn_closed(_FakeConn(closed=True)) is True

    def test_method_style_new_asyncssh_2_24(self):
        """2.24+ 把 is_closed 改成方法；判错会让活连接被当死连接丢弃。"""
        assert ssh_ops._ssh_is_conn_closed(_FakeConnMethodStyle(False)) is False
        assert ssh_ops._ssh_is_conn_closed(_FakeConnMethodStyle(True)) is True

    def test_raising_conn_treated_as_closed(self):
        class Boom:
            @property
            def is_closed(self):
                raise RuntimeError("boom")

        assert ssh_ops._ssh_is_conn_closed(Boom()) is True

    def test_object_without_is_closed_treated_as_closed(self):
        assert ssh_ops._ssh_is_conn_closed(object()) is True


# ══════════════════════════ _ssh_detect_os ══════════════════════════

class TestDetectOs:
    def test_unknown_conn_defaults_to_linux(self):
        assert ssh_ops._ssh_detect_os("ghost") == "linux"

    def test_unknown_conn_purges_stale_cache(self):
        ssh_ops._SSH_OS_CACHE["ghost"] = "windows"
        ssh_ops._ssh_detect_os("ghost")
        assert "ghost" not in ssh_ops._SSH_OS_CACHE

    def test_cached_value_returned_without_probing(self):
        _add_conn("c1")
        ssh_ops._SSH_OS_CACHE["c1"] = "windows"
        assert ssh_ops._ssh_detect_os("c1") == "windows"

    def test_no_probe_attempts_when_cached(self):
        """命中缓存不应再发起远程命令——用会抛异常的连接来验证没走到探测。"""
        class NoRun:
            is_closed = False

            def run(self, *a, **k):
                raise AssertionError("命中缓存时不应调用 conn.run")

        ssh_ops._SSH_CONNECTIONS["c2"] = {
            "conn": NoRun(), "host": "h", "user": "u",
            "port": 22, "connected_at": 0, "remark": "",
        }
        ssh_ops._SSH_OS_CACHE["c2"] = "linux"
        assert ssh_ops._ssh_detect_os("c2") == "linux"


# ══════════════════════════ ssh_list ══════════════════════════

class TestSshList:
    def test_unknown_conn(self):
        assert "不存在" in ssh_ops.ssh_list("ghost")

    def test_no_connections(self):
        ssh_ops._SSH_CONNECTIONS.clear()
        out = ssh_ops.ssh_list()
        assert "(无活动连接)" in out
        assert "ssh_connect" in out  # 给出下一步提示

    def test_single_conn_detail_shows_status(self):
        cid = _add_conn("nas", remark="NAS存储")
        out = ssh_ops.ssh_list(cid)
        assert f"连接ID: {cid}" in out
        assert "✅ 已连接" in out
        assert "NAS存储" in out
        assert "root" in out

    def test_closed_conn_shown_as_disconnected(self):
        cid = _add_conn("dead", closed=True)
        assert "❌ 已断开" in ssh_ops.ssh_list(cid)

    def test_list_view_never_leaks_ip(self):
        """安全设计：ssh_list 是"查看连接"的入口，也不能暴露服务器地址。"""
        _add_conn("nas", remark="NAS")
        out = ssh_ops.ssh_list()
        assert "192.0.2.55" not in out

    def test_audit_section_present_when_log_exists(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("192.168.44.77", "root", "uptime")
        out = ssh_ops.ssh_list()
        assert "最近操作审计" in out
        assert "192.168.44.77" not in out  # 审计区同样脱敏


# ══════════════════════════ ssh_connect 校验分支 ══════════════════════════

class TestSshConnectValidation:
    def test_empty_host_rejected_without_connecting(self):
        out = ssh_ops.ssh_connect("", "root")
        assert out.startswith("连接失败")
        assert ssh_ops._SSH_CONNECTIONS == {}

    def test_garbage_host_rejected_without_connecting(self):
        out = ssh_ops.ssh_connect("!!!bad!!!", "root")
        assert out.startswith("连接失败")
        assert ssh_ops._SSH_CONNECTIONS == {}

    def test_invalid_host_does_not_teardown_existing(self):
        """先校验后断旧连接：新 host 不合法时，不能把还在用的旧连接拆掉。

        （ssh_connect 的实际顺序是 validate_host -> 断旧连接 -> 建新连接。
        曾经的断言写成"非法 host 也会 pop 旧条目"，是把流程读反了。）
        """
        _add_conn("dup")

        class Boom:
            is_closed = False

            def close(self):
                raise AssertionError("非法 host 不该走到断开旧连接这一步")

        ssh_ops._SSH_CONNECTIONS["dup"]["conn"] = Boom()
        out = ssh_ops.ssh_connect("!!!bad!!!", "root", conn_id="dup")
        assert out.startswith("连接失败")
        assert "dup" in ssh_ops._SSH_CONNECTIONS, "旧连接被误拆"

    def test_validation_failure_leaves_connection_table_untouched(self):
        """连接失败不得留下半初始化的连接条目。"""
        ssh_ops._SSH_CONNECTIONS.clear()
        ssh_ops.ssh_connect("not a host!!", "root", conn_id="half")
        assert ssh_ops._SSH_CONNECTIONS == {}


# ══════════════════════════ 注册一致性 ══════════════════════════

class TestRegistryConsistency:
    def _public_ssh_tools(self):
        import inspect as _inspect

        return sorted(
            n for n, o in vars(ssh_ops).items()
            if n.startswith("ssh_")
            and _inspect.isfunction(o)
            and not n.startswith("_")
        )

    def test_all_16_tools_registered(self):
        tools = self._public_ssh_tools()
        assert len(tools) == 16, f"公开工具数变为 {len(tools)}，请同步本测试"
        registered = {
            t["function"]["name"]
            for t in registry_mod.TOOLS
            if t["function"]["name"].startswith("ssh_")
        }
        missing = set(tools) - registered
        assert not missing, f"这些 ssh 工具未注册进 TOOLS: {sorted(missing)}"

    def test_tool_map_entries_exist(self):
        for name in self._public_ssh_tools():
            assert name in registry_mod.TOOL_MAP, f"{name} 未进 TOOL_MAP"
            assert registry_mod.TOOL_MAP[name] is getattr(ssh_ops, name)

    def test_tool_map_points_at_real_function(self):
        for name, fn in registry_mod.TOOL_MAP.items():
            if name.startswith("ssh_"):
                assert getattr(ssh_ops, name) is fn, f"{name} 指向错误对象"

    def test_schema_required_params_are_callable_params(self):
        """schema 里的 required 必须是函数真有的参数，否则模型会传错。"""
        import inspect as _inspect

        for t in registry_mod.TOOLS:
            name = t["function"]["name"]
            if not name.startswith("ssh_"):
                continue
            fn = getattr(ssh_ops, name, None)
            if fn is None:
                continue
            sig = _inspect.signature(fn)
            props = set(t["function"].get("parameters", {}).get("properties", {}))
            for req in t["function"].get("parameters", {}).get("required", []):
                assert req in sig.parameters, f"{name} 的 required 参数 {req} 不存在"
            extra = props - set(sig.parameters)
            assert not extra, f"{name} schema 声明了函数没有的参数: {sorted(extra)}"


# ══════════════════════════ 静默异常回归守卫 ══════════════════════════

class TestSilentExceptGuard:
    """守住 8 处已知的静默 except，新增的同类写法必须在此登记并说明理由。"""

    KNOWN_SILENT = {
        "_ssh_detect_os",   # 3 处：三级探测逐级回退，失败即试下一招
        "ssh_connect",      # 1 处：断旧连接失败不应阻断新连接
        "_upload",          # 1 处：chmod 644 失败不影响上传本身
        "_close",           # 1 处：关闭尽力而为
        "ssh_disconnect",   # 1 处：_close 的冗余包裹
        "ssh_health_check", # 1 处：仅"建议重启"的可选提示
    }

    @classmethod
    def _silent_handlers(cls):
        src = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = node.body
            silent = len(body) == 1 and isinstance(body[0], ast.Pass)
            if silent:
                found.append(node)
        return tree, found

    @staticmethod
    def _owner(tree, node):
        best, span = "<module>", 10**9
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(n, "end_lineno", n.lineno)
                if n.lineno <= node.lineno <= end and (end - n.lineno) < span:
                    best, span = n.name, end - n.lineno
        return best

    def test_silent_handler_count_is_exactly_eight(self):
        _, found = self._silent_handlers()
        assert len(found) == 8, (
            f"静默 except 数量变为 {len(found)}（基线 8）。"
            "若属有意新增，请在 KNOWN_SILENT 登记并说明为什么可以静默。"
        )

    def test_silent_handlers_only_in_known_functions(self):
        tree, found = self._silent_handlers()
        owners = {self._owner(tree, n) for n in found}
        unexpected = owners - self.KNOWN_SILENT
        assert not unexpected, f"出现未登记的静默异常处理: {sorted(unexpected)}"

    def test_no_bare_except(self):
        """裸 except: 会吞掉 KeyboardInterrupt/SystemExit，绝对禁止。"""
        src = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        bare = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ExceptHandler) and n.type is None
        ]
        assert not bare, f"存在 {len(bare)} 个裸 except:"


# ══════════════════════════════════════════════════════════════════════════
# 第二轮补齐（2026-10-09）：ssh_exec / 运维工具的执行路径
#
# 第一轮只覆盖了纯函数与连接表，16 个工具里真正会"下发命令"的路径全部未设防。
# 这里用假连接（async run 返回可编程结果）驱动真实代码路径——_ssh_run_async 跑在
# 持久后台事件循环线程上，假连接只要提供 async def run() 就能走通。
#
# 所有断言值均来自 evals/probe_ssh_ops.py 的实测输出，不是照抄源码字面量；
# 每条测试都断言"错误分支有没有真的打到服务器"（conn.commands），这是牙齿所在。
# ══════════════════════════════════════════════════════════════════════════


class _ExecResult:
    """asyncssh 连接 run() 的返回对象（ssh_exec 只用到这三个属性）。"""

    def __init__(self, stdout="", stderr="", exit_status=0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


class _ExecConn:
    """假连接：记录每一条被下发的命令，返回可编程结果。绝不发起真实网络。"""

    def __init__(self, closed=False, stdout="", stderr="", exit_status=0):
        self.closed = closed
        self._stdout = stdout
        self._stderr = stderr
        self._exit = exit_status
        self.commands = []

    @property
    def is_closed(self):          # 旧版 asyncssh 形态：属性
        return self.closed

    async def run(self, cmd, **kw):
        self.commands.append(cmd)
        return _ExecResult(self._stdout, self._stderr, self._exit)


def _install_exec(cid="default", remark="", os_type="linux", **kw):
    """装一个活连接到模块表，并清空审计日志让计数断言确定。"""
    conn = _ExecConn(**kw)
    ssh_ops._SSH_CONNECTIONS[cid] = {
        "conn": conn, "host": "192.0.2.55", "user": "root",
        "port": 22, "connected_at": 0, "remark": remark,
    }
    ssh_ops._SSH_OS_CACHE[cid] = os_type      # 预置，跳过 OS 探测
    ssh_ops._SSH_AUDIT_LOG.clear()
    return conn


# ── ssh_exec：连接门控 ──────────────────────────────────────────────────

class TestSshExecConnectionGating:
    def test_missing_conn_short_circuits_without_network(self):
        ssh_ops._SSH_CONNECTIONS.clear()
        out = ssh_ops.ssh_exec(command="echo hi", conn_id="ghost")
        assert "连接 'ghost' 不存在" in out
        assert "ssh_connect" in out, "必须告诉模型下一步该调谁"

    def test_closed_conn_reported_and_evicted(self):
        conn = _install_exec(cid="t-closed", closed=True)
        out = ssh_ops.ssh_exec(command="echo hi", conn_id="t-closed")
        assert "已断开" in out
        # 断开的连接必须被踢出连接表，否则下次仍会命中旧连接对象
        assert "t-closed" not in ssh_ops._SSH_CONNECTIONS
        assert conn.commands == [], "已断开的连接不该再下发命令"


# ── ssh_exec：退出码语义（_ssh_cmd_ok 判定的前提） ──────────────────────

class TestSshExecExitCode:
    def test_exit_zero_has_no_marker(self):
        """关键不对称：退出码只在非零时追加，exit=0 绝不能出现 [退出码: 0]。"""
        _install_exec(stdout="ok", exit_status=0)
        out = ssh_ops.ssh_exec(command="true", _internal=True)
        assert out == "ok"
        assert "[退出码:" not in out

    def test_nonzero_exit_appends_marker(self):
        _install_exec(stdout="boom", exit_status=2)
        out = ssh_ops.ssh_exec(command="false", _internal=True)
        assert "[退出码: 2]" in out
        assert out.splitlines()[-1] == "[退出码: 2]"

    def test_no_output_reports_no_output(self):
        _install_exec()
        out = ssh_ops.ssh_exec(command="true", _internal=True)
        assert out == "[无输出]"
        assert "[退出码:" not in out

    def test_stderr_block_and_exit_marker(self):
        _install_exec(stderr="warn!", exit_status=1)
        out = ssh_ops.ssh_exec(command="cmd", _internal=True)
        assert out.splitlines()[0] == "[stderr]"
        assert "warn!" in out
        assert "[退出码: 1]" in out

    def test_stderr_alone_does_not_trigger_exit_marker(self):
        """退出码只看 exit_status，与 stderr 是否为空无关。"""
        _install_exec(stderr="warn!", exit_status=0)
        out = ssh_ops.ssh_exec(command="cmd", _internal=True)
        assert "[stderr]" in out
        assert "[退出码:" not in out


# ── ssh_exec：截断 ──────────────────────────────────────────────────────

class TestSshExecTruncation:
    def test_long_stdout_truncated_at_8000(self):
        _install_exec(stdout="x" * 9000)
        out = ssh_ops.ssh_exec(command="big", _internal=True)
        assert "... (输出过长，已截断，共 9000 字符)" in out
        assert "x" * 8000 in out, "前 8000 字符应保留"
        assert "x" * 8001 not in out, "第 8001 字符起必须丢弃"

    def test_long_stderr_truncated_at_4000(self):
        _install_exec(stderr="e" * 5000, exit_status=1)
        out = ssh_ops.ssh_exec(command="cmd", _internal=True)
        assert "... (错误输出过长，已截断，共 5000 字符)" in out
        assert "e" * 4000 in out
        assert "e" * 4001 not in out


# ── ssh_exec：前缀 ──────────────────────────────────────────────────────

class TestSshExecPrefix:
    def test_default_prefix_identifies_server(self):
        _install_exec(stdout="hi")
        out = ssh_ops.ssh_exec(command="echo hi")
        assert out.startswith("[default]\n")

    def test_remark_appears_in_prefix(self):
        _install_exec(stdout="hi", remark="NAS存储服务器")
        out = ssh_ops.ssh_exec(command="echo hi")
        assert out.startswith("[default | NAS存储服务器]")

    def test_internal_suppresses_prefix(self):
        """运维工具内部调用传 _internal=True，避免前缀在报告里重复堆叠。"""
        _install_exec(stdout="hi")
        out = ssh_ops.ssh_exec(command="echo hi", _internal=True)
        assert out == "hi"

    def test_prefix_never_leaks_ip(self):
        _install_exec(stdout="hi")
        out = ssh_ops.ssh_exec(command="echo hi")
        assert "192.0.2.55" not in out


# ── ssh_exec：Windows 编码注入 ──────────────────────────────────────────

class TestSshExecEncodingInjection:
    """中文 Windows 默认 GBK，asyncssh 按 UTF-8 解码会乱码/报错。"""

    @pytest.mark.parametrize("cmd", [
        'powershell -Command "Get-Date"',
        "powershell -Command 'Get-Date'",
    ])
    def test_powershell_gets_utf8_console_injection(self, cmd):
        conn = _install_exec(os_type="windows")
        ssh_ops.ssh_exec(command=cmd, _internal=True)
        sent = conn.commands[-1]
        assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in sent
        assert sent.startswith("powershell"), "只在 -Command 之后注入，不动命令本体"

    @pytest.mark.parametrize("cmd", ["ipconfig", "dir", "netstat -an"])
    def test_plain_cmd_gets_chcp_prefix(self, cmd):
        conn = _install_exec(os_type="windows")
        ssh_ops.ssh_exec(command=cmd, _internal=True)
        assert conn.commands[-1] == f"chcp 65001 >nul 2>&1 & {cmd}"

    def test_existing_chcp_not_doubled(self):
        conn = _install_exec(os_type="windows")
        ssh_ops.ssh_exec(command="chcp 65001 & dir", _internal=True)
        assert conn.commands[-1] == "chcp 65001 & dir"

    @pytest.mark.parametrize("os_type", ["linux"])
    def test_linux_commands_untouched(self, os_type):
        conn = _install_exec(os_type=os_type)
        ssh_ops.ssh_exec(command="ipconfig", _internal=True)
        assert conn.commands[-1] == "ipconfig"


# ── ssh_exec：危险命令 × PERMISSION_LEVEL ──────────────────────────────

class TestSshExecDangerousGating:
    """与本地 run_command 对齐：统一受 PERMISSION_LEVEL 管辖，不再无条件拦截。"""

    @staticmethod
    def _set_level(monkeypatch, level):
        from zeroai.core import constants as C
        monkeypatch.setattr(C, "PERMISSION_LEVEL", level)

    def test_full_mode_executes_and_leaves_audit(self, monkeypatch):
        self._set_level(monkeypatch, "full")
        conn = _install_exec()
        ssh_ops.ssh_exec(command="rm -rf /", _internal=True)
        assert len(conn.commands) == 1, "full 模式必须真的放行"
        assert any("[全权限放行" in e for e in ssh_ops._SSH_AUDIT_LOG), \
            "放行必须留痕，否则 ssh_list 无法追溯危险命令"

    def test_restricted_blocks_before_network(self, monkeypatch):
        self._set_level(monkeypatch, "restricted")
        conn = _install_exec()
        out = ssh_ops.ssh_exec(command="rm -rf /", _internal=True)
        assert "危险命令" in out
        assert "confirm_dangerous" in out, "要告知模型如何显式放行"
        assert conn.commands == [], "被拦截时一个字节都不该发出去"
        assert ssh_ops._SSH_AUDIT_LOG == [], "被拦的命令不能记成放行"

    def test_restricted_with_confirm_executes(self, monkeypatch):
        self._set_level(monkeypatch, "restricted")
        conn = _install_exec()
        ssh_ops.ssh_exec(command="rm -rf /", confirm_dangerous=True, _internal=True)
        assert len(conn.commands) == 1


# ── _ssh_cmd_ok：防"假成功汇报"的闸门 ──────────────────────────────────

class TestSshCmdOk:
    @pytest.mark.parametrize("result,expected", [
        ("hello", True),
        ("[无输出]", True),
        ("[default]\nhello", True),
        ("命令自己打印 exit=1 也算成功", True),
        ("exit=1\n实际是成功命令的输出", True),
        ("", False),
        (None, False),
        ("错误：连接 'x' 不存在", False),
        ("错误:连接已断开", False),
        ("执行错误: boom", False),
        ("上传失败: SFTP错误", False),
        ("⚠️ 检测到危险命令（匹配模式: p）", False),
        ("out\n[退出码: 1]", False),
        ("out\n[退出码: 0]", False),
        ("out\n[退出码: -1]", False),
    ])
    def test_verdict(self, result, expected):
        assert ssh_ops._ssh_cmd_ok(result) is expected

    def test_stdout_exit_text_is_not_misjudged(self):
        """docstring 明确承诺：命令自己打印 exit=1 不该被宽泛匹配成失败。"""
        assert ssh_ops._ssh_cmd_ok("build failed: exit=1\nsee log") is True


# ── 运维工具：坏参数必须在触网之前被拒 ──────────────────────────────────

class TestOpsValidationShortCircuit:
    """装的是【活连接】——校验必须自己拦住，否则 conn.commands 不会为空。"""

    @pytest.mark.parametrize("name,kwargs", [
        ("ssh_service_manage", dict(action="bogus", service="nginx")),
        ("ssh_service_manage", dict(action="status", service="")),
        ("ssh_service_manage", dict(action="status", service="bad name!")),
        ("ssh_service_manage", dict(action="reload", service="all")),
        ("ssh_process_check", dict(sort_by="bogus")),
        ("ssh_network_diag", dict(action="bogus")),
        ("ssh_network_diag", dict(action="ping")),
        ("ssh_network_diag", dict(action="ping", target="bad target!")),
        ("ssh_docker_manage", dict(action="bogus")),
        ("ssh_docker_manage", dict(action="logs")),
        ("ssh_docker_manage", dict(action="logs", container="bad name!")),
        ("ssh_firewall_manage", dict(action="bogus")),
        ("ssh_firewall_manage", dict(action="open", protocol="icmp")),
        # 端口合法时，protocol 校验必须自己拦——否则上面那条只是被端口校验"顺手"救了
        ("ssh_firewall_manage", dict(action="open", protocol="icmp", port=8080)),
        ("ssh_firewall_manage", dict(action="open", port=99999)),
    ])
    def test_bad_param_rejected_without_network(self, name, kwargs):
        conn = _install_exec()
        out = getattr(ssh_ops, name)(**kwargs)
        assert isinstance(out, str) and out
        assert out.startswith(("错误", "⚠️")), f"{name} 没走拒绝分支: {out[:70]}"
        assert conn.commands == [], f"{name} 把坏参数打到服务器了: {conn.commands}"


class TestMissingConnErrorNotSwallowed:
    """组报告型工具（disk_analyze/samba/network/docker…）会先搭报告骨架，
    但底层连接错误必须保留——否则模型看到一份"看起来正常"的空报告。"""

    @pytest.mark.parametrize("name,kwargs", [
        ("ssh_disk_analyze", dict(path="/", conn_id="ghost")),
        ("ssh_setup_samba_share", dict(conn_id="ghost")),
        ("ssh_log_view", dict(service="nginx", conn_id="ghost")),
        ("ssh_health_check", dict(conn_id="ghost")),
        ("ssh_process_check", dict(sort_by="cpu", conn_id="ghost")),
        ("ssh_network_diag", dict(action="stats", conn_id="ghost")),
        ("ssh_docker_manage", dict(action="ps", conn_id="ghost")),
        ("ssh_firewall_manage", dict(action="status", conn_id="ghost")),
        ("ssh_service_manage", dict(action="status", service="nginx", conn_id="ghost")),
        ("ssh_deploy", dict(deploy_config={}, conn_id="ghost")),
    ])
    def test_connection_error_survives(self, name, kwargs):
        ssh_ops._SSH_CONNECTIONS.clear()
        out = getattr(ssh_ops, name)(**kwargs)
        assert "连接 'ghost' 不存在" in out, f"{name} 吞掉了连接错误: {out[:80]!r}"


# ── 文件传输 ────────────────────────────────────────────────────────────

class TestSshTransferValidation:
    @pytest.mark.parametrize("name,kwargs", [
        ("ssh_upload", dict(local_path="X", remote_path="/y", conn_id="ghost")),
        ("ssh_download", dict(remote_path="/r", local_path="l", conn_id="ghost")),
    ])
    def test_missing_conn_short_circuits(self, name, kwargs):
        ssh_ops._SSH_CONNECTIONS.clear()
        out = getattr(ssh_ops, name)(**kwargs)
        assert "连接 'ghost' 不存在" in out
        assert "ssh_connect" in out

    @pytest.mark.parametrize("name,kwargs", [
        ("ssh_upload", dict(local_path="X", remote_path="/y")),
        ("ssh_download", dict(remote_path="/r", local_path="l")),
    ])
    def test_closed_conn_evicted(self, name, kwargs):
        _install_exec(cid="default", closed=True)
        out = getattr(ssh_ops, name)(**kwargs)
        assert "已断开" in out
        assert "default" not in ssh_ops._SSH_CONNECTIONS

    def test_upload_missing_local_file_before_sftp(self):
        """假连接没有 start_sftp_client——若走到 SFTP 会报"上传错误"，
        所以断言"本地文件不存在"就证明校验确实短路在前。"""
        _install_exec()
        out = ssh_ops.ssh_upload(
            local_path="X:/definitely/not/here.txt", remote_path="/y")
        assert "本地文件不存在" in out
        assert "上传错误" not in out


# ── ssh_disconnect 与审计脱敏 ──────────────────────────────────────────

class TestSshDisconnect:
    def test_success_removes_conn_and_audits_masked(self):
        _install_exec(remark="NAS")
        out = ssh_ops.ssh_disconnect("default")
        assert out.startswith("✅ 已断开连接")
        assert "root@NAS" in out, "用备注标识，不暴露 IP"
        assert "default" not in ssh_ops._SSH_CONNECTIONS
        assert ssh_ops._SSH_AUDIT_LOG, "断开必须留审计"
        entry = ssh_ops._SSH_AUDIT_LOG[-1]
        assert "192.0.2.55" not in entry, "完整 IP 绝不能进审计日志"
        assert "192.0.***.***" in entry
        assert "[DISCONNECT]" in entry

    def test_missing_conn_message(self):
        ssh_ops._SSH_CONNECTIONS.clear()
        out = ssh_ops.ssh_disconnect("ghost")
        assert "不存在" in out


class TestAuditLog:
    def test_ip_masked_to_two_octets(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("192.0.2.55", "root", "uptime", "ok")
        entry = ssh_ops._SSH_AUDIT_LOG[-1]
        assert "192.0.2.55" not in entry
        assert "192.0.***.***" in entry
        assert "root@" in entry and "uptime" in entry and "ok" in entry

    def test_domain_not_masked(self):
        """域名不是 IP，按设计保留原样，否则排查时无从定位。"""
        ssh_ops._SSH_AUDIT_LOG.clear()
        ssh_ops._ssh_audit("nas.example.com", "root", "uptime", "")
        assert "nas.example.com" in ssh_ops._SSH_AUDIT_LOG[-1]

    def test_log_capped_at_max(self):
        ssh_ops._SSH_AUDIT_LOG.clear()
        cap = ssh_ops._SSH_AUDIT_MAX
        for i in range(cap + 25):
            ssh_ops._ssh_audit("10.0.0.1", "root", f"cmd{i}", "")
        assert len(ssh_ops._SSH_AUDIT_LOG) == cap, "审计日志必须封顶，不能无限涨"
        assert "cmd224" in ssh_ops._SSH_AUDIT_LOG[-1], "最新条目应在末尾"
        assert ssh_ops._SSH_AUDIT_LOG[0].endswith("cmd25"), "最旧的 25 条已被挤出"
