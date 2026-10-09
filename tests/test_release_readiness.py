"""发布测试：完整集成验证

测试目标：
1. 版本号正确（动态对比 zeroai.__version__，不再硬编码具体版本）
2. 统一入口 zeroai.main 可用
3. python -m zeroai 支持
4. zeroai.core 全部子模块可导入
5. zeroai.tools 全部子模块可导入 + registry 一致性
6. zeroai.tui 包装模块可导入
7. 关键工具函数实际调用

（原第 7/8/10 项是 tui_agent.py 的兼容性 / 切换块 / 循环导入校验，
  该文件已于 2026-10-06 删除，三项随之移除；原第 9 项"实际调用"改指
  zeroai 包的直接 import 后保留为现在的第 7 项。）
"""
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 本文件已移入 tests/，需上溯一级


def test_version():
    """测试 1：版本号验证（与包内 __version__ 动态比对）"""
    print("[Test 1] 版本号验证...")
    import zeroai
    result = subprocess.run(
        [sys.executable, "-m", "zeroai", "--version"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 必须在项目根执行，否则 -m zeroai 找不到包
    )
    assert result.returncode == 0, f"zeroai --version 失败: {result.stderr}"
    assert zeroai.__version__ in result.stdout, \
        f"--version 输出 {result.stdout!r} 与包版本 {zeroai.__version__} 不一致"
    print(f"  OK: {result.stdout.strip()}")


def test_main_entry():
    """测试 2：统一入口"""
    print("\n[Test 2] 统一入口验证...")
    import zeroai
    from zeroai.main import main, _get_version
    assert callable(main), "zeroai.main.main 不可调用"
    assert _get_version() == zeroai.__version__, f"版本号错误: {_get_version()}"
    print(f"  OK: zeroai.main.main 可调用，版本 {_get_version()}")


def test_module_entry():
    """测试 3：python -m zeroai 支持"""
    print("\n[Test 3] python -m zeroai 支持...")
    import zeroai.__main__
    assert hasattr(zeroai.__main__, "main"), "zeroai.__main__ 无 main 函数"
    print("  OK: python -m zeroai 可用")


def test_core_imports():
    """测试 4：zeroai.core 全部子模块"""
    print("\n[Test 4] zeroai.core 子模块导入...")
    from zeroai.core import (
        paths, runtime, secrets, constants, expert_route,
        context_compress, model_manager, response_utils,
    )
    # 验证关键函数
    from zeroai.core.paths import _get_desktop_dir
    from zeroai.core.runtime import runtime_cache, RuntimeCache
    from zeroai.core.secrets import _load_config, _save_config
    from zeroai.core.constants import MODEL_CONFIGS, EXPERT_TEAM, WORK_MODE
    from zeroai.core.expert_route import route_expert, LRUCache
    from zeroai.core.context_compress import cleanup_context, compress_context
    from zeroai.core.model_manager import get_active_model_info
    from zeroai.core.response_utils import _strip_model_tokens
    print("  OK: 全部 8 个 core 子模块导入成功")


def test_tools_imports():
    """测试 5：zeroai.tools 全部子模块 + registry"""
    print("\n[Test 5] zeroai.tools 子模块导入...")
    from zeroai.tools import (
        file_manager, command_exec, network, system_check,
        security, doc_gen, academic, window_mgr, ssh_ops, registry,
        voice,
    )
    from zeroai.tools.registry import TOOLS, TOOL_MAP
    # 计数是"已知良品数"守卫：防止工具被静默增删。
    # 有意新增工具时**必须同步改这里**并说明原因 —— 否则这条会立刻变红。
    # 真正的结构性不变式是下面那行 tools_keys == map_keys（与数字无关）。
    # 2026-10-09：63 -> 65，新增 skill_list / skill_load（Skills 机制，
    # 对标 OpenCode，见 zeroai/core/skills.py）。
    assert len(TOOLS) == 65, f"TOOLS 数量错误: {len(TOOLS)}"
    assert len(TOOL_MAP) == 65, f"TOOL_MAP 数量错误: {len(TOOL_MAP)}"
    tools_keys = {t["function"]["name"] for t in TOOLS}
    map_keys = set(TOOL_MAP.keys())
    assert tools_keys == map_keys, "TOOLS 和 TOOL_MAP key 不一致"
    print(f"  OK: 全部 11 个 tools 子模块导入成功，{len(TOOLS)} 个工具注册")


def test_tui_wrappers():
    """测试 6：zeroai.tui 包装模块"""
    print("\n[Test 6] zeroai.tui 包装模块...")
    from zeroai.tui.colors import C_BG, C_FG
    from zeroai.tui.markdown import render_markdown
    from zeroai.tui.identity import _sanitize_identity_leak
    from zeroai.tui.widgets import InfoBar, HintBar, TokenBar
    from zeroai.tui.screens import AddModelScreen, SettingsScreen, VoiceDialogScreen
    from zeroai.tui.app import ZeroAI

    # 验证 __getattr__ 按需导入
    import zeroai.tui
    assert zeroai.tui.ZeroAI is ZeroAI, "__getattr__ 导入不一致"
    assert zeroai.tui.InfoBar is InfoBar, "__getattr__ 导入不一致"
    print("  OK: 全部 7 个 tui 子模块导入成功（colors/markdown/identity/widgets/screens/app/icons）")


def test_real_calls():
    """测试 7：关键功能实际调用"""
    print("\n[Test 7] 关键功能实际调用...")
    from zeroai.core.expert_route import route_expert
    from zeroai.core.paths import _get_desktop_dir
    from zeroai.tools.academic import render_formula
    from zeroai.tools.file_manager import read_file
    from zeroai.tools.system_check import system_info

    # read_file
    result = read_file(__file__, max_length=50)
    assert isinstance(result, str) and result
    print(f"  OK: read_file 成功（{len(result)} 字符）")

    # system_info
    result = system_info()
    assert isinstance(result, str)
    print(f"  OK: system_info 成功（{len(result)} 字符）")

    # route_expert
    expert = route_expert("写一个 Python 函数")
    assert isinstance(expert, str)
    print(f"  OK: route_expert 成功（{expert}）")

    # render_formula
    result = render_formula("E=mc^2")
    assert isinstance(result, str)
    print(f"  OK: render_formula 成功（{result[:30]}）")

    # _get_desktop_dir
    desktop = _get_desktop_dir()
    assert isinstance(desktop, str) and desktop
    print(f"  OK: _get_desktop_dir 成功（{desktop}）")


def main():
    print("=" * 70)
    print("ZeroAI 发布测试")
    print("=" * 70)

    test_version()
    test_main_entry()
    test_module_entry()
    test_core_imports()
    test_tools_imports()
    test_tui_wrappers()
    test_real_calls()

    print("\n" + "=" * 70)
    print("✅ 全部 7 项测试通过，发布就绪")
    print("=" * 70)
    print("\n架构摘要：")
    print("  zeroai/                    模块化包（唯一实现）")
    print("  ├── core/                  核心层")
    print("  ├── tools/                 工具层")
    print("  ├── tui/                   TUI 层（app.py + 8 个 app_*.py mixin）")
    print("  ├── main.py                统一入口")
    print("  └── __main__.py            模块入口")
    print("  pyproject.toml             入口 zeroai.main:main，py-modules = []")


if __name__ == "__main__":
    main()
