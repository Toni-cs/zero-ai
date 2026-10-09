# -*- coding: utf-8 -*-
"""定位 ssh_ops 中所有 except 块，标出裸 pass 的静默吞异常点。"""
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TARGET = Path(r"D:\C\C\zeroai\tools\ssh_ops.py")


def enclosing_func(tree: ast.AST, node: ast.AST) -> str:
    """找出 node 所属的最内层函数名。"""
    best = "<module>"
    best_span = (-1, -1)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(n, "end_lineno", n.lineno)
            if n.lineno <= node.lineno <= end:
                span = end - n.lineno
                if best_span[0] == -1 or span < best_span[0]:
                    best, best_span = n.name, (span, end)
    return best


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()

    rows = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.ExceptHandler):
            continue
        body = n.body
        is_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
        is_doc_only = is_pass or (
            len(body) == 1
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        )
        exc = (
            ast.unparse(n.type)
            if n.type
            else "（裸 except:）"
        )
        rows.append(
            {
                "line": n.lineno,
                "func": enclosing_func(tree, n),
                "exc": exc,
                "silent": is_doc_only,
                "stmts": len(body),
                "first": lines[n.lineno - 1].strip()[:70],
            }
        )

    rows.sort(key=lambda r: r["line"])

    print(f"{'行':>5}  {'函数':<26} {'异常类型':<28} {'静默':<5} 首句")
    print("-" * 118)
    silent = [r for r in rows if r["silent"]]
    for r in rows:
        print(
            f"{r['line']:>5}  {r['func']:<26} {r['exc']:<28} "
            f"{'是' if r['silent'] else '否':<5} {r['first']}"
        )

    print()
    print(f"except 总数: {len(rows)}   静默吞异常(pass/docstring): {len(silent)}")
    print()
    print("=== 静默吞异常清单（本次要审的对象）===")
    for r in silent:
        print(f"  {r['line']:>5}  {r['func']:<26} {r['exc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
