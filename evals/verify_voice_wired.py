# -*- coding: utf-8 -*-
"""接通验收：get_asr_model() 能否在真实启动路径下成功构造识别器。

覆盖四个此前失败/不稳的场景：
  1. 干净环境（sys.path 无 libs/）下调用 get_asr_model() —— 此前 ModuleNotFoundError
  2. 工作目录不是项目根 —— 此前 _find_resource_dir 返回相对路径导致定位失败
  3. libs/ 追加后无遮蔽 —— anyio 等仍解析到 site-packages
  4. 语音工具仍在注册表中（模型看得到、调得到）
"""
from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
import textwrap

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"D:\C\C")
FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'通过' if ok else '失败'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


# ── 场景 1 + 3：干净环境调用 get_asr_model()，随后验证无遮蔽 ────────────
print("=" * 94)
print("场景 1/3：干净环境（sys.path 无 libs/）调用 get_asr_model()")
print("=" * 94)

# 强制剥离任何 libs/，模拟真实启动
sys.path = [p for p in sys.path if pathlib.Path(p).resolve() != (ROOT / "libs").resolve()]
sys.path.insert(0, str(ROOT))
importlib.invalidate_caches()

check("启动前 sys.path 不含 libs/", str(ROOT / "libs") not in sys.path)

# 记录关键模块的解析位置（追加 libs/ 之前的基线）
baseline = {}
for n in ("anyio", "httpx", "yaml", "asyncssh"):
    try:
        baseline[n] = str(importlib.import_module(n).__file__)
    except Exception as e:
        baseline[n] = f"<{type(e).__name__}>"
print("  基线解析位置:")
for n, v in baseline.items():
    print(f"      {n:<10} {v}")

print()
import zeroai.tools.voice as voice

check("voice 模块加载", True, voice.__file__)
check("调用前 sys.path 仍不含 libs/", str(ROOT / "libs") not in sys.path,
      "（_ensure_vendored_path 应是按需调用，不是 import 时就生效）")

t0 = __import__("time").perf_counter()
try:
    rec = voice.get_asr_model()
    dt = __import__("time").perf_counter() - t0
    check("get_asr_model() 成功", True, f"耗时 {dt:.2f}s, 类型={type(rec).__name__}")
except Exception as e:
    check("get_asr_model() 成功", False, f"{type(e).__name__}: {str(e)[:300]}")
    rec = None

check("调用后 sys.path 已追加 libs/", str(ROOT / "libs") in sys.path)
check("返回的是 OfflineRecognizer", rec is not None and type(rec).__name__ == "OfflineRecognizer",
      type(rec).__name__ if rec is not None else "None")

print()
print("  追加 libs/ 后的关键模块解析（必须与基线一致）:")
for n, v in baseline.items():
    now = str(importlib.import_module(n).__file__)
    check(f"{n} 未被遮蔽", now == v, now)

print()
print("=" * 94)
print("场景 2：工作目录不是项目根（此前 _find_resource_dir 返回相对路径）")
print("=" * 94)
probe = textwrap.dedent(r"""
    import os, sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.path.insert(0, r'D:\C\C')
    from zeroai.core.paths import _find_resource_dir, _ensure_vendored_path
    for sub in ('libs', 'models'):
        d = _find_resource_dir(sub)
        print(f'{sub}|{d}|{os.path.isabs(d)}|{os.path.isdir(d)}')
    p = _ensure_vendored_path()
    print(f'vendored|{p}|{p in sys.path}')
""")
tmp = pathlib.Path(os.environ.get("TEMP", ".")) / "probe_cwd.py"
tmp.write_text(probe, encoding="utf-8")

# 关键：从一个【不含】libs/、models/ 的目录启动
foreign_cwd = pathlib.Path(os.environ.get("TEMP", "."))
r = subprocess.run([sys.executable, str(tmp)], cwd=str(foreign_cwd),
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print(f"  工作目录 = {foreign_cwd}")
for line in (r.stdout or "").splitlines():
    print(f"      {line}")
    parts = line.split("|")
    if len(parts) == 4:
        sub, path, isabs, exists = parts
        check(f"_find_resource_dir('{sub}') 为绝对路径", isabs == "True", path)
        check(f"_find_resource_dir('{sub}') 指向真实目录", exists == "True", path)
    elif len(parts) == 3 and parts[0] == "vendored":
        check("_ensure_vendored_path() 生效", parts[2] == "True", parts[1])
if r.returncode != 0:
    check("从外部目录启动的探测", False, (r.stderr or "")[-400:])

print()
print("=" * 94)
print("场景 4：语音工具仍在注册表")
print("=" * 94)
from zeroai.tools.registry import TOOLS, TOOL_MAP
names = [t.get("function", {}).get("name") for t in TOOLS]
check("listen_asr 在 TOOLS", "listen_asr" in names)
check("listen_asr 在 TOOL_MAP", "listen_asr" in TOOL_MAP)
check("speak_tts 在 TOOL_MAP", "speak_tts" in TOOL_MAP)
# TOOL_MAP 存的是【可调用函数】，schema dict 在 TOOLS 里，不能对函数 .get()
check("TOOL_MAP['listen_asr'] 是可调用", callable(TOOL_MAP.get("listen_asr")),
      type(TOOL_MAP.get("listen_asr")).__name__)
desc = ""
for t in TOOLS:
    fn = t.get("function", {}) if isinstance(t, dict) else {}
    if fn.get("name") == "listen_asr":
        desc = fn.get("description", "")
        break
check("描述可被模型看到", len(desc) > 20, f"{len(desc)} 字符")

print()
print("=" * 94)
print("结果")
print("=" * 94)
if FAILS:
    print(f"  失败 {len(FAILS)} 项:")
    for f in FAILS:
        print(f"    - {f}")
    sys.exit(1)
print("  全部通过 —— 原生语音栈已接通。")
sys.exit(0)
