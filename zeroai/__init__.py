"""ZeroAI - Terminal AI Assistant for Research & Engineering

模块清单：
- core：核心引擎（LLM 客户端、专家路由、上下文管理、Agent Loop）
- tools：工具函数集（文件/命令/网络/文档/安全/SSH/语音等）
- memory：向量记忆/RAG（语义检索、对话记忆、文件监视）
- mcp：Model Context Protocol（Client/Server 双向支持）
- tui：终端 UI（基于 rich/components）
- utils：通用工具

启动性能（2026-09-11 实测优化）：
本包 __init__ 曾顶层导入 expert/llm/context/tools.base/mcp，实测把
`import zeroai` 拖到 ~1.95s（openai SDK ~0.7s + OpenAI() 构造 ~0.95s）。
现改为 PEP 562 惰性导出：`import zeroai` 只加载轻量 config/paths/secrets
（~30ms），以下符号在首次访问时才导入，`from zeroai import X` 语义不变：
- expert 系列 / LLMClient / context 系列 / tools.base 系列 / mcp 子包
"""
import sys
from pathlib import Path

# Add package to path if needed
package_dir = Path(__file__).parent
if str(package_dir.parent) not in sys.path:
    sys.path.insert(0, str(package_dir.parent))

# 轻量导入：config.yaml 加载（yaml ~18ms），保证 init() 可用
from .core.config import get_config, load_config
from .utils.platform import is_windows, is_linux, is_macos, get_platform

# ---- 惰性导出映射（PEP 562）：symbol -> (module, attr) ----
_LAZY_EXPORTS = {
    "get_expert_router": ("zeroai.core.expert", "get_expert_router"),
    "get_hybrid_system": ("zeroai.core.expert", "get_hybrid_system"),
    "route_expert": ("zeroai.core.expert", "route_expert"),
    "route_expert_async": ("zeroai.core.expert", "route_expert_async"),
    "ExpertRouter": ("zeroai.core.expert", "ExpertRouter"),
    "HybridExpertSystem": ("zeroai.core.expert", "HybridExpertSystem"),
    "LLMClient": ("zeroai.core.llm", "LLMClient"),
    "get_multi_model_client": ("zeroai.core.llm", "get_multi_model_client"),
    "get_context_manager": ("zeroai.core.context", "get_context_manager"),
    "cleanup_context": ("zeroai.core.context", "cleanup_context"),
    "compress_context": ("zeroai.core.context", "compress_context"),
    "cleanup_and_compress": ("zeroai.core.context", "cleanup_and_compress"),
    "estimate_tokens": ("zeroai.core.context", "estimate_tokens"),
    "get_model_context_limit": ("zeroai.core.context", "get_model_context_limit"),
    "Tool": ("zeroai.tools.base", "Tool"),
    "ToolRegistry": ("zeroai.tools.base", "ToolRegistry"),
    "ToolError": ("zeroai.tools.base", "ToolError"),
    "ToolExecutionError": ("zeroai.tools.base", "ToolExecutionError"),
    "ToolValidationError": ("zeroai.tools.base", "ToolValidationError"),
}

# 惰性子模块（PEP 562）：`zeroai.memory` / `zeroai.tui` 等首次访问时才 import，
# 避免 `import zeroai` 就把 Textual / numpy 全家拖起来。
_LAZY_SUBMODULES = ("core", "memory", "mcp", "tools", "tui", "utils")


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        import importlib
        mod_name, attr = _LAZY_EXPORTS[name]
        val = getattr(importlib.import_module(mod_name), attr)
        globals()[name] = val  # 缓存，后续访问不再走 __getattr__
        return val
    # 子模块惰性解析（core/memory/mcp/tools/tui/utils）。
    #
    # 必须用 importlib.import_module，不能用 `from . import X`：后者内部的
    # _handle_fromlist 会先做 hasattr(本模块, X) 探测，而该属性此刻尚未写入
    # globals，于是又绕回本 __getattr__，形成无限递归（实测
    # `hasattr(zeroai, "mcp")` 直接抛 RecursionError）。
    #
    # 注意 core / utils 已在文件顶部被 config/platform 连带导入，因此
    # 它们的属性访问不会走到这里；其余子模块首次访问时才真正 import。
    if name in _LAZY_SUBMODULES:
        import importlib
        mod = importlib.import_module(__name__ + "." + name)
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | set(_LAZY_SUBMODULES))


__version__ = "1.1.6"
__author__ = "ZeroAI"


def init(config_path: str = None):
    """Initialize ZeroAI with configuration"""
    if config_path:
        load_config(config_path)
    else:
        # Try to find config.yaml in package directory
        config_file = package_dir / "config.yaml"
        if config_file.exists():
            load_config(str(config_file))


def get_version() -> str:
    """Get ZeroAI version"""
    return __version__


# Auto-initialize on import
if not get_config()._config:
    init()
