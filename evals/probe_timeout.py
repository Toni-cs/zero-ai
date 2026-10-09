# -*- coding: utf-8 -*-
"""定位 Windows + shell=True 下 timeout 的真实语义：
是没触发，还是触发了但 subprocess.run 在 communicate() 上被孙进程拖住。"""
from __future__ import annotations

import locale
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENC = locale.getpreferredencoding(False)
CMD = "ping -n 30 127.0.0.1"   # 自然耗时约 29 秒


def probe_run(timeout):
    t0 = time.time()
    try:
        subprocess.run(CMD, shell=True, capture_output=True, text=True,
                       timeout=timeout, encoding=ENC, errors="replace")
        return time.time() - t0, "正常返回(未超时)", None
    except subprocess.TimeoutExpired as e:
        return time.time() - t0, "TimeoutExpired", e


print("=" * 70)
print("A. subprocess.run(timeout=2) 真实行为")
print("=" * 70)
dt, kind, exc = probe_run(2)
print(f"  耗时 {dt:.1f}s  结果={kind}")
print(f"  => {'2 秒超时却被拖到 %.1f 秒后才返回' % dt if dt > 5 else '2 秒内返回，符合预期'}")
if exc is not None:
    print(f"  exc.stdout 长度={len(exc.stdout or '')}  exc.stderr 长度={len(exc.stderr or '')}")

print()
print("=" * 70)
print("B. Popen + 显式 taskkill /T /F 后能多快返回")
print("=" * 70)


def probe_popen(timeout):
    t0 = time.time()
    p = subprocess.Popen(CMD, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True,
                         encoding=ENC, errors="replace")
    try:
        p.communicate(timeout=timeout)
        return time.time() - t0, "正常返回", p.returncode
    except subprocess.TimeoutExpired:
        t_raise = time.time() - t0
        # 杀整棵进程树
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=10)
        except Exception as e:
            print(f"    taskkill 失败: {e}")
        try:
            p.communicate(timeout=5)
        except Exception:
            pass
        return time.time() - t0, f"超时在 {t_raise:.1f}s 触发", p.returncode


dt, kind, rc = probe_popen(2)
print(f"  总耗时 {dt:.1f}s  {kind}  rc={rc}")
print(f"  => {'被压回 5 秒内' if dt < 5 else '仍然被拖住'}")

print()
print("=" * 70)
print("C. 判定")
print("=" * 70)
print("  若 A=29s 而 B<5s：subprocess.run 的 timeout 只保证【检测】，")
print("  不保证【返回】——Windows 上它在 communicate() 等孙进程自然结束。")
