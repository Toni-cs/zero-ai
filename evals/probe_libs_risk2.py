# -*- coding: utf-8 -*-
"""摸底 v2：sherpa_onnx 的真实依赖闭包 + 遮蔽风险精确判定 + 模型就位情况。

上一版在 .dist-info 目录上让 find_spec 抛异常，且 shadow 判定逻辑有误。
这版只看真正的包目录，并用"实际导入会解析到哪个文件"来判定遮蔽。
"""
from __future__ import annotations

import importlib
import importlib.util as iu
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"D:\C\C")
LIBS = ROOT / "libs"


def _is_pkg(p: pathlib.Path) -> bool:
    """真正的包目录：有 __init__.py 或是命名空间包。排除 .dist-info/_libs 等。"""
    if not p.is_dir():
        return False
    n = p.name
    if n.endswith((".dist-info", ".libs", ".data")) or n.startswith(("_", ".")):
        return False
    return (p / "__init__.py").is_file()


pkgs = sorted(p.name for p in LIBS.iterdir() if _is_pkg(p))
print("=" * 94)
print("1. libs/ 里真正的包（共 {} 个）".format(len(pkgs)))
print("=" * 94)
print("  " + ", ".join(pkgs))

print()
print("=" * 94)
print("2. 遮蔽风险：libs/ 置于 sys.path 首位时，会解析到哪")
print("=" * 94)
print(f"  {'包名':<22} {'site-packages 有':<17} {'置首后解析到':<14} 风险")
print("  " + "-" * 82)
risky = []
for name in pkgs:
    spec0 = iu.find_spec(name)
    in_sp = bool(spec0 and spec0.origin and "site-packages" in spec0.origin)
    sys.path.insert(0, str(LIBS))
    try:
        importlib.invalidate_caches()
        s = iu.find_spec(name)
        where = "libs/" if (s and s.origin and str(LIBS) in s.origin) else (
            "site-packages" if s else "?")
    except Exception as e:
        where = f"!{type(e).__name__}"
    finally:
        sys.path.remove(str(LIBS))
        importlib.invalidate_caches()
    risk = "!! 会遮蔽" if (in_sp and where == "libs/") else "安全"
    if risk.startswith("!!"):
        risky.append(name)
    print(f"  {name:<22} {'是' if in_sp else '否':<17} {where:<14} {risk}")

print(f"\n  会被遮蔽的包: {risky if risky else '无'}")

print()
print("=" * 94)
print("3. sherpa_onnx 的源码 import（判断它是否需要 libs/ 里其他包）")
print("=" * 94)
sp = LIBS / "sherpa_onnx"
imports = set()
for py in sorted(sp.glob("*.py")):
    for line in py.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")) and not s.startswith("from ."):
            imports.add(s)
print(f"  顶层 import 语句 {len(imports)} 条:")
for s in sorted(imports):
    print(f"      {s}")

print()
print("=" * 94)
print("4. 不把 libs/ 全塞进 sys.path，能否单独加载 sherpa_onnx")
print("=" * 94)
# 清掉任何 libs 影响
sys.path = [p for p in sys.path if not str(pathlib.Path(p)) == str(LIBS)]
try:
    import sherpa_onnx as _x
    print("  当前环境已能导入:", _x.__file__)
except Exception:
    import importlib.util
    init = sp / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "sherpa_onnx", init, submodule_search_locations=[str(sp)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sherpa_onnx"] = mod          # 关键：注册，让内部相对导入可用
    try:
        spec.loader.exec_module(mod)
        print(f"  独立加载成功: {mod.__file__}")
        syms = [n for n in dir(mod) if not n.startswith("_")]
        print(f"  符号数: {len(syms)}")
        for n in ("OfflineRecognizer", "OnlineRecognizer", "KeywordSpotter"):
            print(f"      {n:<22} {hasattr(mod, n)}")
    except Exception as e:
        print(f"  独立加载失败: {type(e).__name__}: {e}")
    finally:
        pass

print()
print("=" * 94)
print("5. SenseVoice 模型文件就位情况")
print("=" * 94)
import zeroai.tools.voice as voice
for attr in sorted(dir(voice)):
    if attr.startswith("_") and ("SENSE" in attr or "MODEL" in attr.upper()
                                 or "TOKENS" in attr.upper()):
        v = getattr(voice, attr)
        if isinstance(v, str):
            p = pathlib.Path(v)
            exists = p.is_file()
            size = p.stat().st_size if exists else 0
            print(f"  {attr:<30} {'存在' if exists else '缺失':<5} {size:>12,} B  {v}")
        else:
            print(f"  {attr:<30} {type(v).__name__}: {v!r}"[:130])
