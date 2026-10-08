# -*- coding: utf-8 -*-
"""把改动前的 EXPERT_TEAM 关键词快照成 JSON，供变体实验复现旧基线。

为什么需要快照：
route_variants 直接从 constants.EXPERT_TEAM 读词表。一旦按 V6 改了
constants，V0（复刻旧算法）读到的就是新词表，「改动前」这个基线再也
无法复现，对比表会失真。

为什么用 AST 而不是 import：
constants.py 顶部有 `from .secrets import ...` 相对导入，脱离包上下文
无法加载；exec 整个模块又会执行不该执行的副作用。用 ast 只取
EXPERT_TEAM 这一个赋值节点，再 ast.literal_eval，既不执行代码也不受
导入影响（该节点只由 dict/list/str/int 字面量构成）。
"""
import ast
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evals", "results", "keywords_before.json")


def read_source():
    """优先从 git 历史取（可指定 revision），否则取本地备份。"""
    rev = sys.argv[1] if len(sys.argv) > 1 else None
    if rev:
        r = subprocess.run(["git", "show", "%s:zeroai/core/constants.py" % rev],
                           capture_output=True, cwd=ROOT)
        if r.returncode != 0:
            raise SystemExit("git show 失败: %s" % r.stderr.decode("utf-8", "replace"))
        return r.stdout.decode("utf-8"), "git:%s" % rev

    cands = sorted((d for d in os.listdir(ROOT) if d.startswith(".backup_")),
                   reverse=True)
    for d in cands:
        p = os.path.join(ROOT, d, "constants.py.bak")
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as f:
                return f.read(), p
    raise SystemExit("找不到改动前的 constants.py 备份")


def extract_expert_team(src):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "EXPERT_TEAM":
                    return ast.literal_eval(node.value)
    raise SystemExit("源码里没找到 EXPERT_TEAM 赋值")


def main():
    src, origin = read_source()
    team = extract_expert_team(src)
    snap = {k: list(v.get("keywords") or []) for k, v in team.items()}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    print("来源:", origin)
    print("快照 ->", OUT)
    for k, v in snap.items():
        print("  %-10s %2d  %s" % (k, len(v), " ".join(v[:6])))


if __name__ == "__main__":
    main()
