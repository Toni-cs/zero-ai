"""代理模式回归测试 —— 防止"绕过客户端工厂"的缺陷再次出现。

背景（2026-09-16 审计发现）
--------------------------------------------------------------------------
`zeroai/core/secrets.py` 的 `_make_openai_client` / `_make_openai_sync_client`
是"代理 or 直连"这一决策的**唯一实现**。llm.py 与 model_manager.py 早已改为
调用工厂（其 docstring 明确写着"不要在此处直接 OpenAI(...)"）。

但审计发现 TUI 层仍有直接构造：
  * `zeroai/tui/app.py` ZeroAI.get_current_client()      —— 无调用点（潜在陷阱）
  * `zeroai/tui/screens.py` _async_generate_ai()          —— **线上可达**（语音对话框）
实测后果：代理开启 + 本地无真实 Key 时，`get_current_client()` 抛
`OpenAIError: Missing credentials`。

判据（三层）
--------------------------------------------------------------------------
L1 函数体内出现 OpenAI(...) / AsyncOpenAI(...) 构造调用
L2 同一函数体内**也**调用了 _make_openai_client / _make_openai_sync_client
   -> 工厂是主路径，构造只是 `if not _is_proxy_enabled():` 的本地模式覆盖，**合法**
L3 未调工厂且未守卫 -> **真绕过**，测试失败
"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from tui_sources import tui_app_paths  # noqa: E402

FACTORY_NAMES = {"_make_openai_client", "_make_openai_sync_client"}
CTOR_NAMES = {"OpenAI", "AsyncOpenAI"}
GUARD_NAME = "_is_proxy_enabled"

# 这些模块**就是**工厂本身的所在地，直接构造是它们的职责
FACTORY_MODULES = {
    ROOT / "zeroai" / "core" / "secrets.py",
}

SCAN_TARGETS = [
    # TUI 侧 2026-10-06 由单个 app.py 拆成 app.py + 8 个 app_*.py；
    # 构造调用在 app_turn_loop/app_turn_multi，漏一个文件就等于该文件不再受检。
    *tui_app_paths(),
    ROOT / "zeroai" / "tui" / "screens.py",
    ROOT / "zeroai" / "memory" / "vector_store.py",
    ROOT / "zeroai" / "core" / "llm.py",
    ROOT / "zeroai" / "core" / "model_manager.py",
]


def _call_name(node: ast.Call):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _enclosing_func(funcs, line):
    best = None
    for f in funcs:
        end = f.end_lineno or f.lineno
        if f.lineno <= line <= end:
            if best is None or (end - f.lineno) < ((best.end_lineno or best.lineno) - best.lineno):
                best = f
    return best


def _find_bypasses(path: Path):
    """返回 [(lineno, func_name)] —— 真绕过清单"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and _call_name(n) in CTOR_NAMES):
            continue
        host = _enclosing_func(funcs, n.lineno)
        if host is None:
            # 模块级构造：一律视为绕过（无法用函数内守卫解释）
            out.append((n.lineno, "<module>"))
            continue
        names = {x.id for x in ast.walk(host) if isinstance(x, ast.Name)}
        if names & FACTORY_NAMES:
            continue  # L2：工厂是主路径，构造是本地模式覆盖 -> 合法
        if GUARD_NAME in names:
            continue  # L3：被代理守卫
        out.append((n.lineno, host.name))
    return out


# ────────────────────────── T1：静态扫描 ──────────────────────────

def test_no_unguarded_direct_client_construction():
    """T1 生产模块中不得存在绕过工厂的客户端构造"""
    offenders = []
    for p in SCAN_TARGETS:
        if not p.exists() or p in FACTORY_MODULES:
            continue
        for lineno, fname in _find_bypasses(p):
            offenders.append(f"{p.relative_to(ROOT)}:{lineno} in {fname}()")
    assert not offenders, (
        "以下位置直接构造 OpenAI/AsyncOpenAI 且未调用 secrets 工厂，"
        "代理模式会在这些路径上失效：\n  " + "\n  ".join(offenders)
    )


def test_factory_modules_are_excluded_correctly():
    """T1b 工厂模块自身被正确排除（防止扫描器把工厂本体误报）"""
    assert (ROOT / "zeroai" / "core" / "secrets.py") in FACTORY_MODULES
    assert not _find_bypasses(ROOT / "zeroai" / "core" / "secrets.py")


def test_scanner_detects_known_bad_sample(tmp_path):
    """T1c 扫描器对**已知坏样本**必须报警（否则是空转的假测试）"""
    bad = tmp_path / "bad_mod.py"
    bad.write_text(
        "from openai import OpenAI\n"
        "def make(cfg):\n"
        "    return OpenAI(base_url=cfg['base_url'], api_key=cfg['api_key'])\n",
        encoding="utf-8",
    )
    assert _find_bypasses(bad), "扫描器未能识别直连构造 —— 判据失效"


def test_scanner_accepts_guarded_local_override(tmp_path):
    """T1d 扫描器必须接受"工厂 + 本地模式覆盖"这一合法模式"""
    good = tmp_path / "good_mod.py"
    good.write_text(
        "from openai import AsyncOpenAI\n"
        "from zeroai.core.secrets import _make_openai_client, _is_proxy_enabled\n"
        "def make(cfg):\n"
        "    c = _make_openai_client('glm')\n"
        "    if not _is_proxy_enabled():\n"
        "        c = AsyncOpenAI(base_url=cfg['base_url'], api_key=cfg['api_key'])\n"
        "    return c\n",
        encoding="utf-8",
    )
    assert not _find_bypasses(good), "扫描器误报合法的本地模式覆盖"


# ────────────────────────── T2：行为验证 ──────────────────────────

@pytest.fixture()
def proxy_env(monkeypatch):
    """把环境设成"代理已启用 + 本地无真实 Key"（代理模式的典型场景）"""
    from zeroai.core import secrets as S
    from zeroai.core.constants import MODEL_CONFIGS

    monkeypatch.setattr(
        S, "PROXY_CONFIG",
        {"enabled": True, "base_url": "http://127.0.0.1:8899", "token": "tok-xyz"},
    )
    monkeypatch.setitem(MODEL_CONFIGS["glm"], "api_key", "")
    return S, MODEL_CONFIGS


def test_get_current_client_honors_proxy(proxy_env):
    """T2 代理启用时 get_current_client 必须走代理（此前直连并抛 OpenAIError）"""
    from zeroai.tui.app import ZeroAI

    app = SimpleNamespace(model_key="glm")
    client = ZeroAI.get_current_client(app)
    assert "127.0.0.1:8899" in str(client.base_url), (
        f"未走代理，base_url={client.base_url}"
    )
    assert client.api_key == "tok-xyz"


def test_voice_dialog_client_honors_proxy(proxy_env):
    """T3 语音对话框的客户端构造必须经工厂（此前内联直连，线上可达）"""
    src = (ROOT / "zeroai" / "tui" / "screens.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    host = None
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_generate_ai":
            host = n
            break
    assert host is not None, "未找到 _async_generate_ai"
    names = {x.id for x in ast.walk(host) if isinstance(x, ast.Name)}
    assert "_make_openai_sync_client" in names, (
        "_async_generate_ai 未调用 secrets 工厂 —— 代理模式在语音对话框失效"
    )
    assert GUARD_NAME in names, "_async_generate_ai 缺少代理守卫"


def test_local_mode_still_uses_upstream(monkeypatch):
    """T4 代理未启用时必须仍走上游（防止修复引入"永远走代理"的回归）"""
    from zeroai.core import secrets as S
    from zeroai.core.constants import MODEL_CONFIGS
    from zeroai.tui.app import ZeroAI

    monkeypatch.setattr(S, "PROXY_CONFIG", {"enabled": False, "base_url": "", "token": ""})
    monkeypatch.setitem(MODEL_CONFIGS["glm"], "api_key", "real-key")
    app = SimpleNamespace(model_key="glm")
    client = ZeroAI.get_current_client(app)
    assert "127.0.0.1:8899" not in str(client.base_url)
    assert client.api_key == "real-key"
