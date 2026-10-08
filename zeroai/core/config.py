"""Configuration loader for ZeroAI"""
import os
import sys
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """ZeroAI configuration manager"""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration from YAML file"""
        if config_path is None:
            # Look for config.yaml in package directory
            config_path = Path(__file__).parent.parent / "config.yaml"
        
        self.config_path = Path(config_path)
        self._config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            # 警告必须走 stderr：这是库代码，把诊断信息打到 stdout 会污染
            # 调用方的输出（例如 `zeroai --task ... --json` 的单行 JSON 契约）。
            print(f"Warning: Config file not found at {self.config_path}", file=sys.stderr)
            return self._get_default_config()
        except yaml.YAMLError as e:
            print(f"Warning: Error parsing config file: {e}", file=sys.stderr)
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Return default configuration

        注：2026-10-08 起不再包含 "experts" 段 —— 专家配置的唯一数据源是
        constants.EXPERT_TEAM（见 get_expert_config）。保留一份会让
        「看起来权威、实际没人读」的副本，那正是漂移事故的成因。
        """
        return {
            "models": {},
            "hybrid": {
                "max_parallel_experts": 3,
                "expert_max_chars": 800,
                "dedup_similarity_threshold": 0.7,
                "memory_turns": 3,
                "enable_collab_chain": False
            },
            "tools": {
                "max_result_length": 8000,
                "timeout": 120,
                "python_sandbox_timeout": 10
            },
            "tui": {
                "theme": "dark",
                "font_size": 14,
                "show_toolbar": True,
                "auto_scroll": True
            },
            "security": {
                "allowed_commands": ["dir", "ls", "pwd", "echo", "cat", "type", "find", "grep"],
                "blocked_commands": ["format", "del /f", "shutdown", "mkfs", "rm -rf /"],
                "max_output_length": 8000
            }
        }
    
    def get_model_config(self, model_key: str) -> Dict[str, Any]:
        """Get configuration for a specific model"""
        models = self._config.get("models", {})
        if model_key not in models:
            raise ValueError(f"Model '{model_key}' not found in configuration")
        
        model_config = models[model_key].copy()
        
        # Try to load API key from environment if not set
        if not model_config.get("api_key"):
            env_key = f"ZEROAI_{model_key.upper()}_API_KEY"
            model_config["api_key"] = os.environ.get(env_key, "")
        
        return model_config
    
    def get_expert_config(self, expert_key: str) -> Dict[str, Any]:
        """Get configuration for a specific expert.

        2026-10-08 起改为从 constants.EXPERT_TEAM 取，**不再读
        config.yaml 的 experts 段**。

        改动前两处并存且已发生漂移（实测）：

            10/10 专家 keywords 不同
             4/10 专家 system_prompt 不同（knowledge/chinese/vision/academic）
             4/10 desc、2/10 label 不同

        其中最严重的是 **academic**：config.yaml 里的版本只有
        「你是 ZeroAI 的学术专家，专精论文写作…」一句，而 EXPERT_TEAM
        里是一整套学术规范（禁止编造文献、引用前必须 citation_check、
        公式必须 LaTeX…）。走本方法的 expert.py 全链路拿不到这些规则，
        等于同一程序在不同入口上学术诚信约束不一致。

        调用方所需字段核对：
        - expert.py  -> system_prompt / label     EXPERT_TEAM 均有
        - llm.py     -> model_key                 EXPERT_TEAM 有
        - 外部       -> base_url / api_key        仍由 get_model_config 补充

        （models 段实测与 constants.MODEL_CONFIGS **零漂移**，故不动。）
        """
        from .constants import EXPERT_TEAM

        if expert_key not in EXPERT_TEAM:
            raise ValueError(f"Expert '{expert_key}' not found in EXPERT_TEAM")

        expert_config = dict(EXPERT_TEAM[expert_key])

        # Get the model config for this expert
        model_key = expert_config.get("model_key")
        if model_key:
            model_config = self.get_model_config(model_key)
            expert_config["base_url"] = model_config.get("base_url")
            expert_config["api_key"] = model_config.get("api_key")

        return expert_config
    
    def get_hybrid_config(self) -> Dict[str, Any]:
        """Get hybrid mode configuration"""
        return self._config.get("hybrid", self._get_default_config()["hybrid"])
    
    def get_tools_config(self) -> Dict[str, Any]:
        """Get tools configuration"""
        return self._config.get("tools", self._get_default_config()["tools"])
    
    def get_tui_config(self) -> Dict[str, Any]:
        """Get TUI configuration"""
        return self._config.get("tui", self._get_default_config()["tui"])
    
    def get_security_config(self) -> Dict[str, Any]:
        """Get security configuration"""
        return self._config.get("security", self._get_default_config()["security"])
    
    def reload(self):
        """Reload configuration from file"""
        self._config = self._load_config()
    
    def save(self, config_path: Optional[str] = None):
        """Save current configuration to file"""
        path = Path(config_path) if config_path else self.config_path
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(self._config, f, allow_unicode=True, default_flow_style=False)


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get global configuration instance"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from file"""
    global _config
    _config = Config(config_path)
    return _config
