# -*- coding: utf-8 -*-
"""ZeroAI 类的权威源码清单（测试专用，非测试文件）。

【为什么要有这个文件】
2026-10-06 把 `zeroai/tui/app.py` 里 3,664 行的 `ZeroAI` 巨类按职责拆成
app.py + 8 个 `app_*.py`。凡是"读源码做字符串 / AST 断言"的测试，如果继续
硬编码单个文件名，会出现两种坏结果：

  1. 误报 —— 方法搬走了，断言找不到就红（拆分当天就会炸 6 个测试）；
  2. 更糟的**静默失效** —— 扫描目标列表里少了新文件，测试照常绿，
     但那 8 个文件从此没人扫，覆盖悄无声息地掉了。

第 2 种不会有人报警，所以这里做单一事实来源：所有测试从这里取清单，
清单本身由 `test_tui_source_budget.py` 校验"没有漏掉的模块"。

【清单怎么来】
  TUI_APP_PATHS 的 glob 只是**便捷入口**，权威判定是
  `tui_zeroai_modules()`：解析 app.py 的类头继承表，反查每个 mixin
  来自哪个模块 —— 新增 mixin 模块若没被 glob 命中，护栏测试会失败。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUI_DIR = REPO_ROOT / "zeroai" / "tui"

# ---------------------------------------------------------------- 清单
def tui_app_paths():
    """承载 ZeroAI 类的全部源文件：绝对 Path 元组（app.py + app_*.py）。"""
    return (TUI_DIR / "app.py",) + tuple(sorted(TUI_DIR.glob("app_*.py")))


def tui_app_rels():
    """同上，但形如 "zeroai/tui/app.py" 的相对路径（POSIX 分隔）。"""
    return tuple(p.relative_to(REPO_ROOT).as_posix() for p in tui_app_paths())


def core_sources_rels():
    """承载 ZeroAI 的源文件（相对路径，POSIX 分隔）。

    2026-10-06 起与 `tui_app_rels()` 同义 —— 原本这里是
    `("tui_agent.py",) + tui_app_rels()`，即"旧壳 + 新实现"；tui_agent.py
    删除后只剩新实现。保留函数名是因为 `test_agent_loop_advanced` 等测试
    以它为清单入口，改名会让"清单"这件事重新散落到各处。
    """
    return tui_app_rels()


def read_sources(*rels):
    """按相对路径拼接源码，等价于老测试里的 `_source_of(*rels)`。"""
    chunks = []
    for rel in rels:
        fp = REPO_ROOT / Path(rel)
        if fp.exists():
            chunks.append(fp.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def read_tui_app_sources():
    """ZeroAI 的全部实现源码拼接（做字符串断言用）。"""
    return "\n".join(p.read_text(encoding="utf-8") for p in tui_app_paths())


# ---------------------------------------------------------------- 权威判定
def tui_zeroai_modules():
    """解析 app.py 类头，反查 ZeroAI 继承的每个 mixin 定义在哪个模块。

    返回相对路径字符串元组，例如 ("zeroai/tui/app_settings.py", ...)。
    这是"哪些文件承载 ZeroAI"的权威答案 —— glob 只是它的便捷入口。
    """
    import ast

    src = (TUI_DIR / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ZeroAI"),
        None,
    )
    if cls is None:
        raise AssertionError("zeroai/tui/app.py 里找不到 class ZeroAI")

    # from zeroai.tui.app_settings import SettingsMixin  ->  {SettingsMixin: 模块}
    alias2mod = {}
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                alias2mod[a.asname or a.name] = f"{n.module}.{a.name}"

    mods = []
    for base in cls.bases:
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if name is None:
            continue
        if name == "App":  # Textual 基类，不是本仓库的模块
            continue
        mod = alias2mod.get(name)
        if mod is None:
            raise AssertionError(f"ZeroAI 的基类 {name} 不是本仓库 import 进来的")
        mod_path = mod.rsplit(".", 1)[0]          # 去掉末尾的类名
        mods.append(mod_path.replace(".", "/") + ".py")
    # 类本身住在 app.py —— 清单里必须也包含它
    return ("zeroai/tui/app.py",) + tuple(mods)
