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
