# -*- coding: utf-8 -*-
"""判定 libs/ 应该【追加到末尾】还是【逐包 importlib 装载】。

判定标准（严格）：
  A. 追加后，现有可导入的模块必须仍解析到原位置 —— 严格增量、零遮蔽
  B. sherpa_onnx 必须能导入（主路径）
  C. faster_whisper 必须能导入（回退路径；它依赖 av，而 av 未 pip 安装）

若 A/B/C 全成立，追加方案胜出：一行代码，且依赖闭包（av/ctranslate2/onnxruntime）
自动可解，不必逐包装载。
"""
from __future__ import annotations

import importlib
import importlib.util as iu
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"D:\C\C")
LIBS = ROOT / "libs"
PROBE = ["anyio", "httpx", "yaml", "asyncssh", "textual", "numpy", "openai",
         "zeroai", "zeroai.core.paths", "zeroai.tools.registry"]

print("=" * 94)
print("A. 基线：追加前各模块解析到哪")
print("=" * 94)
before = {}
for name in PROBE:
    try:
        m = importlib.import_module(name)
        before[name] = str(m.__file__)
        print(f"  {name:<22} {before[name]}")
    except Exception as e:
        before[name] = f"<{type(e).__name__}>"
        print(f"  {name:<22} FAIL {type(e).__name__}: {e}")

print()
print("libs/ 当前在 sys.path 中:", str(LIBS) in sys.path)

# ── 关键动作：追加（append）到末尾，不插首位 ────────────────────────────
sys.path.append(str(LIBS))
importlib.invalidate_caches()

print()
print("=" * 94)
print("A2. 追加后：必须仍解析到原位置（严格增量、零遮蔽）")
print("=" * 94)
regress = []
for name in PROBE:
    try:
        m = importlib.import_module(name)
        now = str(m.__file__)
        same = now == before[name]
        if not same:
            regress.append(name)
        flag = "一致" if same else "!! 变了"
        print(f"  {name:<22} {flag:<6} {now}")
    except Exception as e:
        regress.append(name)
        print(f"  {name:<22} !! FAIL {type(e).__name__}: {e}")

print(f"\n  发生回归的模块: {regress if regress else '无'}")
a_ok = not regress

print()
print("=" * 94)
print("B. 主路径 sherpa_onnx")
print("=" * 94)
try:
    sh = importlib.import_module("sherpa_onnx")
    print(f"  OK  {sh.__file__}")
    for n in ("OfflineRecognizer", "OnlineRecognizer", "KeywordSpotter"):
        print(f"      {n:<20} {hasattr(sh, n)}")
    b_ok = True
except Exception as e:
    print(f"  FAIL {type(e).__name__}: {e}")
    b_ok = False

print()
print("=" * 94)
print("C. 回退路径 faster_whisper（依赖 av / ctranslate2，均未 pip 安装）")
print("=" * 94)
c_ok = False
try:
    fw = importlib.import_module("faster_whisper")
    print(f"  OK  {fw.__file__}")
    from faster_whisper import WhisperModel
    print(f"      WhisperModel 可用: {WhisperModel is not None}")
    c_ok = True
except Exception as e:
    print(f"  FAIL {type(e).__name__}: {str(e)[:300]}")

# av 单独看一眼，因为它是 faster_whisper 的关键原生依赖
print()
try:
    av = importlib.import_module("av")
    print(f"  av: OK  {av.__file__}  version={getattr(av, '__version__', '?')}")
except Exception as e:
    print(f"  av: FAIL {type(e).__name__}: {str(e)[:200]}")

print()
print("=" * 94)
print("判定")
print("=" * 94)
print(f"  A 零遮蔽/零回归 : {'成立' if a_ok else '不成立'}")
print(f"  B sherpa_onnx   : {'成立' if b_ok else '不成立'}")
print(f"  C faster_whisper: {'成立' if c_ok else '不成立'}")
if a_ok and b_ok and c_ok:
    print("\n  [结论] 追加方案成立：一行代码，主路径与回退路径同时打通，零遮蔽。")
    sys.exit(0)
if a_ok and b_ok and not c_ok:
    print("\n  [结论] 追加方案只解决主路径，回退路径仍不通 —— 需要单独处置。")
    sys.exit(2)
print("\n  [结论] 追加方案破坏了现有导入，不可用，必须走逐包装载。")
sys.exit(1)
