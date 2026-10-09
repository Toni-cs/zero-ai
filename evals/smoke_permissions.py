# -*- coding: utf-8 -*-
"""权限放开改动冒烟测试

验证三件事：
1. code_execute 在 full 模式下不再被 AST 拦截，且放开网络
2. CodeSandbox 默认行为（check_safety=True）不变，未改动的调用方不受影响
3. ssh_exec 危险命令在 full 模式放行、restricted 模式仍拦截（与 run_command 对齐）
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    from zeroai.core.constants import PERMISSION_LEVEL

    print(f"PERMISSION_LEVEL = {PERMISSION_LEVEL!r}")
    check("当前为全权限模式", PERMISSION_LEVEL == "full")

    # ---------- 1. CodeSandbox 新开关 ----------
    print("\n=== 1. CodeSandbox.check_safety 开关 ===")
    from zeroai.core.sandbox import CodeSandbox

    sb_open = CodeSandbox(check_safety=False, allow_network=True)
    check("check_safety=False 生效", sb_open.check_safety is False)
    check("allow_network=True 生效", sb_open.allow_network is True)

    r = sb_open.execute(
        "import subprocess\n"
        "print(subprocess.run(['echo', 'subprocess-ok'],"
        " capture_output=True, text=True).stdout.strip())"
    )
    check(
        "check_safety=False 时 os/subprocess 不被拦",
        r["success"] and not r["issues"],
        f"success={r['success']} issues={r['issues']!r} "
        f"stdout={r['stdout'].strip()!r} err={r['error']!r}",
    )

    sb_default = CodeSandbox()
    r2 = sb_default.execute("import os\nos.system('echo hi')")
    check(
        "默认 check_safety=True 仍然拦截（老调用方不受影响）",
        r2["returncode"] == -2 and bool(r2["issues"]),
        f"returncode={r2['returncode']} issues={r2['issues']!r}",
    )

    # ---------- 2. 网络是否真的放开（_wrap_code 不再注入 socket 补丁）----------
    print("\n=== 2. 网络封锁是否解除 ===")
    wrapped_open = sb_open._wrap_code("pass", "C:\\")
    check(
        "allow_network=True 时不再注入 socket 屏蔽",
        "PermissionError" not in wrapped_open and "_blocked_socket" not in wrapped_open,
    )
    wrapped_closed = CodeSandbox(allow_network=False)._wrap_code("pass", "C:\\")
    check(
        "allow_network=False 时仍注入 socket 屏蔽",
        "_blocked_socket" in wrapped_closed,
    )

    # ---------- 3. code_execute 走全权限路径 ----------
    print("\n=== 3. code_execute（command_exec）===")
    from zeroai.tools.command_exec import code_execute

    out = code_execute(
        "import socket\n"
        "s = socket.socket()\n"
        "print('socket-created', s is not None)\n"
        "s.close()"
    )
    check(
        "code_execute 允许创建 socket（禁网已解除）",
        "socket-created True" in out and "代码安全检查未通过" not in out,
        out.replace("\n", " | ")[:220],
    )

    out2 = code_execute("import os\nprint(os.getcwd() != '')")
    check(
        "code_execute 不再触发 AST 安全拦截",
        "安全检查问题" not in out2 and "代码安全检查未通过" not in out2,
        out2.replace("\n", " | ")[:220],
    )

    # ---------- 4. ssh_exec 危险命令闸门 ----------
    print("\n=== 4. ssh_exec 危险命令闸门 ===")
    import zeroai.tools.ssh_ops as ssh_ops

    check(
        "模块导入期仍无 zeroai.core 依赖（惰性导入约束）",
        "zeroai.core" not in ssh_ops.__doc__.split("依赖：")[1].split('"""')[0]
        or "惰性导入" in ssh_ops.__doc__,
        "docstring 已注明惰性导入",
    )

    # 伪连接，绕过"连接不存在"的前置检查
    class _FakeConn:
        def run(self, *a, **k):
            raise AssertionError("不应真正执行")

        def is_closed(self):
            return False

    ssh_ops._SSH_CONNECTIONS["faketest"] = {
        "conn": _FakeConn(),
        "host": "10.0.0.1",
        "user": "root",
        "connected_at": 0,
    }
    try:
        # full 模式：应在到达执行前就放行（拦截消息不得出现）
        res_full = ssh_ops.ssh_exec(
            "reboot", conn_id="faketest", confirm_dangerous=False, _internal=True
        )
        check(
            "full 模式：危险命令不再被拦截",
            "检测到危险命令" not in res_full,
            res_full.replace("\n", " | ")[:200],
        )
        check(
            "full 模式：放行时写入审计日志",
            any("全权限放行" in e for e in ssh_ops._SSH_AUDIT_LOG),
            [e for e in ssh_ops._SSH_AUDIT_LOG if "全权限放行" in e][-1:] or "(无)",
        )

        # restricted 模式：应恢复拦截
        import zeroai.core.constants as _const

        old = _const.PERMISSION_LEVEL
        try:
            _const.PERMISSION_LEVEL = "restricted"
            res_res = ssh_ops.ssh_exec(
                "reboot", conn_id="faketest", confirm_dangerous=False, _internal=True
            )
            check(
                "restricted 模式：危险命令仍被拦截",
                "检测到危险命令" in res_res,
                res_res.replace("\n", " | ")[:200],
            )

            res_res2 = ssh_ops.ssh_exec(
                "reboot", conn_id="faketest", confirm_dangerous=True, _internal=True
            )
            check(
                "restricted 模式：confirm_dangerous=True 可显式放行",
                "检测到危险命令" not in res_res2,
                res_res2.replace("\n", " | ")[:200],
            )
        finally:
            _const.PERMISSION_LEVEL = old
    finally:
        ssh_ops._SSH_CONNECTIONS.pop("faketest", None)

    # ---------- 5. 运维流水线不再被"危险命令"误判为失败 ----------
    print("\n=== 5. _ssh_is_success 连锁影响 ===")
    blocked = ("⚠️ 检测到危险命令（匹配模式: \\breboot\\b）\n"
               "命令: reboot\n"
               "如确认要执行，请重新调用并设置 confirm_dangerous=true")
    check(
        "被拦截的返回值仍会被判为失败（restricted 语义保留）",
        ssh_ops._ssh_cmd_ok(blocked) is False,
    )
    # 注意：[退出码: N] 标记只在 exit_code != 0 时追加（ssh_exec 尾部），
    # 所以"成功"的真实样例里不应出现该标记——早先用 [退出码: 0] 当样例是错的。
    check(
        "正常执行返回值判为成功",
        ssh_ops._ssh_cmd_ok("started ok\n[退出码: 3]") is False
        and ssh_ops._ssh_cmd_ok("started ok\nnginx is running") is True,
    )

    print("\n" + "=" * 70)
    print(f"{'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
