"""
ZeroAI Full Integration Test

验证 ZeroAI 核心模块的完整集成：
- zeroai 主包导入
- tui_agent 入口模块
- 专家系统 / LLM / 配置模块
- zeroai_tui C/Zig 加速层（可选，未安装时降级测试）
- MCP / Agent Loop / 向量记忆（阶段 3 新增）

运行：python test_full_integration.py
"""
import sys
import os

import pytest

# Add paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 本文件已移入 tests/，需上溯一级


def test_full_integration():
    """Test complete integration"""
    print("=" * 60)
    print("ZeroAI Full Integration Test")
    print("=" * 60)
    print()

    tests = [
        ("Import zeroai package", test_import_zeroai),
        ("Import tui_agent", test_import_tui_agent),
        ("Expert system ready", test_expert_system),
        ("LLM module ready", test_llm_module),
        ("Config module ready", test_config),
        ("MCP module ready", test_mcp_module),
        ("Agent Loop ready", test_agent_loop),
        ("Vector memory ready", test_vector_memory),
        ("Tools registry ready", test_tools_registry),
        ("ZeroAI-TUI (optional)", test_zeroai_tui_optional),
    ]

    passed = 0
    failed = 0
    skipped = 0

    for name, test_func in tests:
        print(f"[Test] {name}...")
        try:
            test_func()
            print(f"  [OK] Passed")
            passed += 1
        except pytest.skip.Exception as e:
            print(f"  [SKIP] Skipped ({e})")
            skipped += 1
        except Exception as e:
            print(f"  [ERROR] {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)

    if failed == 0:
        print()
        print("Full integration ready!")
        print()
        print("Usage:")
        print("  # Run with Textual UI (default)")
        print("  python tui_agent.py")
        print()
        print("  # Run with zeroai-tui UI (if installed)")
        print("  python tui_agent.py --ui zeroai-tui")
        print()
        print("  # Run ZeroAI as MCP Server")
        print("  python -m zeroai.mcp")

    assert failed == 0, f"{failed} 项集成测试失败"


def test_import_zeroai():
    """Test importing zeroai package"""
    import zeroai
    assert hasattr(zeroai, '__version__'), "zeroai 缺少 __version__ 属性"


def test_import_tui_agent():
    """Test importing tui_agent"""
    import tui_agent
    assert hasattr(tui_agent, 'main'), "tui_agent 缺少 main 函数"


def test_expert_system():
    """Test expert system"""
    from zeroai.core.expert import ExpertRouter
    router = ExpertRouter()
    assert router is not None, "ExpertRouter 返回 None"


def test_llm_module():
    """Test LLM module"""
    from zeroai.core import llm
    assert hasattr(llm, 'LLMClient'), "llm 模块缺少 LLMClient"


def test_config():
    """Test config module"""
    from zeroai.core.config import Config
    config = Config()
    assert config is not None, "Config 返回 None"


def test_mcp_module():
    """Test MCP module (阶段 3 新增)"""
    from zeroai import mcp
    assert hasattr(mcp, 'MCPClient'), "mcp 缺少 MCPClient"
    assert hasattr(mcp, 'MCPServer'), "mcp 缺少 MCPServer"


def test_agent_loop():
    """Test Agent Loop (阶段 1 增强)"""
    from zeroai.core.agent import AdvancedAgentLoop, MultiAgentCollaborator
    assert AdvancedAgentLoop is not None, "AdvancedAgentLoop 为 None"
    assert MultiAgentCollaborator is not None, "MultiAgentCollaborator 为 None"


def test_vector_memory():
    """Test vector memory (阶段 2 新增)"""
    from zeroai.memory import VectorStore, ConversationMemory
    assert VectorStore is not None, "VectorStore 为 None"
    assert ConversationMemory is not None, "ConversationMemory 为 None"


def test_tools_registry():
    """Test tools registry"""
    from zeroai.tools.registry import TOOL_MAP, TOOLS
    assert len(TOOL_MAP) > 0, "TOOL_MAP 为空"
    assert len(TOOLS) > 0, "TOOLS 为空"


def test_zeroai_tui_optional():
    """Test zeroai_tui (optional - skip if not installed)"""
    try:
        import zeroai_tui
    except ImportError:
        pytest.skip("zeroai_tui 未安装（可选依赖）")
    assert hasattr(zeroai_tui, '__version__'), "zeroai_tui 缺少 __version__ 属性"


if __name__ == "__main__":
    try:
        test_full_integration()
        sys.exit(0)
    except AssertionError as e:
        print(f"集成测试失败: {e}")
        sys.exit(1)
