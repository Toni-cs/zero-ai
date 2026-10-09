# -*- coding: utf-8 -*-
"""Skills + Formatters 冒烟测试（人工核验用，非 pytest）"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core.skills import (  # noqa: E402
    clear_cache, discover_skills, get_skill, inject_catalog,
    render_catalog, skill_list, skill_load, find_relevant,
)
from zeroai.core.formatters import (  # noqa: E402
    detect, format_code, format_on_write, formatting_enabled, formatter_for,
)

print("=== 1. Skills 发现 ===")
clear_cache()
sks = discover_skills()
for s in sks:
    print(f"  - {s.name} [{s.source}] desc={s.summary[:40]!r} triggers={s.triggers}")
print(f"  合计 {len(sks)} 个")

print("\n=== 2. skill_list / skill_load ===")
print(skill_list())
print("---")
print(skill_load("git-commit")[:160].replace("\n", " | "))
print("--- 不存在的技能 ---")
print(skill_load("nope")[:100])

print("\n=== 3. 目录渲染与注入 ===")
cat = render_catalog()
print(cat)
inj = inject_catalog("BASE PROMPT")
print("--- 注入后 ---")
print(inj)
print("--- 重复注入应幂等 ---")
inj2 = inject_catalog(inj)
print("幂等:", inj2 == inj)

print("\n=== 4. 相关性匹配 ===")
for q in ("帮我写个提交信息", "这段 Python 规范吗", "今天天气怎么样"):
    hits = find_relevant(q)
    print(f"  {q!r} -> {[h.name for h in hits]}")

print("\n=== 5. Formatters 检测 ===")
avail = detect()
print("  可用格式化器:", list(avail) or "**无（本机未装 ruff/black）**")
print("  formatting_enabled():", formatting_enabled())
print("  .py ->", formatter_for("a.py"))
print("  .bin ->", formatter_for("a.bin"))
print("  无扩展名 ->", formatter_for("Makefile"))

print("\n=== 6. format_code 三态 ===")
code = "x=1\ndef f( a,b ):\n  return a+b\n"
out, name, err = format_code(code, "t.py")
print(f"  无格式化器: changed={out!=code} name={name} err={err}")
print(f"  返回的是原对象: {out == code}")

print("\n=== 7. write_file + 格式化钩子 ===")
tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "demo.py")
from zeroai.tools.file_manager import write_file, edit_file  # noqa: E402
r = write_file(p, "x=1\n")
print("  write_file ->", r)
r2 = edit_file(p, operation="append", content="y=2")
print("  edit_file  ->", repr(r2[:60]))
print("  文件内容   ->", repr(open(p, encoding="utf-8").read()))

print("\n=== 8. format_on_write 绝不抛异常 ===")
for target in (os.path.join(tmp, "nope.py"), tmp, "\\invalid\\path"):
    try:
        res = format_on_write(target)
        print(f"  {target!r} -> {res!r}  (未抛异常)")
    except Exception as e:
        print(f"  {target!r} -> **抛异常了** {e}")

print("\n全部冒烟通过")
