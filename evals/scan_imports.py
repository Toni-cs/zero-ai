# -*- coding: utf-8 -*-
"""扫描 zeroai/ 下所有第三方 import，与 pyproject 已声明依赖做差集。

用法：
    python evals/scan_imports.py
输出：
    evals/results/imports.json
    evals/results/imports.md
"""
import ast
import io
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "zeroai")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")

# 标准库（Python 3.10+），粗略但足够：以 sys.stdlib_module_names 为准
STDLIB = set(getattr(sys, "stdlib_module_names", set()))

# 本包内部模块
LOCAL = {"zeroai"}

# 同仓库内的子项目（有各自的 setup.py / requirements.txt），不是 PyPI 依赖
LOCAL_SUBPROJECTS = {"zeroai_tui", "zeroai_proxy"}


def iter_py(base):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def top_level(name):
    return name.split(".")[0]


def scan_imports():
    found = defaultdict(set)   # module -> {files}
    for path in iter_py(PKG):
        rel = os.path.relpath(path, ROOT)
        try:
            src = io.open(path, encoding="utf-8").read()
        except Exception:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    found[top_level(a.name)].add(rel)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:   # 相对导入 = 本包
                    continue
                if node.module:
                    found[top_level(node.module)].add(rel)
    return found


def parse_declared():
    """用 tomllib 精确解析 pyproject 的 dependencies 与各 optional 组。

    此前用正则抓所有引号字面量，会把 classifiers 里的
    "Apache-2.0"、"Programming :: ..." 误判为依赖。
    """
    if not os.path.exists(PYPROJECT):
        return set(), {}
    import tomllib
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    groups = defaultdict(set)
    declared = set()

    def add_list(dst, items):
        for lit in items or []:
            name = re.split(r"[\[<>=!~; ]", str(lit).strip())[0]
            if name:
                dst.add(name)
                declared.add(name)

    proj = data.get("project", {})
    add_list(groups["project"], proj.get("dependencies"))
    for gname, items in (proj.get("optional-dependencies") or {}).items():
        add_list(groups["optional:" + gname], items)

    return declared, groups


# import 名 -> PyPI 发行名 的已知映射
DIST_NAME = {
    "faiss": "faiss-cpu",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
    "gi": "PyGObject",
    "wx": "wxPython",
    "sklearn": "scikit-learn",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "jieba": "jieba",
    "edge_tts": "edge-tts",
    "send2trash": "send2trash",
    "uiautomation": "uiautomation",
    "sounddevice": "sounddevice",
    "pygame": "pygame",
    "starlette": "starlette",
    "httpx": "httpx",
    "watchdog": "watchdog",
    "nacl": "pynacl",
    "bcrypt": "bcrypt",
    "magic": "python-magic",
}


def main():
    found = scan_imports()
    declared, groups = parse_declared()

    # 本包内部模块名（zeroai 下的一级目录 + .py）
    local_names = set(LOCAL)
    for entry in os.listdir(PKG):
        p = os.path.join(PKG, entry)
        if os.path.isdir(p) and entry != "__pycache__":
            local_names.add(entry)
        elif entry.endswith(".py"):
            local_names.add(entry[:-3])

    third = {}
    std_used = set()
    for mod, files in sorted(found.items()):
        if mod in local_names:
            continue
        if mod in STDLIB:
            std_used.add(mod)
            continue
        third[mod] = sorted(files)

    missing = {}
    for mod in third:
        if mod in LOCAL_SUBPROJECTS:
            continue
        dist = DIST_NAME.get(mod, mod)
        hit = any(d.lower().replace("_", "-") == dist.lower().replace("_", "-")
                  for d in declared)
        if not hit:
            missing[mod] = {"dist": dist, "files": third[mod],
                            "n_files": len(third[mod])}

    result = {
        "n_files_scanned": len(list(iter_py(PKG))),
        "declared_packages": sorted(declared),
        "declared_groups": {k: sorted(v) for k, v in groups.items()},
        "third_party_used": {k: sorted(v) for k, v in third.items()},
        "missing_declaration": missing,
        "n_missing": len(missing),
    }

    os.makedirs(os.path.join(ROOT, "evals", "results"), exist_ok=True)
    with io.open(os.path.join(ROOT, "evals", "results", "imports.json"),
                 "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    L = ["# 第三方依赖声明审计", "",
         "- 扫描文件数: %d" % result["n_files_scanned"],
         "- pyproject 已声明（含 extras）: %d 个" % len(declared),
         "- 实际 import 的第三方模块: %d 个" % len(third),
         "- **已 import 但未声明: %d 个**" % len(missing), "",
         "## 未声明的依赖", "",
         "| import 名 | PyPI 发行名 | 引用文件数 | 引用位置 |",
         "|---|---|---|---|"]
    for mod, info in sorted(missing.items(), key=lambda kv: -kv[1]["n_files"]):
        locs = ", ".join("`%s`" % f for f in info["files"][:4])
        if info["n_files"] > 4:
            locs += " 等 %d 处" % info["n_files"]
        L.append("| `%s` | `%s` | %d | %s |" % (
            mod, info["dist"], info["n_files"], locs))

    L += ["", "## pyproject 现有声明分组", ""]
    for g, pkgs in sorted(result["declared_groups"].items()):
        L.append("- **%s**（%d）: %s" % (g, len(pkgs), ", ".join(sorted(pkgs))))

    L += ["", "## 已声明且未被 import 的（疑似过期）", ""]
    used_dists = {DIST_NAME.get(m, m) for m in third}
    stale = [d for d in sorted(declared)
             if d.lower().replace("_", "-") not in
             {u.lower().replace("_", "-") for u in used_dists}]
    L.append(", ".join("`%s`" % s for s in stale) if stale else "- （无）")

    with io.open(os.path.join(ROOT, "evals", "results", "imports.md"),
                 "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("OK -> evals/results/imports.md  (missing=%d)" % len(missing))


if __name__ == "__main__":
    main()
