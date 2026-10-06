# -*- coding: utf-8 -*-
"""TUI 源码清单护栏 + 单文件复杂度预算。

【为什么加】2026-10-06 拆 `ZeroAI` 巨类时，外部评审的原话是
「会写新功能，不等于会控制复杂度」。拆一次只解决当下，不设闸门下次
还会涨回去，所以这条测试同时守住两件事：

  1. **清单完整性**：凡是承载 ZeroAI 的模块，都必须出现在
     `tui_sources.tui_app_paths()` 里 —— 否则"读源码做断言"的测试会
     静默跳过新文件（覆盖掉了、绿灯还在，没人会发现）。
  2. **复杂度预算**：任一 TUI 文件不得超过 800 行。
     拆分当天的真实数字：app.py 563，最大 app_turn_multi.py 734，
     而拆分前 app.py 是 3,664 行 / 单类 3,513 行。
     涨到 800 就红，逼着下一次改动先想清楚"这块该归谁"。
"""
from pathlib import Path

import pytest

from tui_sources import (
    TUI_DIR,
    tui_app_paths,
    tui_app_rels,
    tui_zeroai_modules,
)

# 单文件行数预算（拆分当天最大 734，留 66 行余量）
MAX_LINES = 800


def test_manifest_covers_every_zeroai_module():
    """清单必须精确等于「app.py 类头里挂的那些模块」，不能多也不能少。"""
    declared = set(tui_zeroai_modules())       # 权威：解析类头继承表
    listed = set(tui_app_rels())               # 便捷：glob app*.py
    missing = sorted(declared - listed)        # 有模块承载 ZeroAI 却没进清单
    extra = sorted(listed - declared)          # 进了清单但其实不承载 ZeroAI
    assert not missing, (
        "这些模块承载 ZeroAI，却没进 tui_sources 清单，"
        "所有读源码的断言都会漏掉它们：" + str(missing)
    )
    assert not extra, "清单里有不承载 ZeroAI 的文件（glob 太宽或忘记更新）：" + str(extra)


def test_zeroai_class_exists_only_in_app_py():
    """同一个类名不得在包内出现第二份实现（防止"拆了又长回来"）。"""
    hits = []
    for p in sorted(TUI_DIR.glob("*.py")):
        if "class ZeroAI" in p.read_text(encoding="utf-8"):
            hits.append(p.name)
    assert hits == ["app.py"], f"class ZeroAI 出现在 {hits}，应只存在于 app.py"


@pytest.mark.parametrize("path", tui_app_paths(), ids=lambda p: p.name)
def test_file_within_complexity_budget(path: Path):
    """单文件行数预算：超过就说明又开始堆了，先拆再加功能。"""
    n = len(path.read_text(encoding="utf-8").splitlines())
    assert n <= MAX_LINES, (
        f"{path.name} 已 {n} 行，超过预算 {MAX_LINES} 行。"
        "这一条是 2026-10-06 拆 3,664 行巨类之后立的规矩："
        "先按职责拆模块，再往里加功能。"
    )


def test_app_py_is_a_shell_not_a_god_file():
    """app.py 只该留生命周期与骨架，不该重新长成大文件。"""
    n = len((TUI_DIR / "app.py").read_text(encoding="utf-8").splitlines())
    assert n <= MAX_LINES, f"app.py 已 {n} 行，骨架不该长到 {MAX_LINES} 行"
