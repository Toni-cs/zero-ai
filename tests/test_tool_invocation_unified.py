"""工具调用统一入口的回归测试。

背景（2026-09-16）
==========================================================================
"按签名过滤模型幻觉参数"这段逻辑此前在三个派发点各抄了一份：

  - ``zeroai/tui/app.py``            （TUI 主对话路径）
  - ``zeroai/core/agent.py``         （ReAct 路径）
  - ``zeroai/core/parallel_tools.py``（并行工具）

三份**漏了同一个 case**：当被调函数签名是 ``**kwargs`` 时，
``set(inspect.signature(fn).parameters)`` 得到 ``{'kwargs'}``，
于是模型传的**每一个真实参数都被当成幻觉参数丢掉**。

MCP 工具包装函数正是 ``async def _wrapper(**kwargs)``，所以：

  模型调 ``mcp__computer__type_text({"text": "hi"})``
    → 过滤后 ``safe_args = {}``
    → 服务器报 ``1 validation error for type_textArguments / text Field required``

更糟的是 TUI 路径还有第二个缺陷：它把 ``async`` 包装函数**当同步函数调用**，
``result = fn(**safe_args)`` 拿到的是协程对象，从未被 await，随后
``result += "..."`` 抛 ``TypeError``，被 ``except TypeError`` 吞成

  参数错误：unsupported operand type(s) for +=: 'coroutine' and 'str'

即：15 个电脑操作工具**全部"点了没反应"**，且报错文案完全误导。

本测试锁住三件事：
1. ``filter_tool_args`` 对 ``**kwargs`` / 签名不可解析两类都全量透传；
2. ``invoke_tool`` 正确 await 异步工具（这是 TUI 崩溃的直接原因）；
3. 三个派发点**不再**存在第 4 份手写过滤（静态锁）。
"""
import ast
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from zeroai.mcp.registry import _build_signature_from_schema  # noqa: E402
from zeroai.tools.registry import TOOL_MAP, filter_tool_args, invoke_tool  # noqa: E402
from tui_sources import read_tui_app_sources, tui_app_paths  # noqa: E402

# 三个派发点（迁移后都应改为调用统一入口）。
# TUI 派发点 2026-10-06 由单个 app.py 拆成 app.py + 8 个 app_*.py，
# 清单走 tui_sources，避免以后再拆时静默漏扫。
DISPATCH_FILES = [
    *tui_app_paths(),
    ROOT / "zeroai" / "core" / "agent.py",
    ROOT / "zeroai" / "core" / "parallel_tools.py",
]

# 真实 MCP 工具的 inputSchema 形状（取自 computer-control-mcp 的 type_text）
COMPUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要输入的文字"},
        "interval": {"type": "number"},
    },
    "required": ["text"],
}


# ═══════════════════════ T1：参数过滤契约 ═══════════════════════

def test_filter_passthrough_for_var_keyword():
    """T1a **kwargs 型函数必须全量透传（这正是 MCP 包装函数的形状）"""
    async def wrapper(**kwargs):
        return "x"

    safe, extra = filter_tool_args(wrapper, {"text": "hi", "n": 1})
    assert safe == {"text": "hi", "n": 1}, f"参数被丢掉了: {safe}"
    assert extra == set()


def test_filter_normal_function_still_filters():
    """T1b 普通函数仍按名过滤，多余参数进 extra（防回归到"不过滤"）"""
    def f(a, b=2):
        return a

    safe, extra = filter_tool_args(f, {"a": 1, "b": 3, "zzz": 9})
    assert safe == {"a": 1, "b": 3}
    assert extra == {"zzz"}


def test_filter_passthrough_when_signature_unavailable():
    """T1c 签名不可解析时全量透传（宁可多传不可少传）"""
    safe, extra = filter_tool_args(42, {"a": 1})  # 42 不是可调用对象
    assert safe == {"a": 1}
    assert extra == set()


def test_old_naive_filter_would_drop_args():
    """T1d 固化缺陷判据本身：旧写法对 **kwargs 工具必然丢光参数。

    本测试不依赖生产代码，只证明"缺陷是真的"，防止有人误以为
    原逻辑本来就能工作而把修复回退掉。
    """
    async def wrapper(**kwargs):
        return "x"

    old_valid_params = set(inspect.signature(wrapper).parameters)
    assert old_valid_params == {"kwargs"}, "前提不成立：签名形状变了"
    old_safe = {k: v for k, v in {"text": "hi"}.items() if k in old_valid_params}
    assert old_safe == {}, "旧写法未复现缺陷"

    new_safe, _ = filter_tool_args(wrapper, {"text": "hi"})
    assert new_safe == {"text": "hi"}


# ═══════════════ T2：由 inputSchema 派生真实签名 ═══════════════

def test_signature_from_schema_yields_real_params():
    """T2a 派生签名应暴露真实参数名、required 无默认值、可选默认 None"""
    sig = _build_signature_from_schema(COMPUTER_SCHEMA)
    assert sig is not None
    params = sig.parameters
    assert list(params) == ["text", "interval"]
    assert params["text"].default is inspect.Parameter.empty
    assert params["interval"].default is None
    assert params["text"].annotation is str
    assert params["interval"].annotation is float


def test_signature_from_schema_empty_properties_is_valid():
    """T2b 无参工具（properties 为空）应得到空签名，而非 None"""
    sig = _build_signature_from_schema({"type": "object", "properties": {}})
    assert sig is not None
    assert list(sig.parameters) == []


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"properties": None},
        {"properties": {"class": {"type": "string"}}},   # Python 关键字
        {"properties": {"a-b": {"type": "string"}}},     # 非标识符
        {"properties": {"1x": {"type": "string"}}},      # 非标识符
    ],
)
def test_signature_from_schema_returns_none_for_bad_input(bad):
    """T2c 畸形 schema 必须返回 None（退回 **kwargs + 全量透传兜底）"""
    assert _build_signature_from_schema(bad) is None


# ═══════════ T3：invoke_tool 的真实派发（决定性回归） ═══════════

def test_invoke_tool_awaits_async_wrapper(monkeypatch):
    """T3a 【决定性】MCP 风格 async **kwargs 包装函数：
    必须收到真实参数，且返回值是**已 await 的文本**而不是协程对象。

    这正是红队实测崩溃的那条路径：旧代码返回
    ``参数错误：unsupported operand type(s) for +=: 'coroutine' and 'str'``。
    """
    received = {}

    async def fake_wrapper(**kwargs):
        received.update(kwargs)
        return "OK:typed"

    monkeypatch.setitem(TOOL_MAP, "mcp__computer__type_text", fake_wrapper)

    result, extra = asyncio.run(invoke_tool("mcp__computer__type_text", {"text": "hi"}))
    assert received == {"text": "hi"}, f"参数被丢弃了: {received}"
    assert result == "OK:typed"
    assert "coroutine" not in result
    assert "参数错误" not in result
    assert extra == set()


def test_invoke_tool_with_signature_bearing_wrapper(monkeypatch):
    """T3b 挂上 __signature__ 的真实 MCP 包装函数：
    真实参数保留，**幻觉参数仍被识别**（证明修复没把过滤一起废掉）。"""
    received = {}

    async def fake_wrapper(**kwargs):
        received.update(kwargs)
        return "OK"

    sig = _build_signature_from_schema(COMPUTER_SCHEMA)
    assert sig is not None
    fake_wrapper.__signature__ = sig
    monkeypatch.setitem(TOOL_MAP, "mcp__computer__type_text", fake_wrapper)

    result, extra = asyncio.run(
        invoke_tool("mcp__computer__type_text", {"text": "hi", "bogus": 1})
    )
    assert received == {"text": "hi"}, received
    assert extra == {"bogus"}, f"幻觉参数未被识别: {extra}"
    assert result == "OK"


def test_invoke_tool_supports_sync_tools(monkeypatch):
    """T3c 同步工具仍能正常工作（不能只顾异步把同步弄坏）"""
    def sync_tool(a, b=1):
        return f"{a}-{b}"

    monkeypatch.setitem(TOOL_MAP, "__t_sync__", sync_tool)
    result, extra = asyncio.run(invoke_tool("__t_sync__", {"a": "x", "zzz": 1}))
    assert result == "x-1"
    assert extra == {"zzz"}


def test_invoke_tool_sync_in_thread(monkeypatch):
    """T3d sync_in_thread=True 时同步工具走线程池（agent.py 的既有语义）"""
    import threading

    def sync_tool():
        return threading.current_thread().name

    monkeypatch.setitem(TOOL_MAP, "__t_thread__", sync_tool)
    result, _ = asyncio.run(invoke_tool("__t_thread__", {}, sync_in_thread=True))
    assert result != "MainThread", f"未走线程池，当前线程 {result}"


def test_invoke_tool_unknown_name_raises_keyerror():
    """T3e 未知工具抛 KeyError，由调用方保留各自文案"""
    with pytest.raises(KeyError):
        asyncio.run(invoke_tool("__no_such_tool__", {}))


def test_invoke_tool_always_returns_str(monkeypatch):
    """T3f 返回值必须是 str（调用方会直接做 += 拼接）"""
    def returns_dict():
        return {"width": 2560, "height": 1440}

    monkeypatch.setitem(TOOL_MAP, "__t_dict__", returns_dict)
    result, _ = asyncio.run(invoke_tool("__t_dict__", {}))
    assert isinstance(result, str)
    assert "2560" in result


# ═══════════ T4：静态锁——不许再出现第 4 份手写过滤 ═══════════

def _find_naive_arg_filters(src: str):
    """AST 检测旧写法：先 ``X = set(inspect.signature(f).parameters)``，
    再用 ``{k: v for k, v in ... if k in X}`` 过滤。

    必须走 AST 而非文本匹配 —— 文本匹配会命中修复时留下的**注释里引用的旧代码**
    （已实测误报一次），也会命中文档字符串。
    """
    tree = ast.parse(src)

    # 1. 收集"由 inspect.signature(...).parameters 派生的 set"变量名
    sig_vars = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if not (
            isinstance(val, ast.Call)
            and isinstance(val.func, ast.Name)
            and val.func.id == "set"
            and val.args
        ):
            continue
        arg = val.args[0]
        if not (isinstance(arg, ast.Attribute) and arg.attr == "parameters"):
            continue
        inner = arg.value
        if not (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "signature"
        ):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                sig_vars.add(tgt.id)

    if not sig_vars:
        return []

    # 2. 找按这些名字过滤的推导式
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.DictComp, ast.SetComp, ast.ListComp, ast.GeneratorExp)):
            continue
        for gen in node.generators:
            for cond in gen.ifs:
                if (
                    isinstance(cond, ast.Compare)
                    and len(cond.ops) == 1
                    and isinstance(cond.ops[0], ast.In)
                    and isinstance(cond.comparators[0], ast.Name)
                    and cond.comparators[0].id in sig_vars
                ):
                    hits.append(node.lineno)
    return hits


def test_no_duplicate_naive_arg_filter_remains():
    """T4a 三个派发点不得再出现手写的 ``if k in valid_params`` 过滤。

    判据走 AST（见 ``_find_naive_arg_filters``），因此修复注释里引用的旧代码
    不会造成误报。
    """
    offenders = []
    for path in DISPATCH_FILES:
        hits = _find_naive_arg_filters(path.read_text(encoding="utf-8"))
        if hits:
            offenders.append(f"{path.relative_to(ROOT)}:{hits}")
    assert not offenders, f"仍存在手写的参数过滤，应改用 filter_tool_args: {offenders}"


def test_scanner_detects_known_bad_sample():
    """T4a' 自校验：检测器必须能抓到已知的坏样本（否则 T4a 是空转）"""
    bad = (
        "import inspect\n"
        "def go(fn, args):\n"
        "    valid_params = set(inspect.signature(fn).parameters)\n"
        "    return {k: v for k, v in args.items() if k in valid_params}\n"
    )
    assert _find_naive_arg_filters(bad), "检测器漏掉了已知坏样本"

    # 且不能把"注释里引用的旧代码"当成违规
    commented = (
        "# valid_params = set(inspect.signature(fn).parameters)\n"
        "# {k: v for k, v in args.items() if k in valid_params}\n"
        "x = 1\n"
    )
    assert not _find_naive_arg_filters(commented), "检测器把注释误判为违规"


def test_tui_dispatch_awaits_via_invoke_tool():
    """T4b TUI 主对话派发点必须走 invoke_tool（含 await）"""
    src = read_tui_app_sources()      # app.py + 8 个 app_*.py（派发点在 app_turn_loop.py）
    assert "await invoke_tool(" in src, "TUI 派发点未走统一入口 invoke_tool"
    assert "result, extra = await invoke_tool(name, args)" in src


def test_dispatch_files_import_unified_entry():
    """T4c agent.py / parallel_tools.py 必须导入 filter_tool_args"""
    for rel in ("zeroai/core/agent.py", "zeroai/core/parallel_tools.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "filter_tool_args" in src, f"{rel} 未使用统一入口"
        assert "filter_tool_args(" in src, f"{rel} 未实际调用统一入口"


def test_mcp_registry_attaches_signature():
    """T4d MCP 注册器必须把 inputSchema 派生的签名挂到包装函数上"""
    src = (ROOT / "zeroai" / "mcp" / "registry.py").read_text(encoding="utf-8")
    assert "_wrapper.__signature__" in src
    assert "tool.inputSchema" in src, "调用处未把 inputSchema 传给包装函数工厂"


def test_registry_exports_unified_entry():
    """T4e 统一入口必须出现在 __all__（对外契约）"""
    import zeroai.tools.registry as reg

    assert "filter_tool_args" in reg.__all__
    assert "invoke_tool" in reg.__all__
