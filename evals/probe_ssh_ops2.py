# -*- coding: utf-8 -*-
"""验证：组报告型工具（disk_analyze / samba / log_view）是否吞掉连接错误。"""
from __future__ import annotations
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import zeroai.tools.ssh_ops as ssh_ops

def _reset():
    ssh_ops._SSH_CONNECTIONS.clear()
    ssh_ops._SSH_OS_CACHE.clear()
    ssh_ops._SSH_AUDIT_LOG.clear()

CASES = [
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
]
print("=" * 92)
print("连接不存在时，错误信息是否保留（防吞错）")
print("=" * 92)
for name, kw in CASES:
    _reset()
    out = getattr(ssh_ops, name)(**kw)
    has = "连接 'ghost' 不存在" in out
    print(f"  {'OK ' if has else '!! '}{name:<24} 保留错误={has}  首80: {str(out)[:80]!r}")

print()
print("=" * 92)
print("ssh_upload 本地文件不存在（活连接，验证在 SFTP 之前短路）")
print("=" * 92)
class _C:
    @property
    def is_closed(self): return False
    async def run(self, *a, **k): raise AssertionError("不应执行 run")
def _live():
    ssh_ops._SSH_CONNECTIONS["default"] = {
        "conn": _C(), "host": "192.0.2.55", "user": "root",
        "port": 22, "connected_at": 0, "remark": ""}
    ssh_ops._SSH_OS_CACHE["default"] = "linux"
_reset()
_live()
out = ssh_ops.ssh_upload(local_path="X:/definitely/not/here.txt", remote_path="/y")
print(f"  返回: {out[:100]!r}")
print(f"  含'本地文件不存在' = {'本地文件不存在' in out}")
print(f"  含'上传错误'       = {'上传错误' in out}")
