"""工具接线回归测试：TOOLS / TOOL_MAP 必须与 registry 单一真源一致

【本文件的历史与现状】
原名用途是"阶段3 回归"：验证 `tui_agent.py` 内部调用已切换到 `zeroai` 包。
2026-10-06 `tui_agent.py` 被彻底删除（决策记录见 pyproject.toml），原先 8 项
里有 4 项是**纯接线校验**（tui_agent 能否导入、重载后切换块是否激活、
工具/core 符号是否来自 zeroai.*）—— 这些在被测对象删除后无从谈起，一并删除。

保留的 4 项与 tui_agent 无关，测的是 `zeroai` 包自身的不变量，因此改指
`zeroai.tools.registry`（TOOLS/TOOL_MAP 的唯一定义处）继续存活：
1. TOOLS 与 TOOL_MAP 一致，且每个工具都来自 zeroai.*
2. TOOL_MAP 中所有 value 都可调用
3. 关键工具真实调用（只测无副作用的）
4. 工具签名与注册时的参数列表一致
"""
import inspect

from zeroai.core.expert_route import route_expert
from zeroai.core.model_manager import get_model_label
from zeroai.core.paths import _get_desktop_dir
from zeroai.core.response_utils import _parse_think_tags
from zeroai.tools.academic import render_formula
from zeroai.tools.file_manager import list_dir, read_file, search_files
from zeroai.tools.registry import TOOL_MAP, TOOLS
from zeroai.tools.system_check import system_info


def test_tools_toolmap_consistency():
    """测试 1：TOOLS 与 TOOL_MAP 的 key 必须完全一致，且都来自 zeroai.*"""
    print("[Test 1] TOOLS / TOOL_MAP 一致性验证...")

    # 工具函数不得有外部实现混入（原 test_tools_from_zeroai 的实质：
    # 曾用来抓 tui_agent 拿着本地副本不放，现在抓 registry 被塞进非 zeroai 实现）
    bad = {
        name: getattr(fn, "__module__", "?")
        for name, fn in TOOL_MAP.items()
        if not getattr(fn, "__module__", "").startswith("zeroai.")
    }
    assert not bad, f"以下工具实现不来自 zeroai.*: {bad}"

    tools_names = {t["function"]["name"] for t in TOOLS}
    map_keys = set(TOOL_MAP.keys())
    assert tools_names == map_keys, f"TOOLS 和 TOOL_MAP 的 key 不一致：差异={tools_names ^ map_keys}"
    print(f"  OK: TOOLS({len(tools_names)}) 和 TOOL_MAP({len(map_keys)}) 的 key 完全一致，"
          "实现均来自 zeroai.*")


def test_tool_callables():
    """测试 2：TOOL_MAP 中所有 value 都是可调用对象"""
    print("\n[Test 2] TOOL_MAP 可调用性验证...")
    non_callable = [name for name, fn in TOOL_MAP.items() if not callable(fn)]
    if non_callable:
        raise AssertionError(f"以下工具不可调用: {non_callable}")
    print(f"  OK: 全部 {len(TOOL_MAP)} 个工具都是可调用对象")


def test_real_tool_calls():
    """测试 3：关键工具函数实际调用（只测试无副作用的）"""
    print("\n[Test 3] 关键工具函数实际调用验证...")

    # 3.1 read_file 读取自身（max_length=100 只取到文件头的中文 docstring，
    #     所以按文件头实际内容断言，而不是按 "test"/"regression" 这类英文词）
    result = read_file(__file__, max_length=100)
    assert isinstance(result, str), "read_file 返回值不是 str"
    assert "工具接线" in result, f"read_file 内容异常: {result[:50]}"
    print(f"  OK: read_file 读取自身文件成功（{len(result)} 字符）")

    # 3.2 list_dir 列出当前目录
    result = list_dir(".")
    assert isinstance(result, str), "list_dir 返回值不是 str"
    assert "zeroai" in result, f"list_dir 内容异常: {result[:50]}"
    print(f"  OK: list_dir 列出当前目录成功（{len(result)} 字符）")

    # 3.3 system_info
    result = system_info()
    assert isinstance(result, str), "system_info 返回值不是 str"
    print(f"  OK: system_info 返回系统信息（{len(result)} 字符）")

    # 3.4 search_files 搜索自身
    result = search_files("def test_", path=".")
    assert isinstance(result, str), "search_files 返回值不是 str"
    print(f"  OK: search_files 搜索成功（{len(result)} 字符）")

    # 3.5 _get_desktop_dir
    desktop = _get_desktop_dir()
    assert isinstance(desktop, str), "_get_desktop_dir 返回值不是 str"
    assert desktop, "_get_desktop_dir 返回空字符串"
    print(f"  OK: _get_desktop_dir = {desktop}")

    # 3.6 route_expert 路由测试
    expert = route_expert("帮我写一个 Python 函数")
    assert isinstance(expert, str), "route_expert 返回值不是 str"
    print(f"  OK: route_expert('帮我写一个 Python 函数') = {expert}")

    # 3.7 get_model_label
    label = get_model_label()
    assert isinstance(label, str), "get_model_label 返回值不是 str"
    print(f"  OK: get_model_label() = {label}")

    # 3.8 render_formula 公式渲染
    result = render_formula("E=mc^2")
    assert isinstance(result, str), "render_formula 返回值不是 str"
    print(f"  OK: render_formula('E=mc^2') = {result[:50]}")

    # 3.9 _parse_think_tags（返回顺序：think_content, body_content）
    think_content, body_content = _parse_think_tags("<think>思路</think>回答")
    assert think_content == "思路", f"_parse_think_tags think_content 异常: {think_content}"
    assert body_content == "回答", f"_parse_think_tags body_content 异常: {body_content}"
    print(f"  OK: _parse_think_tags 解析正确（think={think_content}, body={body_content}）")


def test_tool_signatures():
    """测试 4：注册工具的签名与预期参数列表一致"""
    print("\n[Test 4] 工具函数签名验证...")
    expected_sigs = {
        "read_file": ["path", "max_length"],
        "write_file": ["path", "content"],
        "list_dir": ["path", "recursive", "max_depth"],
        "search_files": ["pattern", "path"],
        "delete_file": ["path"],
        "move_file": ["src", "dst"],
        "copy_file": ["src", "dst"],
        "create_dir": ["path"],
        "edit_file": ["path", "operation", "line", "content", "start_line", "end_line"],
        "file_diff": ["path_a", "path_b"],
        "read_image": ["path"],
        # 2026-10-09 对齐 OpenCode bash 工具新增 workdir / timeout（见 command_exec.py）
        "run_command": ["command", "workdir", "timeout", "skip_translate"],
        "exec_python": ["code", "timeout"],
        "pip_install": ["package", "action"],
        "web_search": ["query", "num_results"],
        "web_fetch": ["url", "max_length"],
        "git_status": ["repo_path"],
        "system_info": [],
        "process_list": ["name_filter"],
        "check_port": ["port"],
        "render_formula": ["latex", "style"],
        "active_window": [],
        "list_windows": [],
        "read_screen": ["max_length"],
    }
    failed = []
    for name, expected_params in expected_sigs.items():
        fn = TOOL_MAP.get(name)
        if fn is None:
            failed.append(f"  FAIL: {name} 未注册")
            continue
        sig = inspect.signature(fn)
        actual_params = list(sig.parameters.keys())
        if actual_params != expected_params:
            failed.append(f"  FAIL: {name} 签名 {actual_params}，期望 {expected_params}")
    if failed:
        for f in failed:
            print(f)
        raise AssertionError(f"{len(failed)} 个工具签名错误")
    print(f"  OK: 全部 {len(expected_sigs)} 个工具签名验证通过")


def main():
    print("=" * 70)
    print("工具接线回归测试：TOOLS / TOOL_MAP 与 zeroai.tools.registry 单一真源")
    print("=" * 70)

    test_tools_toolmap_consistency()
    test_tool_callables()
    test_real_tool_calls()
    test_tool_signatures()

    print("\n" + "=" * 70)
    print("✅ 全部 4 项测试通过")
    print("=" * 70)
    print("\n摘要：")
    print(f"  - 工具：{len(TOOL_MAP)} 个，实现全部来自 zeroai.*")
    print(f"  - TOOLS / TOOL_MAP：zeroai.tools.registry 内的同一组数据")
    print("  - 注：tui_agent.py 已于 2026-10-06 删除，本文件原 8 项中的")
    print("    4 项纯接线校验随之移除（详见文件头）")


if __name__ == "__main__":
    main()
