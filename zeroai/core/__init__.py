"""Core modules for ZeroAI

导出关键类和函数，便于上层调用：
    from zeroai.core import HybridExpertSystem, cleanup_and_compress, get_hybrid_system

本包包含两组模块：
1. 原有模块（config / expert / llm / context）：基于类的面向对象封装
2. 迁移模块（paths / runtime / constants / secrets / expert_route /
   context_compress / model_manager / response_utils）：从 tui_agent.py
   提取的纯函数版本，保持与原文件函数签名和行为完全一致。

迁移模块可通过子模块命名空间访问，避免与原有模块的同名符号冲突：
    from zeroai.core import expert_route, context_compress
    expert_route.route_expert(user_input)
    context_compress.compress_context(messages, limit)

各迁移模块的典型导出：
- paths：_get_desktop_dir, _resolve_save_path, _find_resource_dir, CONFIG_FILE
- runtime：RuntimeCache, runtime_cache, _is_stopped, _interruptible_await
- secrets：_obfuscate, _deobfuscate, _load_config, _get_api_key, _make_openai_client
- constants：MODEL_CONFIGS, EXPERT_TEAM, WORK_MODE, HYBRID_* 参数
- expert_route：route_expert, route_expert_glm, get_expert_config, LRUCache
- context_compress：cleanup_context, compress_context, cleanup_and_compress
- model_manager：get_client, get_model_name, get_model_label, detect_ollama_models
- response_utils：_strip_model_tokens, _parse_think_tags, _jaccard_similarity,
  _truncate_expert_response, _sanitize_identity_leak

启动性能（2026-09-11 实测优化）：
本包 __init__ 曾顶层 eager 导入全部子模块（expert→llm→openai 链 ~0.75s、
agent ~35ms、model_manager 的 OpenAI() 构造 ~0.95s），导致
`import zeroai` / `--version` / `--check` 全部连坐。现改为 PEP 562 惰性
导出：__all__ 与 `from zeroai.core import X` 语义完全不变，符号在首次
访问时才导入对应模块。子模块（paths/secrets/constants/...）同样惰性。
"""
# 轻量子模块保留 eager（yaml ~18ms，且 zeroai/__init__ 的 init() 需要）
from .config import get_config, load_config

# ====== 惰性导出映射（PEP 562）：symbol -> 所在子模块名 ======
_LAZY_SYMBOL_MAP = {}

for _sym in (
    "ExpertRouter", "HybridExpertSystem", "LRUCache",
    "get_expert_router", "get_hybrid_system",
    "route_expert", "route_expert_async",
    "jaccard_similarity", "truncate_expert_response",
):
    _LAZY_SYMBOL_MAP[_sym] = "expert"

for _sym in ("LLMClient", "MultiModelClient", "get_multi_model_client"):
    _LAZY_SYMBOL_MAP[_sym] = "llm"

for _sym in (
    "ContextManager", "get_context_manager", "create_context_manager",
    "cleanup_context", "compress_context", "cleanup_and_compress",
    "estimate_tokens", "get_model_context_limit",
):
    _LAZY_SYMBOL_MAP[_sym] = "context"

for _sym in (
    "ReActPlanner", "AgentLoop", "PLANNER_SYSTEM_PROMPT",
    "get_agent_loop", "reset_agent_loop",
    "Thought", "Plan",
    "ReflexionEngine", "ToolResultSummarizer", "PlanAndExecutePlanner",
    "AdvancedAgentLoop",
    "REFLECTION_SYSTEM_PROMPT", "SUMMARIZER_SYSTEM_PROMPT",
    "PLANNER_PLAN_SYSTEM_PROMPT",
    "get_advanced_agent_loop", "reset_advanced_agent_loop",
    "AgentRole", "MultiAgentCollaborator",
):
    _LAZY_SYMBOL_MAP[_sym] = "agent"

for _sym in ("CodeSafetyChecker", "CodeSandbox", "check_code_safety"):
    _LAZY_SYMBOL_MAP[_sym] = "sandbox"

for _sym in ("AgentMessage", "MessageBus", "Blackboard",
             "get_message_bus", "get_blackboard"):
    _LAZY_SYMBOL_MAP[_sym] = "agent_bus"

for _sym in ("RoleNode", "CollaborationContext", "RoleDependencyGraph",
             "EnhancedMultiAgentCollaborator"):
    _LAZY_SYMBOL_MAP[_sym] = "dynamic_roles"

for _sym in (
    "ThoughtChunk", "StreamingThoughtEmitter", "InterruptionHandler",
    "ToolCallProgress", "ProgressTracker",
    "get_streaming_emitter", "get_interrupt_handler", "get_progress_tracker",
    "reset_streaming",
):
    _LAZY_SYMBOL_MAP[_sym] = "streaming"

for _sym in ("CodeNode", "CodeEdge", "CodeKnowledgeGraph",
             "get_code_knowledge_graph", "reset_code_knowledge_graph"):
    _LAZY_SYMBOL_MAP[_sym] = "code_knowledge_graph"

for _sym in (
    "ToolCallRequest", "ToolCallResult", "ToolDependencyGraph",
    "ResultMerger", "ParallelToolScheduler",
    "get_parallel_scheduler", "reset_parallel_scheduler",
):
    _LAZY_SYMBOL_MAP[_sym] = "parallel_tools"

for _sym in (
    "VectorCompressor", "CacheStats", "UnifiedCacheManager",
    "FileIndexEntry", "IncrementalIndexer", "ContextBudgetAllocator",
    "get_unified_cache_manager", "get_incremental_indexer",
    "reset_memory_optimizers",
):
    _LAZY_SYMBOL_MAP[_sym] = "memory_optimizer"

for _sym in (
    "_get_desktop_dir", "_resolve_save_path", "_find_resource_dir",
    "_ensure_user_dir", "_get_resource_dir", "CONFIG_FILE", "CUSTOM_MODELS_FILE",
):
    _LAZY_SYMBOL_MAP[_sym] = "paths"

for _sym in (
    "RuntimeCache", "runtime_cache", "_is_stopped", "_set_stop_flag",
    "_interruptible_await", "_interruptible_sleep",
):
    _LAZY_SYMBOL_MAP[_sym] = "runtime"

for _sym in (
    "_obfuscate", "_deobfuscate", "_load_config", "_save_config",
    "_get_api_key", "_load_proxy_config", "_save_proxy_config",
    "_is_proxy_enabled", "_make_openai_client", "PROXY_CONFIG",
):
    _LAZY_SYMBOL_MAP[_sym] = "secrets"

for _sym in (
    "MODEL_CONFIGS", "EXPERT_TEAM", "WORK_MODE", "OR_BASE", "OR_KEY",
    "HYBRID_MAX_PARALLEL_EXPERTS", "HYBRID_EXPERT_MAX_CHARS",
    "HYBRID_DEDUP_SIMILARITY_THRESHOLD", "EXPERT_MEMORY_TURNS",
    "HYBRID_ENABLE_COLLAB_CHAIN", "CHARS_PER_TOKEN",
    "COMPRESS_THRESHOLD_RATIO", "KEEP_RECENT_TURNS",
    "CLEANUP_THRESHOLD_RATIO", "CLEANUP_KEEP_RECENT_TURNS",
    "TOOL_OUTPUT_SUMMARY_MAX_LEN", "PERMISSION_LEVEL", "MAX_FILE_SIZE",
    "set_work_mode",
):
    _LAZY_SYMBOL_MAP[_sym] = "constants"

for _sym in (
    "route_expert_glm", "get_expert_config", "_expert_route_cache",
    "_is_openrouter_expert", "_check_openrouter_circuit_breaker",
    "_record_openrouter_failure", "_record_openrouter_success",
):
    _LAZY_SYMBOL_MAP[_sym] = "expert_route"

for _sym in (
    "_summarize_tool_output", "_estimate_tokens", "_get_model_context_limit",
    "_truncate_messages_for_context", "_model_supports_vision",
    "_filter_messages_for_model", "_split_messages_for_compress",
):
    _LAZY_SYMBOL_MAP[_sym] = "context_compress"

for _sym in (
    "get_active_model_info", "detect_ollama_models", "get_model_display_name",
    "get_client", "get_model_name", "get_model_label", "set_current_model_key",
    "CURRENT_MODEL_KEY", "BUILTIN_MODEL_KEYS",
):
    _LAZY_SYMBOL_MAP[_sym] = "model_manager"

for _sym in (
    "_strip_model_tokens", "_parse_think_tags", "_jaccard_similarity",
    "_truncate_expert_response", "_sanitize_identity_leak",
):
    _LAZY_SYMBOL_MAP[_sym] = "response_utils"

# 以子模块形式导出的迁移模块（按命名空间访问同名符号）
_LAZY_SUBMODULES = (
    "paths", "runtime", "secrets", "constants",
    "expert_route", "context_compress", "model_manager", "response_utils",
    "agent", "expert", "llm", "context", "sandbox", "agent_bus",
    "dynamic_roles", "streaming", "code_knowledge_graph",
    "parallel_tools", "memory_optimizer",
)


def __getattr__(name: str):
    import importlib

    if name in _LAZY_SYMBOL_MAP:
        mod = importlib.import_module(f"{__name__}.{_LAZY_SYMBOL_MAP[name]}")
        val = getattr(mod, name)
        globals()[name] = val  # 缓存，后续访问不再走 __getattr__
        return val

    if name in _LAZY_SUBMODULES:
        mod = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = mod
        return mod

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_SYMBOL_MAP) | set(_LAZY_SUBMODULES))


__all__ = [
    # 原有模块
    "get_config", "load_config",
    "ExpertRouter", "HybridExpertSystem", "LRUCache",
    "get_expert_router", "get_hybrid_system",
    "route_expert", "route_expert_async",
    "jaccard_similarity", "truncate_expert_response",
    "LLMClient", "MultiModelClient", "get_multi_model_client",
    "ContextManager", "get_context_manager", "create_context_manager",
    "cleanup_context", "compress_context", "cleanup_and_compress",
    "estimate_tokens", "get_model_context_limit",
    # 迁移模块（命名空间）
    "paths", "runtime", "secrets", "constants",
    "expert_route", "context_compress", "model_manager", "response_utils",
    # 路径
    "_get_desktop_dir", "_resolve_save_path", "_find_resource_dir",
    "_ensure_user_dir", "_get_resource_dir", "CONFIG_FILE", "CUSTOM_MODELS_FILE",
    # 运行时
    "RuntimeCache", "runtime_cache", "_is_stopped", "_set_stop_flag",
    "_interruptible_await", "_interruptible_sleep",
    # 密钥
    "_obfuscate", "_deobfuscate", "_load_config", "_save_config",
    "_get_api_key", "_load_proxy_config", "_save_proxy_config",
    "_is_proxy_enabled", "_make_openai_client", "PROXY_CONFIG",
    # 常量
    "MODEL_CONFIGS", "EXPERT_TEAM", "WORK_MODE", "OR_BASE", "OR_KEY",
    "HYBRID_MAX_PARALLEL_EXPERTS", "HYBRID_EXPERT_MAX_CHARS",
    "HYBRID_DEDUP_SIMILARITY_THRESHOLD", "EXPERT_MEMORY_TURNS",
    "HYBRID_ENABLE_COLLAB_CHAIN", "CHARS_PER_TOKEN",
    "COMPRESS_THRESHOLD_RATIO", "KEEP_RECENT_TURNS",
    "CLEANUP_THRESHOLD_RATIO", "CLEANUP_KEEP_RECENT_TURNS",
    "TOOL_OUTPUT_SUMMARY_MAX_LEN", "PERMISSION_LEVEL", "MAX_FILE_SIZE",
    "set_work_mode",
    # 专家路由
    "route_expert_glm", "get_expert_config", "_expert_route_cache",
    "_is_openrouter_expert", "_check_openrouter_circuit_breaker",
    "_record_openrouter_failure", "_record_openrouter_success",
    # 上下文压缩
    "_summarize_tool_output", "_estimate_tokens", "_get_model_context_limit",
    "_truncate_messages_for_context", "_model_supports_vision",
    "_filter_messages_for_model", "_split_messages_for_compress",
    # 模型管理
    "get_active_model_info", "detect_ollama_models", "get_model_display_name",
    "get_client", "get_model_name", "get_model_label", "set_current_model_key",
    "CURRENT_MODEL_KEY", "BUILTIN_MODEL_KEYS",
    # 响应处理
    "_strip_model_tokens", "_parse_think_tags", "_jaccard_similarity",
    "_truncate_expert_response", "_sanitize_identity_leak",
    # ReAct Agent
    "ReActPlanner", "AgentLoop", "PLANNER_SYSTEM_PROMPT",
    "get_agent_loop", "reset_agent_loop",
    # 阶段 1 增强
    "Thought", "Plan",
    "ReflexionEngine", "ToolResultSummarizer", "PlanAndExecutePlanner",
    "AdvancedAgentLoop",
    "REFLECTION_SYSTEM_PROMPT", "SUMMARIZER_SYSTEM_PROMPT",
    "PLANNER_PLAN_SYSTEM_PROMPT",
    "get_advanced_agent_loop", "reset_advanced_agent_loop",
    # 阶段 B.4 多 Agent 协作
    "AgentRole", "MultiAgentCollaborator",
    # 阶段 N：代码执行沙箱
    "CodeSafetyChecker", "CodeSandbox", "check_code_safety",
    # 阶段 O：多 Agent 协作增强
    "AgentMessage", "MessageBus", "Blackboard",
    "get_message_bus", "get_blackboard",
    "RoleNode", "CollaborationContext", "RoleDependencyGraph",
    "EnhancedMultiAgentCollaborator",
    # 阶段 P：流式思维链 + 中断响应 + 进度跟踪
    "ThoughtChunk", "StreamingThoughtEmitter", "InterruptionHandler",
    "ToolCallProgress", "ProgressTracker",
    "get_streaming_emitter", "get_interrupt_handler", "get_progress_tracker",
    "reset_streaming",
    # 阶段 Q：项目代码知识图谱
    "CodeNode", "CodeEdge", "CodeKnowledgeGraph",
    "get_code_knowledge_graph", "reset_code_knowledge_graph",
    # 阶段 S：工具调用并行化
    "ToolCallRequest", "ToolCallResult", "ToolDependencyGraph",
    "ResultMerger", "ParallelToolScheduler",
    "get_parallel_scheduler", "reset_parallel_scheduler",
    # 阶段 T：内存与性能优化
    "VectorCompressor", "CacheStats", "UnifiedCacheManager",
    "FileIndexEntry", "IncrementalIndexer", "ContextBudgetAllocator",
    "get_unified_cache_manager", "get_incremental_indexer", "reset_memory_optimizers",
]
