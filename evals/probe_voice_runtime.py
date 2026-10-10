# -*- coding: utf-8 -*-
"""验证 listen_asr 在真实启动路径下能否工作。

模拟 main.py 的 sys.path 设置（只插项目根），再问 voice 模块能否拿到 ASR 后端。
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 模拟 main.py:25 —— 只插入项目根，不插入 libs/
for p in list(sys.path):
    if p.endswith("libs"):
        sys.path.remove(p)
sys.path.insert(0, r"D:\C\C")

print("sys.path 中含 libs/ 的条目:",
      [p for p in sys.path if p.rstrip("\\/").endswith("libs")] or "无")

print()
try:
    import sherpa_onnx  # noqa: F401
    print("import sherpa_onnx: OK")
except Exception as e:
    print(f"import sherpa_onnx: FAIL  {type(e).__name__}: {e}")

print()
print("=" * 88)
print("真实调用 zeroai.tools.voice 的内部初始化函数")
print("=" * 88)
try:
    import zeroai.tools.voice as voice
    print(f"voice 模块加载 OK ({voice.__file__})")
    # 找出真正做 sherpa 初始化的函数
    cands = [n for n in dir(voice) if n.startswith("_get") or "recognizer" in n.lower()
             or "asr" in n.lower()]
    print("相关成员:", cands[:10])
    for name in cands:
        obj = getattr(voice, name)
        if callable(obj):
            try:
                obj()
                print(f"  {name}(): OK —— ASR 后端可用")
            except Exception as e:
                print(f"  {name}(): {type(e).__name__}: {str(e)[:110]}")
except Exception as e:
    print(f"voice 模块加载失败: {type(e).__name__}: {str(e)[:200]}")

print()
print("=" * 88)
print("registry 里 listen_asr 是不是已注册（即模型会不会调用它）")
print("=" * 88)
try:
    from zeroai.tools.registry import TOOLS, TOOL_MAP
    names = [t.get("function", {}).get("name") for t in TOOLS]
    print("listen_asr 在 TOOLS 中:", "listen_asr" in names)
    print("listen_asr 在 TOOL_MAP 中:", "listen_asr" in TOOL_MAP)
    print("speak_tts   在 TOOL_MAP 中:", "speak_tts" in TOOL_MAP)
except Exception as e:
    print("registry 检查失败:", e)
