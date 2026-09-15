"""配置持久化与密钥管理

迁移来源：tui_agent.py 行 358-524

提供：
- _obfuscate / _deobfuscate：base64 混淆/解混淆（非加密，仅防明文泄露）
- _load_config / _save_config：配置文件读写（API Key 混淆存储）
- _get_api_key：按优先级获取 API Key（环境变量 > 配置文件 > 内置默认值）
- _BUILTIN_PROXY / _load_proxy_config / _save_proxy_config / PROXY_CONFIG / _is_proxy_enabled：代理配置
- _make_openai_client：统一的 AsyncOpenAI 客户端工厂（代理/本地双模式）

依赖关系：
- 本模块依赖 paths.py（CONFIG_FILE）
- _make_openai_client 在函数体内延迟导入 constants.MODEL_CONFIGS，以避免循环依赖

注意：本模块不导入 constants.py 的任何模块级符号，确保被 constants.py 导入时不产生循环。
"""
import os
import json
import base64

# 注意：不要在此模块顶层 import openai —— 实测它会把 openai+httpx 全家
# (~0.7s) 拖进所有 import 路径（constants→secrets 链路）。AsyncOpenAI 在
# _make_openai_client 内部按需导入。

from .paths import CONFIG_FILE


def _obfuscate(text: str) -> str:
    """简单混淆（base64），防止明文泄露，不是加密"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _deobfuscate(text: str) -> str:
    """反混淆"""
    try:
        return base64.b64decode(text.encode("ascii")).decode("utf-8")
    except Exception:
        return text


def _load_config() -> dict:
    """加载配置文件（存储 API Key 等敏感信息）"""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            # 反混淆 api_key
            for key in data:
                if "api_key" in data[key] and data[key]["api_key"]:
                    data[key]["api_key"] = _deobfuscate(data[key]["api_key"])
            return data
        except Exception:
            pass
    return {}


def _save_config(data: dict):
    """保存配置文件（API Key 混淆后存储）"""
    try:
        safe = {}
        for key, cfg in data.items():
            safe[key] = dict(cfg)
            if safe[key].get("api_key"):
                safe[key]["api_key"] = _obfuscate(safe[key]["api_key"])
        CONFIG_FILE.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _get_api_key(key: str, default: str = "") -> str:
    """按优先级获取 API Key：环境变量 > 配置文件 > 内置默认值
    确保一定返回非空 Key（如果有内置默认值）"""
    # 1. 环境变量（最高优先级，最安全）
    env_key = f"ZEROAI_API_KEY_{key.upper()}"
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return env_val
    # 2. 配置文件（用户自行配置的 Key）
    config = _load_config()
    if key in config and config[key].get("api_key"):
        return config[key]["api_key"]
    # 3. 内置默认值（确保打包后所有人都能用）
    if default:
        return default
    # 4. 没有内置默认值（如 openrouter 可能没有），返回空
    return ""


# ====== 代理服务器配置（v1.1.0 新增）======
# 启用代理后，所有 AI 请求经代理服务器转发，真实 API Key 只存在服务器端
# 客户端仅需配置代理 URL + 访问 Token，零 Key 泄露风险

# 环境变量优先（ZEROAI_PROXY_URL / ZEROAI_PROXY_TOKEN）
_PROXY_URL_ENV = os.environ.get("ZEROAI_PROXY_URL", "").strip()
_PROXY_TOKEN_ENV = os.environ.get("ZEROAI_PROXY_TOKEN", "").strip()

# 内置默认代理配置 —— v1.1.4 安全硬化：原 URL 与 Token 已从源码中彻底移除。
#
# 原因：原值以 base64 硬编码随 1.1.0–1.1.3 发布到 PyPI，任何下载者均可还原，
# 属已公开泄露、不可撤回的凭据，故永久作废。包内不再携带任何代理凭据。
# 需要经代理转发的用户请显式配置（下方优先级第 1、2 档）。
_BUILTIN_PROXY = {
    "base_url": "",
    "token": "",
}


def _load_proxy_config() -> dict:
    """加载代理配置：环境变量 > 配置文件 > 内置默认值
    返回 {"enabled": bool, "base_url": str, "token": str}

    优先级：
    1. 环境变量（最高，显式设置即视为用户主动启用）
    2. 配置文件中的 proxy 字段（用户自定义开启/关闭）
    3. 默认关闭（安全优先：零配置时绝不静默启用第三方转发）

    信任模型（v1.1.4 起变更）：包内不再携带任何内置代理凭据。用户须通过
    配置文件显式设置 {"proxy": {"enabled": true, "base_url": ..., "token": ...}}
    或设置 ZEROAI_PROXY_URL / ZEROAI_PROXY_TOKEN 环境变量才会启用代理。
    零配置时默认直连各模型官方 API，绝不静默经第三方转发。
    """
    # 1. 环境变量优先
    if _PROXY_URL_ENV and _PROXY_TOKEN_ENV:
        return {
            "enabled": True,
            "base_url": _PROXY_URL_ENV,
            "token": _PROXY_TOKEN_ENV,
        }

    # 2. 配置文件
    try:
        data = _load_config()
        if "proxy" in data and isinstance(data["proxy"], dict):
            p = data["proxy"]
            url = p.get("base_url", "").strip()
            token = p.get("token", "").strip()
            enabled = bool(p.get("enabled", False)) and bool(url) and bool(token)
            return {"enabled": enabled, "base_url": url, "token": token}
    except Exception:
        pass

    # 3. 默认关闭（安全优先：零配置不静默启用第三方转发；
    #    如需开箱即用的代理，显式设置环境变量或配置文件 proxy.enabled=true）
    return {
        "enabled": False,
        "base_url": _BUILTIN_PROXY["base_url"],
        "token": _BUILTIN_PROXY["token"],
    }


def _save_proxy_config(enabled: bool, base_url: str, token: str):
    """保存代理配置到配置文件"""
    try:
        data = _load_config()
        data["proxy"] = {
            "enabled": bool(enabled),
            "base_url": base_url.strip(),
            "token": token.strip(),
        }
        _save_config(data)
    except Exception:
        pass


# 全局缓存代理配置（启动时加载一次，设置面板修改后刷新）
PROXY_CONFIG = _load_proxy_config()


def _is_proxy_enabled() -> bool:
    """代理是否启用"""
    return PROXY_CONFIG.get("enabled", False)


def _refresh_proxy_config():
    """重新加载代理配置（设置面板修改后调用，刷新全局缓存）"""
    global PROXY_CONFIG
    PROXY_CONFIG = _load_proxy_config()


def _make_openai_client(model_key: str):
    """统一的 AsyncOpenAI 客户端工厂
    - 代理启用时：base_url 指向代理，api_key 用 Token（不是真实 Key）
    - 代理未启用时：使用原始 base_url + 真实 api_key（本地开发模式）

    model_key: MODEL_CONFIGS 的键（glm / glm-v / glm-4 / openrouter / ollama / 自定义）

    注意：MODEL_CONFIGS 在函数体内延迟导入，避免与 constants.py 的循环依赖。
    AsyncOpenAI 也在此处按需导入（顶层导入会拖慢包的全部导入路径）。
    """
    # 延迟导入：constants.py 依赖本模块的 _deobfuscate/_get_api_key/_load_config
    from .constants import MODEL_CONFIGS
    from openai import AsyncOpenAI

    base_cfg = MODEL_CONFIGS.get(model_key, {})

    # 代理模式：所有请求经代理转发
    if _is_proxy_enabled():
        proxy_url = PROXY_CONFIG["base_url"]
        # 确保 base_url 以 /v1 结尾（OpenAI SDK 兼容）
        if not proxy_url.rstrip("/").endswith("/v1"):
            proxy_url = proxy_url.rstrip("/") + "/v1"
        return AsyncOpenAI(
            base_url=proxy_url,
            api_key=PROXY_CONFIG["token"],  # 用 Token 替代真实 Key
        )

    # 本地模式：直连上游 API（保留原有行为，兼容本地开发）
    return AsyncOpenAI(
        base_url=base_cfg.get("base_url", ""),
        api_key=base_cfg.get("api_key", ""),
    )


def _make_openai_sync_client(model_key: str):
    """统一的 OpenAI 同步客户端工厂（与 _make_openai_client 逻辑完全对齐）

    存在的理由：llm.py 同时需要同步与异步两种客户端。此前 llm.py 四处
    直接 `OpenAI(...)` / `AsyncOpenAI(...)`，完全绕过本模块的工厂，后果是
    **代理模式形同虚设**——代理开了，核心链路依然直连上游并因无真实 Key 崩溃。
    此处补齐同步版本，让"代理/本地"这一决策只有一处实现。

    model_key: MODEL_CONFIGS 的键
    """
    from .constants import MODEL_CONFIGS
    from openai import OpenAI

    base_cfg = MODEL_CONFIGS.get(model_key, {})

    if _is_proxy_enabled():
        proxy_url = PROXY_CONFIG["base_url"]
        if not proxy_url.rstrip("/").endswith("/v1"):
            proxy_url = proxy_url.rstrip("/") + "/v1"
        return OpenAI(
            base_url=proxy_url,
            api_key=PROXY_CONFIG["token"],
        )

    return OpenAI(
        base_url=base_cfg.get("base_url", ""),
        api_key=base_cfg.get("api_key", ""),
    )
