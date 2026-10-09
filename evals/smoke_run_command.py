# -*- coding: utf-8 -*-
"""run_command 对齐 OpenCode bash 后的行为冒烟（5 项缺口逐项验收）。"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from zeroai.tools.command_exec import MAX_CAPTURE_BYTES, MAX_TIMEOUT_S, run_command

print("=" * 76)
print(f"常量: MAX_CAPTURE_BYTES={MAX_CAPTURE_BYTES} ({MAX_CAPTURE_BYTES/1024/1024:.0f}MB)  "
      f"MAX_TIMEOUT_S={MAX_TIMEOUT_S}")
print("=" * 76)

cases = []


def check(name, ok, detail=""):
    cases.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


# 1. 退出码恒返回
print("\n[1] 退出码")
out = run_command("echo hello", skip_translate=True)
check("成功命令带 [退出码: 0]", "[退出码: 0]" in out, out.splitlines()[0])
check("成功命令保留原输出", "hello" in out, out.replace("\n", "\\n"))

out = run_command("cmd /c exit 7", skip_translate=True)
check("非零退出码如实返回", "[退出码: 7]" in out, out.splitlines()[0])

# 2. workdir
print("\n[2] workdir")
out = run_command("cd", workdir=r"C:\Windows", skip_translate=True)
check("指定目录生效", "windows" in out.lower(), out.strip()[:80])

out = run_command("echo x", workdir=r"C:\no_such_dir_zzz", skip_translate=True)
check("不存在的目录报错", "工作目录不存在" in out, out.strip()[:90])

# 相对路径解析
out = run_command("cd", workdir=".", skip_translate=True)
check("相对路径可解析", "[退出码: 0]" in out, out.strip()[:60])

# 3. timeout
print("\n[3] timeout")
import time
t0 = time.time()
out = run_command("ping -n 30 127.0.0.1", timeout=2, skip_translate=True)
dt = time.time() - t0
check("timeout=2 生效且未跑满30s", "命令超时" in out and dt < 10, f"{dt:.1f}s")
check("超时提示含调大指引", "调大 timeout" in out, out.strip().splitlines()[-1][:70])
check("超时提示含上限", str(MAX_TIMEOUT_S) in out, "")

t0 = time.time()
out = run_command("cmd /c exit 3", timeout=99999, skip_translate=True)
dt = time.time() - t0
check(f"timeout 上限截到 {MAX_TIMEOUT_S}s（不被 99999 卡住）", "[退出码: 3]" in out and dt < 3, f"{dt:.1f}s")

# 4. 截断标记
print("\n[4] 截断标记")
out = run_command("python -c \"print('A'*5000)\"", skip_translate=True)
check("未超限时不带截断标记", "输出已截断" not in out and "[退出码: 0]" in out, f"len={len(out)}")

big = run_command("python -c \"print('B'*%d)\"" % (MAX_CAPTURE_BYTES + 5000),
                  skip_translate=True)
check("超过 1MB 触发截断", "输出已截断" in big, "")
check("截断标记给出总长度", "共 %d 字符" % (MAX_CAPTURE_BYTES + 5000) in big,
      [l for l in big.splitlines() if "截断" in l][0][:70] if "截断" in big else "")
check("截断标记给出已返回长度", str(MAX_CAPTURE_BYTES) in big, "")
check("截断后不含尾部 B 超量", big.rstrip().endswith("B") and len(big) < MAX_CAPTURE_BYTES + 600,
      f"返回总长={len(big)}")

# 5. 空输出
print("\n[5] 边界")
out = run_command("cmd /c \"exit 0\"", skip_translate=True)
check("空输出有占位", "(无输出)" in out, out.strip().replace("\n", " | "))

# 6. 向后兼容：老调用方形态
print("\n[6] 向后兼容")
out = run_command("echo compat")
check("run_command(cmd) 单参可调", "[退出码: 0]" in out, out.strip()[:50])
out = run_command("echo compat", skip_translate=True)
check("run_command(cmd, skip_translate=True) 关键字可调", "[退出码: 0]" in out, "")

# 7. 高危拦截仍生效
print("\n[7] 高危命令闸门未回退")
out = run_command("format C:", skip_translate=True)
check("受限模式拦截文案保留", "已拦截高危命令" in out or "[退出码" in out, out.strip()[:60])

print()
print("=" * 76)
n_ok = sum(1 for _, ok, _ in cases if ok)
print(f"冒烟结果: {n_ok}/{len(cases)} PASS")
for name, ok, detail in cases:
    if not ok:
        print(f"  FAIL: {name}  {detail}")
print("=" * 76)
raise SystemExit(0 if n_ok == len(cases) else 1)
