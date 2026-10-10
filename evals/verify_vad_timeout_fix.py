# -*- coding: utf-8 -*-
"""验证 VAD 前置等待修复：静音环境下 listen_asr 应等约 3 秒才返回。

修复前：max_pre_wait=46 x sleep(0.03) = 1.38s（+ 开销 ≈ 2.7s 实测）
修复后：直接计时 PRE_WAIT_SECONDS = 3.0s（+ 同样的开销 ≈ 4.3s 实测）

本机实测环境底噪 RMS ≈ 0.0009 < 阈值 0.02，不会误触发语音检测。
"""
from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\C\C")

from zeroai.core.paths import _ensure_vendored_path  # noqa: E402

_ensure_vendored_path()

print("=" * 78)
print("VAD 前置等待修复验证")
print("=" * 78)

# ── 1. 源码级断言：缺陷写法已移除，新常量已就位 ──
print("\n[1] 源码结构检查")
import pathlib

src = pathlib.Path(r"D:\C\C\zeroai\tools\voice.py").read_text(encoding="utf-8")

CHECKS = [
    ("PRE_WAIT_SECONDS = 3.0 存在", "PRE_WAIT_SECONDS = 3.0" in src),
    ("按块计算的缺陷写法已移除（仅查可执行行，跳过作为修复说明保留的注释）",
     all("max_pre_wait = int(" not in ln and "pre_wait_count += 1" not in ln
         for ln in src.splitlines()
         if not ln.lstrip().startswith("#"))),
    ("改用实际计时", "time.time() - start > PRE_WAIT_SECONDS" in src),
]
ok_all = True
for name, ok in CHECKS:
    print(f"  {'✅' if ok else '❌'} {name}")
    ok_all = ok_all and ok

# ── 2. 行为级断言：静音时应等约 3 秒 ──
print("\n[2] 行为检查（无语音，listen_asr 应在 ~3s 后返回）")
from zeroai.tools.voice import listen_asr  # noqa: E402

t0 = time.time()
try:
    text = listen_asr(max_seconds=10, silence_seconds=1.0)
    dt = time.time() - t0
except Exception as e:
    print(f"  ❌ 调用异常: {type(e).__name__}: {e}")
    sys.exit(1)

print(f"  返回: {text}")
print(f"  耗时: {dt:.2f}s")

# 期望：PRE_WAIT(3.0) + 噪声采样(0.3) + 开销 ≈ 3.4~4.5s
# 修复前：1.38 + 0.3 + 开销 ≈ 2.5~3.0s
TIMEOUT_OK = 2.5 <= dt <= 6.0
NEAR_3S = 3.0 <= dt <= 5.0

if text != "（未录到声音）":
    print("  ⚠️ 未返回超时文案（可能环境中有语音/噪声超过阈值），跳过计时断言")
    time_ok = None
elif NEAR_3S:
    print(f"  ✅ 在 3.0~5.0s 区间返回（{dt:.2f}s），符合 PRE_WAIT_SECONDS=3.0")
    time_ok = True
elif TIMEOUT_OK:
    print(f"  ⚠️ {dt:.2f}s 不在 3.0~5.0s 区间，请人工确认")
    time_ok = False
else:
    print(f"  ❌ {dt:.2f}s 明显异常")
    time_ok = False

print("\n" + "=" * 78)
print("判定")
print("=" * 78)
if ok_all and time_ok is True:
    print("  ✅ 通过：结构与行为均已修复")
    rc = 0
elif ok_all and time_ok is None:
    print("  ⚠️ 结构通过，行为断言被跳过（环境有声）")
    rc = 2
else:
    print(f"  ❌ 未通过  结构={ok_all}  行为={time_ok}")
    rc = 1
sys.exit(rc)
