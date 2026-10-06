# -*- coding: utf-8 -*-
"""GHOST 名字回归测试（2026-09-16 新增）

【为什么加这个】
2026-09-15 的 ZeroAI 搬迁中，`ZeroAI` 从 tui_agent.py 搬到 zeroai/tui/app.py，
但 `_load_config` / `_save_config` / `_truncate_expert_response` /
`_OPENROUTER_FAIL_COUNTS` 这 4 个名字既没进 switch-over 块、也没被显式 import，
成了悬空引用 —— 运行到那些行就是 NameError。

当时那次审计（audit_fallback 系列）报 "ghost=[]"，因为它只检查了
「switch 块里的 140 个名字是否安全」，**没有**检查
「ZeroAI 实际加载的每个名字是否都存在于 app.py 的模块全局」。

本测试用 CPython 的 symtable 做权威判定：对源文件里每个作用域，
取所有 `is_global() and not is_assigned()` 的符号（即真的从模块全局读），
再验证该名字是否可由模块级 import/赋值/def/内置解析。
任何解析不了的名字 = 运行期 NameError = GHOST。
"""
import builtins
import ast
import symtable
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tests"))

from tui_sources import tui_app_paths  # noqa: E402


def _module_namespace(tree):
    """收集模块级可解析的名字（import / 赋值 / def / class / 内置）。"""
    names = set(dir(builtins)) | {
        "__name__", "__file__", "__doc__", "__builtins__",
        "__spec__", "__package__", "__loader__", "__cached__",
    }

    def harvest(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom):
                for a in sub.names:
                    names.add(a.asname or a.name)
            elif isinstance(sub, ast.Import):
                for a in sub.names:
                    names.add((a.asname or a.name).split(".")[0])
            elif isinstance(sub, ast.Assign):
                for t in sub.targets:
                    for x in ast.walk(t):
                        if isinstance(x, ast.Name):
                            names.add(x.id)
            elif isinstance(sub, ast.AnnAssign):
                if isinstance(sub.target, ast.Name):
                    names.add(sub.target.id)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                names.add(sub.name)

    for node in tree.body:
        harvest(node)
    return names


def find_ghosts(path: Path):
    """返回 {name: [scope_path, ...]}，全为空表示干净。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    st = symtable.symtable(src, path.name, "exec")
    module_names = _module_namespace(tree)

    ghosts = {}

    def walk(tbl, path_acc, top=False):
        if not top:
            for name in tbl.get_identifiers():
                sym = tbl.lookup(name)
                if sym.is_global() and not sym.is_assigned():
                    if name not in module_names:
                        ghosts.setdefault(name, []).append(path_acc or "<module>")
        for child in tbl.get_children():
            walk(child, (path_acc + "/" + child.get_name()).lstrip("/"))

    walk(st, "", top=True)
    return ghosts, len(module_names)


# ══════════════════════════════════════════════════════════════════
# 核心断言：承载 ZeroAI 的每个文件都不得有悬空全局名
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("path", tui_app_paths(), ids=lambda p: p.name)
def test_app_py_has_no_ghost_names(path):
    """承载 ZeroAI 的每个文件都不得存在无法解析的全局读名字。

    这是一个"结构性"测试：它不依赖运行时是否走到某行，
    因此能在**静态阶段**拦住"搬走了定义、忘了搬引用"这类回归。

    2026-10-06 起 ZeroAI 拆成 app.py + 8 个 app_*.py，参数化到全部文件 ——
    只查 app.py 的话，新拆出去的 8 个文件就没有这道防线。
    """
    ghosts, n = find_ghosts(path)
    assert not ghosts, (
        f"{path.name} 发现 {len(ghosts)} 个悬空全局名（运行期 NameError）：\n"
        + "\n".join(f"  {k}  出现于 {v[:3]}" for k, v in sorted(ghosts.items()))
    )


def test_screens_has_no_ghost_names():
    """zeroai/tui/screens.py 同样检查（本轮改动过该文件）。"""
    ghosts, n = find_ghosts(_ROOT / "zeroai" / "tui" / "screens.py")
    assert not ghosts, (
        f"screens.py 发现 {len(ghosts)} 个悬空全局名：\n"
        + "\n".join(f"  {k}  出现于 {v[:3]}" for k, v in sorted(ghosts.items()))
    )


# ══════════════════════════════════════════════════════════════════
# 针对性断言：本轮修复的 4 个名字必须真的可解析且指向同一对象
# ══════════════════════════════════════════════════════════════════
def test_four_previously_ghost_names_are_bound():
    """这 4 个名字曾因搬迁而悬空，必须都能从 app.py 解析到。"""
    import zeroai.tui.app as APP

    for name in ("_load_config", "_save_config",
                 "_truncate_expert_response", "_OPENROUTER_FAIL_COUNTS"):
        assert name in APP.__dict__, f"{name} 仍不在 app.py 模块命名空间中"


def test_repaired_names_are_same_objects_as_canonical():
    """修复必须是"引用"而非"复制"——否则又会出现两份真源漂移。

    这正是 _OPENROUTER_FAIL_COUNTS 当初出问题的根因：
    tui_agent 和 expert_route 各有一份 dict，写一份读另一份，
    计数器永远是 0（本次一并修正为单一真源）。
    """
    import zeroai.tui.app as APP
    import zeroai.core.secrets as S
    import zeroai.core.expert_route as ER
    import zeroai.core.response_utils as RU

    assert APP._load_config is S._load_config
    assert APP._save_config is S._save_config
    assert APP._truncate_expert_response is RU._truncate_expert_response
    assert APP._OPENROUTER_FAIL_COUNTS is ER._OPENROUTER_FAIL_COUNTS, (
        "app 读的 _OPENROUTER_FAIL_COUNTS 必须与 expert_route 写的是同一个对象"
    )


def test_openrouter_counter_is_single_source_of_truth():
    """熔断计数器必须是单一真源（app 读的 == expert_route 写的）。

    【重要】实测发现：EXPERT_TEAM 中**没有任何** model_key=="openrouter" 的专家，
    因此 _is_openrouter_expert() 恒为 False，_record_openrouter_failure()
    会提前 return 0、根本不写 dict —— 整个熔断子系统目前是**不可达代码**。

    所以这里不能断言"计数会增长"（那是假的）。能断言的只有：
      1) 两个模块引用的是同一个 dict 对象（消除"写一份读另一份"的隐患）；
      2) 一旦将来加入 openrouter 专家，读写会落到同一处。
    """
    import zeroai.tui.app as APP
    import zeroai.core.expert_route as ER
    from zeroai.core.constants import EXPERT_TEAM

    # 1) 同一对象
    assert APP._OPENROUTER_FAIL_COUNTS is ER._OPENROUTER_FAIL_COUNTS

    # 2) 模拟"存在一个 openrouter 专家"后，读写确实落到同一 dict
    key = "__ghost_probe_expert__"
    fake_cfg = {"model_key": "openrouter", "label": "probe",
                "model": "x", "base_url": "x"}
    EXPERT_TEAM[key] = fake_cfg
    try:
        ER._OPENROUTER_FAIL_COUNTS.pop(key, None)
        n = ER._record_openrouter_failure(key)
        assert n == 1, f"记录一次失败应返回 1，实际 {n}"
        # 关键：app 侧读到同一个 dict 的值（修复前读到的是 tui_agent 的另一份，恒为 0）
        assert APP._OPENROUTER_FAIL_COUNTS.get(key, 0) == 1, (
            "app 读到的计数器未随 expert_route 的写入更新 —— 说明仍是两份真源"
        )
        # 熔断器在 3 次后应触发
        ER._record_openrouter_failure(key)
        assert ER._record_openrouter_failure(key) == 3
        assert ER._check_openrouter_circuit_breaker(key) is True, (
            "连续失败 3 次后熔断器应生效"
        )
        # 成功一次应重置
        ER._record_openrouter_success(key)
        assert APP._OPENROUTER_FAIL_COUNTS.get(key, 0) == 0
    finally:
        EXPERT_TEAM.pop(key, None)
        ER._OPENROUTER_FAIL_COUNTS.pop(key, None)


def test_openrouter_subsystem_currently_unreachable():
    """记录当前事实：EXPERT_TEAM 中无 openrouter 专家，熔断器不可达。

    这不是"要求"而是"现状快照"。若将来真的加入了 openrouter 专家，
    本测试会失败 —— 那正是提醒"熔断器现在变成活代码了，请重新审查"的信号。
    """
    from zeroai.core.constants import EXPERT_TEAM

    ors = [k for k, v in EXPERT_TEAM.items() if v.get("model_key") == "openrouter"]
    assert not ors, (
        f"检测到 OpenRouter 专家 {ors} —— 熔断器已成为活代码，"
        "请重新审查 _OPENROUTER_FAIL_COUNTS 的读写路径与降级逻辑"
    )


# ══════════════════════════════════════════════════════════════════
# 函数级（惰性）反向依赖：模块级检查发现不了它们
# ══════════════════════════════════════════════════════════════════
def _lazy_tui_agent_imports(path):
    """返回某文件里所有对 tui_agent 的 import 语句行号（含函数内）。"""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("tui_agent"):
                out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("tui_agent"):
                    out.append((node.lineno, a.name))
    return out


def test_no_reverse_dependency_on_tui_agent():
    """zeroai/ 内不得有任何对 tui_agent 的 import（模块级与函数级都扫）。

    【为什么加这条】2026-09-16 发现：此前的"模块级反向依赖 = 0"检查
    **只覆盖模块级 import**，因此 screens.py 里
    `from tui_agent import speak_tts`（在 TTS 分支内）一直没被发现 ——
    而且它还引入了一份与 zeroai.tools.voice 不同的**重复实现**。

    【2026-10-06 更新】tui_agent.py 已彻底删除。原本给 zeroai/main.py 留的
    白名单（"过渡期回退：zeroai.tui 导入失败时回退到 tui_agent"）随之撤销 ——
    现在是**零白名单**，整个 zeroai/ 一棵树都不允许出现这类 import。

    这条现在同时是 pyproject.toml 里退出条件 (a) 的执行者：那里原写
    `grep -rn "from tui_agent\\|import tui_agent" zeroai/` 返回空，但 grep 会
    命中注释与文档字符串里的迁移史料（"原先这里是 from tui_agent import …"），
    而史料值得保留。故改用本测试的 AST 判定：只看**语句**，不看文字。
    """
    offenders = {}
    for py in sorted((_ROOT / "zeroai").rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        found = _lazy_tui_agent_imports(py)
        if found:
            offenders[str(py.relative_to(_ROOT))] = found
    assert not offenders, (
        "zeroai/ 内存在对 tui_agent 的导入（含函数内懒加载）：\n"
        + "\n".join(f"  {k}: {v}" for k, v in offenders.items())
    )
