# -*- coding: utf-8 -*-
"""专家配置必须只有单一数据源 constants.EXPERT_TEAM。

## 背景

2026-10-08 之前，同一份专家配置存在**两处**并存且持续漂移：

    constants.EXPERT_TEAM          10/10 keywords 不同
    zeroai/config.yaml 的 experts  4/10 system_prompt 不同
                                   4/10 desc、2/10 label 不同

读 config.yaml 的有两条活路径：
  - zeroai/core/config.py  -> get_expert_config()
      被 expert.py 全链路使用（拼 system_prompt / 取 label）
  - zeroai-tui/zeroai_tui/integration.py -> init_core()
      **随 wheel 分发**，直接把 system_prompt 拼进 messages

后果不是"配置不一致"这种抽象问题，而是具体的：

    academic 的 system_prompt
      config.yaml 版 75 字   —— 一句普通的学术专家介绍
      constants 版   5345 字  —— 一整套学术规范（禁止编造文献、
                                引用前必须 citation_check、公式必须
                                LaTeX、PRISMA、GB/T 格式…）

也就是说：**走 config.yaml 入口时，学术诚信那套规则根本不生效**，
而走 TUI 入口时生效 —— 同一程序在不同入口上约束不同。

本测试把「单一数据源」钉死，任何一处回退都会失败。
"""
import ast
import io
import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_YAML = os.path.join(ROOT, "zeroai", "config.yaml")
CONFIG_PY = os.path.join(ROOT, "zeroai", "core", "config.py")
INTEGRATION_PY = os.path.join(ROOT, "zeroai-tui", "zeroai_tui",
                              "integration.py")


def _team():
    import sys
    sys.path.insert(0, ROOT)
    from zeroai.core.constants import EXPERT_TEAM
    return EXPERT_TEAM


def _config():
    import sys
    sys.path.insert(0, ROOT)
    from zeroai.core.config import get_config
    return get_config()


# ────────────────── 数据源必须唯一 ──────────────────

def test_config_yaml_has_no_experts_section():
    """config.yaml 不得再含 experts 段 —— 只要数据还在，就总有人会去读。"""
    with io.open(CONFIG_YAML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert "experts" not in cfg, (
        "config.yaml 重新出现了 experts 段。它与 constants.EXPERT_TEAM "
        "并存时曾漂移到 4/10 system_prompt 不同，其中 academic 的 "
        "config 版只有 75 字、丢掉了整套学术规范（见 "
        "evals/results/config_experts_drift.md）。"
        "若确需恢复该段，必须同时提供强制同步测试。")
    for top in ("models", "hybrid", "tools", "tui", "security"):
        assert top in cfg, "config.yaml 缺少顶层段 %s" % top


def test_default_config_has_no_experts_key():
    """_get_default_config 的默认字典里不得再有 experts 键。"""
    src = io.open(CONFIG_PY, encoding="utf-8").read()
    m = re.search(r"def _get_default_config.*?return \{(.*?)\n\s*\}",
                  src, re.S)
    assert m, "找不到 _get_default_config"
    assert '"experts"' not in m.group(1), (
        "_get_default_config 的默认字典里又出现了 experts 键")


def _is_config_ref(node):
    """识别 config._config / _config / config 这类引用。"""
    if isinstance(node, ast.Name):
        return node.id in ("_config", "config")
    if isinstance(node, ast.Attribute):
        return node.attr == "_config" or _is_config_ref(node.value)
    return False


def _find_config_experts_reads(src, filename):
    """用 AST 找出**真正会执行**的 config experts 读取。

    不能用正则：代码里大量形如「此前读 config._config.get("experts")」
    的**说明注释**会被误判 —— 首版测试就是这么把两处注释当成违规的。
    AST 不含注释，天然排除。
    """
    tree = ast.parse(src, filename=filename)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "get"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "experts"
                    and _is_config_ref(f.value)):
                hits.append(node.lineno)
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if (isinstance(sl, ast.Constant) and sl.value == "experts"
                    and _is_config_ref(node.value)):
                hits.append(node.lineno)
    return hits


def test_no_code_reads_config_experts():
    """全仓运行时代码不得再读 config.yaml 的 experts 段。"""
    offenders = []
    for base in (os.path.join(ROOT, "zeroai"),
                 os.path.join(ROOT, "zeroai-tui")):
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in ("__pycache__", "zig-cache")]
            for f in fn:
                if not f.endswith((".py", ".zig")):
                    continue
                p = os.path.join(dp, f)
                try:
                    src = io.open(p, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                if '"experts"' not in src and "'experts'" not in src:
                    continue  # 快速跳过
                try:
                    hits = _find_config_experts_reads(
                        src, os.path.relpath(p, ROOT))
                except SyntaxError:
                    continue  # 非 Python 源（.zig）等交给专门测试
                if hits:
                    offenders.append("%s:%s"
                                     % (os.path.relpath(p, ROOT), hits))
    assert not offenders, (
        "以下运行时代码仍在读 config.yaml 的 experts 段：%s" % offenders)


# ────────────────── config 路径必须返回 EXPERT_TEAM 的数据 ──────────

@pytest.mark.parametrize("key", ["pm", "coder", "reasoner", "knowledge",
                                 "chinese", "vision", "academic",
                                 "devops", "security", "data"])
def test_config_accessor_returns_expert_team_data(key):
    team = _team()
    cfg = _config().get_expert_config(key)
    assert cfg["system_prompt"] == team[key]["system_prompt"], (
        "%s 的 system_prompt 与 EXPERT_TEAM 不一致 —— 双数据源回归" % key)
    assert cfg["label"] == team[key]["label"]
    assert cfg["desc"] == team[key]["desc"]
    assert cfg["keywords"] == team[key]["keywords"]
    assert cfg["model"] == team[key]["model"]
    assert cfg["model_key"] == team[key]["model_key"]
    # base_url / api_key 仍由 models 段补充
    assert "base_url" in cfg
    assert "api_key" in cfg


def test_unknown_expert_still_raises():
    with pytest.raises(ValueError):
        _config().get_expert_config("nonexistent_expert")


# ────────────────── 学术诚信规则必须经由 config 路径可达 ──────────

def test_academic_prompt_has_integrity_rules_via_config_path():
    """这是本组测试的核心：75 字版 prompt 曾让学术规范整套失效。"""
    cfg = _config().get_expert_config("academic")
    sp = cfg["system_prompt"]
    assert len(sp) > 1000, (
        "academic 的 system_prompt 只有 %d 字 —— 很可能又退回了 "
        "config.yaml 里那句 75 字的通用介绍" % len(sp))
    for must in ("citation_check", "禁止编造", "LaTeX"):
        assert must in sp, (
            "academic system_prompt 缺少「%s」这条学术诚信规则" % must)


def test_academic_prompt_is_identical_across_entrypoints():
    """TUI 路径与 headless 路径必须拿到同一段提示词。"""
    import sys
    sys.path.insert(0, ROOT)
    from zeroai.core.constants import EXPERT_TEAM
    via_tui = EXPERT_TEAM["academic"]["system_prompt"]
    via_headless = _config().get_expert_config("academic")["system_prompt"]
    assert via_tui == via_headless, "两个入口的 academic 提示词不同"


# ────────────────── TUI 集成层（随 wheel 分发） ──────────────────

def test_tui_integration_reads_expert_team():
    assert os.path.exists(INTEGRATION_PY), "zeroai_tui/integration.py 不存在"
    src = io.open(INTEGRATION_PY, encoding="utf-8").read()
    assert "from zeroai.core.constants import EXPERT_TEAM" in src, (
        "zeroai_tui/integration.py 不再从 EXPERT_TEAM 取专家配置")
    # 该模块随 wheel 分发，读到过期提示词会被直接打包带出去
    hits = _find_config_experts_reads(src, "integration.py")
    assert not hits, (
        "zeroai_tui/integration.py 第 %s 行仍在读 config.yaml 的 experts 段"
        % hits)
    # 关键词/提示词取值必须落到 EXPERT_TEAM 上
    assert "self._experts = EXPERT_TEAM" in src


def test_config_yaml_still_ships_required_sections():
    """删段不得破坏 wheel 需要携带的其余配置（api_key 等）。"""
    with io.open(CONFIG_YAML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert isinstance(cfg, dict)
    glm = (cfg.get("models") or {}).get("glm") or {}
    assert "api_key" in glm, "models.glm 缺 api_key 键（可为空，但键必须在）"
    assert "base_url" in glm or "model" in glm


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
