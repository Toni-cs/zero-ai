"""AGENTS.md 自动生成

本模块是 ``_auto_generate_agents_md`` 的**唯一定义处**。
（历史：2026-09-15 从 tui_agent.py L12143-12312 搬入，原处改为转发。）

【为什么搬】ZeroAI 类（迟早要搬进 ``zeroai/tui/app.py``）会调用它。
若留在 tui_agent.py，搬出去的 app.py 就得反过来 import tui_agent ——
那正是 1.1.5 故障的形态。故落到 zeroai.core。

【依赖】无。AST 实测该函数**不引用任何模块级名字**：函数体内自带
``import os as _os`` / ``import re as _re``，且 ``_IGNORE_DIRS`` /
``_IGNORE_EXTS`` 是函数内局部变量。因此搬走时不需要带走任何东西。
"""

def _auto_generate_agents_md(project_dir: str) -> str:
    """自动扫描项目结构并生成 AGENTS.md 内容（静默模式，不依赖 UI）

    ⚠️ 有副作用：本函数**会把内容写入 ``<project_dir>/AGENTS.md``**，
       然后才返回同一份内容。写入失败时静默忽略（仍返回内容）。

       原 docstring 只写了"返回 AGENTS.md 文件内容字符串"，**未提及会写盘**，
       容易让调用方/测试以为它是纯函数而在真实仓库上误跑（本次搬迁验证时
       就因此改写了仓库根目录的 AGENTS.md，已 `git checkout` 还原）。
       行为本身**未改动**（保持与搬迁前逐字一致），只是把文档补准确。

       如需只取内容不落盘，请传一个临时目录，或后续另加 ``dry_run`` 参数。

    Args:
        project_dir: 项目根目录路径

    Returns:
        生成的 AGENTS.md 内容；项目过小或无法扫描时返回空字符串
    """
    import os as _os
    import re as _re

    _IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                    ".idea", ".vs", ".vscode", "dist", "build", ".eggs",
                    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
                    "htmlcov", ".coverage", ".trae-cn"}
    _IGNORE_EXTS = {".pyc", ".pyo", ".so", ".dll", ".exe", ".lib", ".obj",
                    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".woff",
                    ".woff2", ".ttf", ".eot", ".zip", ".tar", ".gz", ".7z",
                    ".onnx", ".pt", ".bin", ".safetensors"}

    dir_tree = []
    file_list = []
    tech_stack = set()
    key_files = {}

    try:
        for root, dirs, files in _os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
            rel_root = _os.path.relpath(root, project_dir)
            depth = 0 if rel_root == "." else rel_root.count(_os.sep) + 1
            if depth > 3:
                dirs[:] = []
                continue

            if rel_root != ".":
                indent = "  " * depth
                dir_tree.append(f"{indent}{_os.path.basename(root)}/")

            for f in sorted(files):
                ext = _os.path.splitext(f)[1].lower()
                if ext in _IGNORE_EXTS:
                    continue
                fpath = _os.path.join(root, f)
                rel_path = _os.path.relpath(fpath, project_dir)
                file_list.append(rel_path)

                if f == "pyproject.toml":
                    tech_stack.add("Python")
                    key_files["pyproject.toml"] = rel_path
                elif f == "setup.py":
                    tech_stack.add("Python")
                    key_files["setup.py"] = rel_path
                elif f == "requirements.txt":
                    tech_stack.add("Python")
                    key_files["requirements.txt"] = rel_path
                elif f == "package.json":
                    tech_stack.add("Node.js")
                    key_files["package.json"] = rel_path
                elif f == "Cargo.toml":
                    tech_stack.add("Rust")
                    key_files["Cargo.toml"] = rel_path
                elif f == "go.mod":
                    tech_stack.add("Go")
                    key_files["go.mod"] = rel_path
                elif f == "CMakeLists.txt":
                    tech_stack.add("C/C++")
                    key_files["CMakeLists.txt"] = rel_path
                elif f == "Makefile":
                    tech_stack.add("Make")
                elif f == "Dockerfile":
                    tech_stack.add("Docker")
                elif f == ".gitignore":
                    key_files[".gitignore"] = rel_path
                elif f in ("README.md", "readme.md"):
                    key_files["README"] = rel_path

            if len(file_list) > 200:
                break
    except Exception:
        return ""

    # 文件太少，可能是空目录，不生成
    if len(file_list) < 3:
        return ""

    # 读取 pyproject.toml
    project_name = _os.path.basename(project_dir)
    project_version = ""
    dependencies = []

    pyproject_path = _os.path.join(project_dir, "pyproject.toml")
    if _os.path.exists(pyproject_path):
        try:
            with open(pyproject_path, "r", encoding="utf-8") as f:
                content = f.read()
            name_m = _re.search(r'name\s*=\s*"([^"]+)"', content)
            ver_m = _re.search(r'version\s*=\s*"([^"]+)"', content)
            if name_m:
                project_name = name_m.group(1)
            if ver_m:
                project_version = ver_m.group(1)
            dep_section = _re.search(r'dependencies\s*=\s*\[([\s\S]*?)\]', content)
            if dep_section:
                deps_raw = dep_section.group(1)
                dependencies = _re.findall(r'"([^"]+)"', deps_raw)
        except Exception:
            pass

    # 生成 AGENTS.md 内容
    lines = []
    lines.append(f"# AGENTS.md — {project_name} 项目上下文")
    lines.append("")
    lines.append("> 此文件由 ZeroAI 自动生成，帮助 AI 理解项目结构。")
    lines.append("> 修改项目结构后重启 ZeroAI 或运行 /初始化 更新。")
    lines.append("")
    lines.append("## 项目信息")
    lines.append(f"- **名称**：{project_name}")
    if project_version:
        lines.append(f"- **版本**：{project_version}")
    lines.append(f"- **路径**：{project_dir}")
    lines.append(f"- **技术栈**：{', '.join(tech_stack) if tech_stack else '未检测到'}")
    lines.append("")

    lines.append("## 目录结构")
    lines.append("```")
    lines.append(f"{project_name}/")
    for d in dir_tree[:50]:
        lines.append(d)
    lines.append("```")
    lines.append("")

    if key_files:
        lines.append("## 关键文件")
        for name, path in key_files.items():
            lines.append(f"- `{path}` — {name}")
        lines.append("")

    if dependencies:
        lines.append("## 核心依赖")
        for dep in dependencies[:20]:
            lines.append(f"- {dep}")
        if len(dependencies) > 20:
            lines.append(f"- ... 共 {len(dependencies)} 个依赖")
        lines.append("")

    ext_counts = {}
    for fpath in file_list:
        ext = _os.path.splitext(fpath)[1].lower()
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    if ext_counts:
        lines.append("## 文件类型统计")
        for ext, cnt in sorted(ext_counts.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"- `{ext}` — {cnt} 个文件")
        lines.append("")

    lines.append("## 编码规范")
    lines.append("- 使用中文注释和文档字符串")
    lines.append("- Python 代码遵循 PEP 8")
    lines.append("- 修改核心文件前必须备份")
    lines.append("")

    agents_content = "\n".join(lines)

    # 写入文件
    agents_path = _os.path.join(project_dir, "AGENTS.md")
    try:
        with open(agents_path, "w", encoding="utf-8") as f:
            f.write(agents_content)
    except Exception:
        pass  # 写入失败也返回内容，至少这次会话能用

    return agents_content


def get_module_info() -> dict:
    """返回模块信息（自检 / 迁移进度跟踪）"""
    return {"exports": ["_auto_generate_agents_md"]}
