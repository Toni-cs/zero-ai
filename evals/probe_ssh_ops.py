# -*- coding: utf-8 -*-
"""探针 v2：ssh_ops 各工具的校验分支是否在【触网之前】短路。

修正：这些工具默认 conn_id="default"，假连接必须装到 default 才有效。
判定口径：假连接记录收到的命令条数。校验若先于执行短路 → 条数必须为 0。
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import zeroai.tools.ssh_ops as ssh_ops


class _ProbeResult:
    def __init__(self, stdout="", stderr="", exit_status=0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


class _ProbeConn:
    """假连接：记录所有被下发的命令，返回可编程结果。"""

    def __init__(self, closed=False, stdout="", stderr="", exit_status=0):
        self.closed = closed
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status
        self.commands: list = []

    @property
    def is_closed(self):
        return self.closed

    async def run(self, cmd, **kw):
        self.commands.append(cmd)
        return _ProbeResult(self.stdout, self.stderr, self.exit_status)


def _install(cid="default", closed=False, **kw):
    conn = _ProbeConn(closed=closed, **kw)
    ssh_ops._SSH_CONNECTIONS[cid] = {
        "conn": conn,
        "host": "192.0.2.55",
        "user": "root",
        "port": 22,
        "connected_at": 0,
        "remark": "",
    }
    ssh_ops._SSH_OS_CACHE[cid] = "linux"   # 跳过 OS 探测
    return conn


def _reset():
    ssh_ops._SSH_CONNECTIONS.clear()
    ssh_ops._SSH_OS_CACHE.clear()
    ssh_ops._SSH_AUDIT_LOG.clear()


def _short(s, n=88):
    s = str(s).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[:n] + "…"


# ── A：校验分支（无连接 / 坏参数），期望不联网 ─────────────────────────
CASES = [
    ("ssh_service_manage", dict(action="bogus", service="nginx"), "none"),
    ("ssh_service_manage", dict(action="status", service=""), "default"),
    ("ssh_service_manage", dict(action="status", service="bad name!"), "default"),
    ("ssh_service_manage", dict(action="reload", service="all"), "default"),
    ("ssh_process_check", dict(sort_by="bogus"), "default"),
    ("ssh_process_check", dict(sort_by="cpu"), "none"),
    ("ssh_network_diag", dict(action="bogus"), "default"),
    ("ssh_network_diag", dict(action="ping"), "default"),
    ("ssh_network_diag", dict(action="ping", target="bad target!"), "default"),
    ("ssh_network_diag", dict(action="ping", target="8.8.8.8"), "none"),
    ("ssh_docker_manage", dict(action="bogus"), "default"),
    ("ssh_docker_manage", dict(action="logs"), "default"),
    ("ssh_docker_manage", dict(action="logs", container="bad name!"), "default"),
    ("ssh_docker_manage", dict(action="ps"), "none"),
    ("ssh_firewall_manage", dict(action="bogus"), "default"),
    ("ssh_firewall_manage", dict(action="open", protocol="icmp"), "default"),
    ("ssh_firewall_manage", dict(action="open", port=99999), "default"),
    ("ssh_firewall_manage", dict(action="status"), "none"),
    ("ssh_log_view", dict(service="bad/service"), "default"),
    ("ssh_log_view", dict(service="nginx"), "none"),
    ("ssh_exec", dict(command="echo hi", conn_id="nope"), "none"),
    ("ssh_exec", dict(command="echo hi", conn_id="dead"), "dead"),
    ("ssh_upload", dict(local_path="X", remote_path="/y", conn_id="nope"), "none"),
    ("ssh_download", dict(remote_path="/r", local_path="l", conn_id="nope"), "none"),
    ("ssh_disconnect", dict(conn_id="nope"), "none"),
    ("ssh_deploy", dict(deploy_config={}, conn_id="nope"), "none"),
    ("ssh_health_check", dict(conn_id="nope"), "none"),
    ("ssh_disk_analyze", dict(path="/", conn_id="nope"), "none"),
    ("ssh_setup_samba_share", dict(conn_id="nope"), "none"),
]

print("=" * 100)
print("A. 各工具的首个错误分支（scene=期望联网方式）")
print("=" * 100)
for name, kwargs, scene in CASES:
    _reset()
    if scene == "none":
        probe = None
    elif scene == "dead":
        probe = _install("dead", closed=True)
    else:
        probe = _install("default")
    fn = getattr(ssh_ops, name)
    try:
        out = fn(**kwargs)
    except Exception as e:
        out = f"<抛异常 {type(e).__name__}: {e}>"
    n = len(probe.commands) if probe else 0
    if probe:
        probe.commands.clear()
    print(f"  {name}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})")
    print(f"        scene={scene:<7} cmds={n}  -> {_short(out)}")

print()
print("=" * 100)
print("B. ssh_exec 正常路径（default 假连接，linux）")
print("=" * 100)
for label, kw, cmd in [
    ("成功+有输出", dict(stdout="hello world"), "echo hi"),
    ("非零退出", dict(stdout="boom", exit_status=2), "false"),
    ("退出码0", dict(stdout="ok", exit_status=0), "true"),
    ("无输出", dict(), "true"),
    ("带 stderr+非零", dict(stderr="warn!", exit_status=1), "cmd"),
    ("超长 stdout", dict(stdout="x" * 9000), "big"),
    ("超长 stderr", dict(stderr="e" * 5000, exit_status=1), "cmd"),
]:
    _reset()
    conn = _install("default", **kw)
    out = ssh_ops.ssh_exec(command=cmd, _internal=True)
    print(f"  [{label}] cmds={len(conn.commands)} 总长={len(out)} "
          f"含退出码标记={'[退出码:' in out}")
    for line in out.splitlines()[:4]:
        print(f"      | {line[:94]}")

print()
print("=" * 100)
print("C. 前缀与 _internal")
print("=" * 100)
_reset()
conn = _install("default")
a = ssh_ops.ssh_exec(command="echo hi")
conn.commands.clear()
b = ssh_ops.ssh_exec(command="echo hi", _internal=True)
print(f"  默认(带前缀)  首行={a.splitlines()[0][:50]!r}")
print(f"  _internal      首行={b.splitlines()[0][:50]!r}")
print(f"  默认以[default]开头 = {a.startswith('[default]')}")

print()
print("=" * 100)
print("D. 编码注入（OS=windows）")
print("=" * 100)
for cmd in ['powershell -Command "Get-Date"', "powershell -Command 'Get-Date'",
            "ipconfig", "chcp 65001 & dir", "dir"]:
    _reset()
    conn = _install("default")
    ssh_ops._SSH_OS_CACHE["default"] = "windows"
    ssh_ops.ssh_exec(command=cmd, _internal=True)
    sent = conn.commands[-1] if conn.commands else "<未执行>"
    print(f"  入参: {cmd[:52]}")
    print(f"  实发: {sent[:118]}")

print()
print("=" * 100)
print("E. 危险命令 × PERMISSION_LEVEL 门控")
print("=" * 100)
from zeroai.core import constants as C
orig = C.PERMISSION_LEVEL
for level, confirm in [("full", False), ("restricted", False), ("restricted", True)]:
    _reset()
    C.PERMISSION_LEVEL = level
    conn = _install("default")
    out = ssh_ops.ssh_exec(command="rm -rf /", confirm_dangerous=confirm, _internal=True)
    audit_hits = len(ssh_ops._SSH_AUDIT_LOG)
    print(f"  level={level:<11} confirm={confirm!s:<5} cmds={len(conn.commands):<3} "
          f"audit={audit_hits}  -> {_short(out)}")
C.PERMISSION_LEVEL = orig

print()
print("=" * 100)
print("F. ssh_disconnect 成功路径 + 审计")
print("=" * 100)
_reset()
conn = _install("default")
out = ssh_ops.ssh_disconnect("default")
print(f"  返回: {out}")
print(f"  连接表剩余: {list(ssh_ops._SSH_CONNECTIONS)}")
print(f"  审计条数: {len(ssh_ops._SSH_AUDIT_LOG)}  末条: "
      f"{ssh_ops._SSH_AUDIT_LOG[-1] if ssh_ops._SSH_AUDIT_LOG else None}")
