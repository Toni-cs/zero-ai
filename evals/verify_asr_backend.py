# -*- coding: utf-8 -*-
"""决定性验证：不碰 sys.path，能否用真模型构造出 ASR 识别器。

如果这步成功，那么"接通原生语音栈"就是一条纯粹的接线工作，没有隐藏障碍。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"D:\C\C")
SP = ROOT / "libs" / "sherpa_onnx"

# 保证环境干净：确保 sys.path 里没有 libs/
sys.path = [p for p in sys.path if pathlib.Path(p).resolve() != (ROOT / "libs").resolve()]
sys.path.insert(0, str(ROOT))

print("=" * 94)
print("1. 加载 sherpa_onnx（按路径独立加载，不污染 sys.path）")
print("=" * 94)
spec = importlib.util.spec_from_file_location(
    "sherpa_onnx", SP / "__init__.py", submodule_search_locations=[str(SP)])
mod = importlib.util.module_from_spec(spec)
sys.modules["sherpa_onnx"] = mod
t0 = time.perf_counter()
spec.loader.exec_module(mod)
print(f"  加载成功 {time.perf_counter() - t0:.2f}s  -> {mod.__file__}")
print(f"  确认 sys.path 未被污染: libs/ 在 sys.path 中 = "
      f"{str(ROOT / 'libs') in sys.path}")

print()
print("=" * 94)
print("2. 确认 anyio 等关键包仍解析到 site-packages（遮蔽未发生）")
print("=" * 94)
import importlib
importlib.invalidate_caches()
for name in ("anyio", "httpx", "yaml", "asyncssh"):
    try:
        m = importlib.import_module(name)
        loc = getattr(m, "__file__", "?")
        ok = "libs" not in str(loc)
        print(f"  {name:<10} {'OK 未被遮蔽' if ok else '!! 被 libs 遮蔽'}  {loc}")
    except Exception as e:
        print(f"  {name:<10} FAIL {type(e).__name__}: {e}")

print()
print("=" * 94)
print("3. 用真实模型构造 OfflineRecognizer（SenseVoice int8, 239MB）")
print("=" * 94)
model = str(ROOT / "models" / "sense-voice" / "model.int8.onnx")
tokens = str(ROOT / "models" / "sense-voice" / "tokens.txt")
print(f"  model : {model}")
print(f"        存在={pathlib.Path(model).is_file()}  "
      f"{pathlib.Path(model).stat().st_size:,} B")
print(f"  tokens: {tokens}")
print(f"        存在={pathlib.Path(tokens).is_file()}")
print()
t0 = time.perf_counter()
try:
    rec = mod.OfflineRecognizer.from_sense_voice(
        model=model, tokens=tokens, num_threads=2, use_itn=True)
    dt = time.perf_counter() - t0
    print(f"  [成功] 识别器构造完成，耗时 {dt:.2f}s")
    print(f"  类型: {type(rec)}")
    methods = [n for n in dir(rec) if not n.startswith("_")]
    print(f"  可用方法: {methods[:12]}")
    ok = True
except Exception as e:
    dt = time.perf_counter() - t0
    print(f"  [失败] {type(e).__name__}（耗时 {dt:.2f}s）")
    print(f"  {str(e)[:600]}")
    ok = False

print()
print("=" * 94)
print("结论")
print("=" * 94)
if ok:
    print("  ASR 后端完全可用，缺的只是一条 import 路径。接通即可。")
else:
    print("  构造失败，接线之外还有别的障碍，需要继续排查。")
sys.exit(0 if ok else 1)
