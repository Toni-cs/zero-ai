# -*- coding: utf-8 -*-
"""MCP 现状取证：模块规模 + 是否接进 CLI/注册表/配置 + 开关默认值。"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\C\C")

print("=" * 78)
print("1. zeroai/mcp/ 模块规模")
print("=" * 78)
tot = 0
for p in sorted((ROOT / "zeroai" / "mcp").glob("*.py")):
    n = len(p.read_text(encoding="utf-8").splitlines())
    tot += n
    print(f"  {p.name:<16} {n:>4} 行")
print(f"  {'合计':<16} {tot:>4} 行")

print()
print("=" * 78)
print("2. 开关默认值与接线点")
print("=" * 78)

# agent.py 的 use_mcp 默认值
agent = (ROOT / "zeroai" / "core" / "agent.py").read_text(encoding="utf-8")
for m in re.finditer(r"^(?P<i>\s*)use_mcp\s*:\s*bool\s*=\s*(?P<v>\w+)", agent, re.M):
    line = agent[: m.start()].count("\n") + 1
    print(f"  agent.py:{line}  use_mcp 默认 = {m.group('v')}")

# CLI 是否暴露开关
print()
print("  CLI/配置层对 MCP 的暴露：")
for rel in ["zeroai/cli.py", "zeroai/core/config.py", "zeroai/config.yaml",
            "zeroai/core/constants.py"]:
    p = ROOT / rel
    if not p.exists():
        print(f"    {rel:<32} (不存在)")
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    hits = [f"L{i}: {l.strip()[:90]}"
            for i, l in enumerate(txt.splitlines(), 1)
            if re.search(r"use_mcp|mcp_config|mcpServers|--mcp", l)]
    if hits:
        print(f"    {rel:<32} {len(hits)} 处")
        for h in hits[:6]:
            print(f"        {h}")
    else:
        print(f"    {rel:<32} 0 处")

print()
print("=" * 78)
print("3. registry 里 MCP 工具如何进入 TOOL_MAP")
print("=" * 78)
reg = (ROOT / "zeroai" / "tools" / "registry.py").read_text(encoding="utf-8")
for i, l in enumerate(reg.splitlines(), 1):
    if re.search(r"mcp|MCP", l):
        print(f"  registry.py:{i}: {l.strip()[:110]}")

print()
print("=" * 78)
print("4. server.py 暴露了什么（作为 MCP 服务端的能力清单）")
print("=" * 78)
srv = ROOT / "zeroai" / "mcp" / "server.py"
if srv.exists():
    tree = ast.parse(srv.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node) or ""
            first = d.strip().splitlines()[0] if d.strip() else ""
            print(f"  {'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}"
                  f"def {node.name}()  {first[:60]}")
        elif isinstance(node, ast.ClassDef):
            d = ast.get_docstring(node) or ""
            first = d.strip().splitlines()[0] if d.strip() else ""
            print(f"  class {node.name}  {first[:60]}")

print()
print("=" * 78)
print("5. CLI 入口：python -m zeroai.mcp 是否可用")
print("=" * 78)
main = ROOT / "zeroai" / "mcp" / "__main__.py"
if main.exists():
    txt = main.read_text(encoding="utf-8")
    print(f"  __main__.py {len(txt.splitlines())} 行")
    print("  首 20 行：")
    for i, l in enumerate(txt.splitlines()[:20], 1):
        print(f"    {i:>2}| {l[:96]}")
