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


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        import importlib
        mod_name, attr = _LAZY_EXPORTS[name]
        val = getattr(importlib.import_module(mod_name), attr)
        globals()[name] = val  # 缓存，后续访问不再走 __getattr__
        return val
    # MCP 模块（阶段 3）：延迟导入，避免启动时连接 MCP 服务器
    if name == "mcp":
        from . import mcp as _mcp
        globals()["mcp"] = _mcp
        return _mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | {"mcp"})


__version__ = "1.1.4"
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
