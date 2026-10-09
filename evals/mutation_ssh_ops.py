# -*- coding: utf-8 -*-
"""变异测试：故意破坏 ssh_ops 的被测行为，确认测试会失败。

"全绿"不等于有效——如果断言是永真的，破坏代码它照样绿。
这里逐条打补丁 → 跑测试 → 必须看到 failed → 立即还原源码。
脚本用 try/finally 保证源码一定被还原，结束时校验 sha 与原始一致。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(r"D:\C\C\zeroai\tools\ssh_ops.py")
TESTS = Path(r"D:\C\C\tests\test_ssh_ops.py")

MUTATIONS = [
    # (标签, 被替换的原文, 替换成, 期望哪个测试类会挂)
    ("M1 退出码无条件追加",
     "        if exit_code != 0:\n            parts.append(f\"[退出码: {exit_code}]\")",
     "        if True:\n            parts.append(f\"[退出码: {exit_code}]\")",
     "TestSshExecExitCode"),

    ("M2 stdout 不再截断",
     "        if len(stdout) > 8000:",
     "        if False and len(stdout) > 8000:",
     "TestSshExecTruncation"),

    ("M3 危险命令不再受 restricted 拦截",
     '    if is_dangerous and PERMISSION_LEVEL != "full" and not confirm_dangerous:',
     "    if False:",
     "TestSshExecDangerousGating"),

    ("M4 审计日志不再脱敏 IP",
     '        safe_host = f"{parts[0]}.{parts[1]}.***.***"',
     "        safe_host = host",
     "TestAuditLog"),

    ("M5 _ssh_cmd_ok 一律判成功",
     "    if not result:\n        return False",
     "    if not result:\n        return True\n    if True:\n        return True",
     "TestSshCmdOk"),

    ("M6 firewall 忽略 protocol 校验",
     '        return "错误：protocol 必须是 tcp 或 udp"',
     "        pass  # mutated: 不再校验",
     "TestOpsValidationShortCircuit"),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run(label: str = "") -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", str(TESTS), "-q", "--no-header"]
    if label:                      # -k 空串会报错，只在有目标时才加
        cmd += ["-k", label]
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(SRC.parents[1]),
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out


def main() -> int:
    before = _sha(SRC)
    original = SRC.read_text(encoding="utf-8")
    results = []
    try:
        for label, old, new, target in MUTATIONS:
            if old not in original:
                results.append((label, target, "锚点未找到", "SKIP"))
                continue
            mutated = original.replace(old, new, 1)
            SRC.write_bytes(mutated.encode("utf-8"))
            code, out = _run(target)
            tail = [l for l in out.splitlines() if "passed" in l or "failed" in l
                    or "error" in l]
            summary = tail[-1] if tail else out.strip()[-120:]
            verdict = "有牙齿" if code != 0 else "!! 没咬住"
            results.append((label, target, summary.strip(), verdict))
    finally:
        # 必须写 bytes：Path.write_text 在 Windows 下默认翻译 \n -> \r\n，
        # 会把整份 LF 源码变成 CRLF（本项目已因此踩过坑）。
        SRC.write_bytes(original.encode("utf-8"))

    after = _sha(SRC)
    print("=" * 96)
    print("变异测试：破坏被测行为后，对应测试是否报警")
    print("=" * 96)
    for label, target, summary, verdict in results:
        print(f"  [{verdict}] {label}")
        print(f"           目标类: {target}")
        print(f"           结果  : {summary}")
    print()
    print(f"源码 sha 还原校验: {'一致 ✓' if before == after else '!! 不一致'}")
    print(f"  before = {before[:16]}")
    print(f"  after  = {after[:16]}")

    # 还原后再跑一次全量，确认干净状态仍是绿的
    code, out = _run("")  # -k "" 匹配全部
    tail = [l for l in out.splitlines() if "passed" in l or "failed" in l]
    print(f"还原后全量: {tail[-1].strip() if tail else out[-120:]}")
    return 0 if before == after and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
