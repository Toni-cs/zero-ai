"""MCP 电脑操作集成的配置回归测试。

背景（2026-09-16）
--------------------------------------------------------------------------
集成本机电脑操作需要写 `~/.zeroai/mcp_config.json`。这里有个**静默陷阱**：

  Claude Desktop / 大多数文档用 `"type": "stdio"`
  ZeroAI 用的是 `"transport": "stdio"`

而 `MCPServerConfig.from_dict` 是 `transport = data.get("transport", "stdio")` ——
**默认值恰好是 stdio**，所以抄 Claude Desktop 的配置在 stdio 下能"碰巧工作"，
但一旦写 `"type": "sse"`，`transport` 会静默退回 `"stdio"`，连接方式完全错，
且**不报错**。本测试把这两个行为都锁住。

注意：本测试**不连接**真实 MCP 服务器（依赖 uvx / 网络 / 桌面会话，
放进 CI 必然 flaky）。只验证配置解析契约。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zeroai.mcp.config import MCPConfig, MCPServerConfig  # noqa: E402

# 本机实际使用的电脑操作服务器配置（与 ~/.zeroai/mcp_config.json 保持一致）
COMPUTER_SERVER = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["computer-control-mcp"],
    "enabled": True,
    "description": "电脑操作：截屏 / OCR / 鼠标 / 键盘 / 窗口管理",
}


def _write(tmp_path: Path, data: dict) -> MCPConfig:
    p = tmp_path / "mcp_config.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return MCPConfig(config_path=p)


# ─────────────────── T1：电脑操作配置能被正确解析 ───────────────────

def test_computer_server_config_parses(tmp_path):
    """T1 电脑操作服务器配置应完整解析出 transport/command/args"""
    cfg = _write(tmp_path, {"mcpServers": {"computer": COMPUTER_SERVER}})
    srv = cfg.get_server("computer")
    assert srv is not None, "配置未被加载"
    assert srv.transport == "stdio"
    assert srv.command == "uvx"
    assert srv.args == ["computer-control-mcp"]
    assert srv.enabled is True


def test_computer_server_validate_passes(tmp_path):
    """T1b 该配置应通过 validate()（否则 TUI 的 only_enabled 过滤会漏掉它）

    注意 validate() 的契约：**通过时返回 None**，不通过时返回错误字符串
    （见 config.py:90 `def validate(self) -> Optional[str]`）。
    因此判定必须用真值，不能写 `== ""` —— 否则校验成功反而被判为失败。
    """
    srv = MCPServerConfig.from_dict("computer", COMPUTER_SERVER)
    assert not srv.validate(), f"配置校验失败: {srv.validate()!r}"


def test_enabled_filter_includes_computer_server(tmp_path):
    """T1c _auto_init_mcp 用 only_enabled=True 取服务器，必须能取到"""
    cfg = _write(tmp_path, {"mcpServers": {"computer": COMPUTER_SERVER}})
    servers = cfg.list_servers(only_enabled=True)
    assert [s.name for s in servers] == ["computer"]


# ─────────────────── T2：锁住 type/transport 陷阱 ───────────────────

def test_transport_field_is_honored(tmp_path):
    """T2 显式 transport 必须生效（sse 不能被当成 stdio）"""
    data = {"mcpServers": {"remote": {
        "transport": "sse", "url": "http://127.0.0.1:9000/sse", "enabled": True,
    }}}
    cfg = _write(tmp_path, data)
    assert cfg.get_server("remote").transport == "sse"


def test_type_field_is_silently_ignored(tmp_path):
    """T2b 用 `type` 而非 `transport` 会被**静默忽略**（默认 stdio）。

    这是文档级陷阱：抄 Claude Desktop 的配置在 stdio 下碰巧能用，
    写 "type": "sse" 则静默退化为 stdio。本测试把该行为固定下来，
    以便将来若要改成兼容 `type`，会有一个明确的测试需要更新。
    """
    data = {"mcpServers": {"remote": {
        "type": "sse", "url": "http://127.0.0.1:9000/sse", "enabled": True,
    }}}
    cfg = _write(tmp_path, data)
    srv = cfg.get_server("remote")
    assert srv.transport == "stdio", (
        "行为已变化：`type` 字段现在会被识别。"
        "若是刻意增加兼容性，请更新本测试与 SKILL/文档。"
    )


def test_invalid_transport_is_rejected_by_validate(tmp_path):
    """T2c 非法 transport 必须被 validate 拒绝（不能静默放行）"""
    srv = MCPServerConfig.from_dict("bad", {
        "transport": "websocket", "command": "x", "enabled": True,
    })
    err = srv.validate()
    assert err, "非法传输方式未被拒绝（validate 返回了假值）"
    assert isinstance(err, str), f"错误信息应为字符串，实际 {type(err)}"


# ─────────────────── T3：本机真实配置文件（存在才验） ───────────────────

@pytest.mark.skipif(
    not (Path.home() / ".zeroai" / "mcp_config.json").exists(),
    reason="本机未配置 MCP，跳过",
)
def test_local_config_is_parseable():
    """T3 本机真实配置文件应可被 ZeroAI 解析（防止手写出语法错误）"""
    real = Path.home() / ".zeroai" / "mcp_config.json"
    cfg = MCPConfig(config_path=real)
    servers = cfg.list_servers(only_enabled=True)
    assert servers, "配置文件存在但没有解析出任何启用的服务器"
    for s in servers:
        assert not s.validate(), f"服务器 {s.name} 配置非法: {s.validate()!r}"
