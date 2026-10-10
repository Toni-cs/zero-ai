# -*- coding: utf-8 -*-
"""接通前摸底：libs/ 里有什么、shadow 风险、模型是否就位、wheel 是否带得动。"""
from __future__ import annotations

import importlib.util as iu
import pathlib
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"D:\C\C")
LIBS = ROOT / "libs"

print("=" * 94)
print("1. libs/ 顶层内容（判断 sys.path 污染风险）")
print("=" * 94)
for p in sorted(LIBS.iterdir()):
    kind = "目录" if p.is_dir() else f"{p.stat().st_size:>12,} B"
    print(f"  {p.name:<34} {kind}")

print()
print("=" * 94)
print("2. shadow 风险：libs/ 里的包，是否也 pip 装了？")
print("=" * 94)
print(f"  {'包名':<22} {'libs/ 里':<10} {'已 pip 装':<12} 判定")
print("  " + "-" * 74)
cands = [p.name for p in LIBS.iterdir()
         if p.is_dir() and not p.name.startswith(("_", "."))]
for name in sorted(cands):
    in_libs = (LIBS / name).is_dir()
    installed = iu.find_spec(name) is not None
    # 模拟把 libs/ 插到最前，看导入会解析到哪
    sys.path.insert(0, str(LIBS))
    try:
        iu.invalidate_caches()
        spec = iu.find_spec(name)
        resolved = pathlib.Path(spec.origin).parent.name if spec and spec.origin else "?"
    except Exception:
        resolved = "?"
    finally:
        sys.path.remove(str(LIBS))
    risk = ("!! 会遮蔽已装版本" if (in_libs and installed and resolved == name)
            else "安全（未安装，libs 提供）" if in_libs and not installed
            else "libs 无此包")
    print(f"  {name:<22} {'是' if in_libs else '否':<10} "
          f"{'是' if installed else '否':<12} {risk}")
    del resolved

print()
print("=" * 94)
print("3. sherpa_onnx 包结构（能否被 importlib 正确加载）")
print("=" * 94)
sp = LIBS / "sherpa_onnx"
print(f"  路径         : {sp}")
print(f"  __init__.py  : {(sp / '__init__.py').is_file()}")
pyd = sorted(sp.rglob("*.pyd"))
dll = sorted(sp.rglob("*.dll"))
print(f"  .pyd 数量    : {len(pyd)}")
for f in pyd:
    print(f"      {f.name:<44} {f.stat().st_size:>12,} B")
print(f"  .dll 数量    : {len(dll)}")
for f in dll:
    print(f"      {f.name:<44} {f.stat().st_size:>12,} B")
print(f"  Python 版本匹配: cp312 vs 当前 {sys.version_info.major}{sys.version_info.minor}"
      f"  -> {'匹配' if f'cp{sys.version_info.major}{sys.version_info.minor}' in ''.join(x.name for x in pyd) else '不匹配'}")

print()
print("=" * 94)
print("4. SenseVoice 模型是否就位")
print("=" * 94)
import zeroai.tools.voice as voice
for attr in dir(voice):
    if attr.startswith("_SENSE") or "MODEL" in attr.upper():
        v = getattr(voice, attr)
        if isinstance(v, str):
            exists = pathlib.Path(v).is_file()
            size = pathlib.Path(v).stat().st_size if exists else 0
            print(f"  {attr:<28} {'存在' if exists else '缺失':<6} {size:>12,} B  {v}")
        else:
            print(f"  {attr:<28} {type(v).__name__}: {v!r}"[:120])

print()
print("=" * 94)
print("5. wheel 里带不带 libs/sherpa_onnx")
print("=" * 94)
wheels = sorted(ROOT.glob("dist/*.whl")) + sorted(ROOT.glob("*.whl"))
if wheels:
    w = wheels[-1]
    print(f"  最新 wheel: {w.name}")
    with zipfile.ZipFile(w) as z:
        names = z.namelist()
        print(f"  条目总数  : {len(names)}")
        hits = [n for n in names if "sherpa" in n.lower() or n.startswith("libs/")]
        print(f"  含 libs/ 或 sherpa 的条目: {len(hits)}")
        for h in hits[:12]:
            print(f"      {h}")
        pyd_in = [n for n in names if n.endswith((".pyd", ".dll", ".so"))]
        print(f"  二进制产物 (.pyd/.dll/.so): {len(pyd_in)}")
else:
    print("  未找到 wheel")
