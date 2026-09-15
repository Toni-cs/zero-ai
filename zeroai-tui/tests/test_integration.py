"""
ZeroAI Integration Test
"""
import sys
import os

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_integration():
    """Test zeroai-tui integration"""
    print("=" * 60)
    print("ZeroAI Integration Test")
    print("=" * 60)
    print()
    
    tests = [
        ("Import zeroai_tui", test_import),
        ("Import zai_chat", test_zai_chat),
        ("Import integration", test_integration_module),
        ("Create ZeroAIChat", test_create_chat),
        ("Create ChatMessage", test_create_message),
        ("Markdown rendering", test_markdown),
        ("Code highlighting", test_highlight),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        print(f"[Test] {name}...")
        try:
            test_func()
            print(f"  [OK] Passed")
            passed += 1
        except Exception as e:
            print(f"  [ERROR] {e}")
            failed += 1
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    if failed == 0:
        print()
        print("Integration ready!")
        print("Run: python -m zeroai_tui.integration")
    
    assert failed == 0, f"{failed} 项测试失败"


def test_import():
    """Test importing zeroai_tui"""
    import zeroai_tui
    assert hasattr(zeroai_tui, '__version__'), "zeroai_tui missing __version__"


def test_zai_chat():
    """Test importing zai_chat"""
    from zeroai_tui import zai_chat
    assert hasattr(zai_chat, 'ZeroAIChat'), "zai_chat missing ZeroAIChat"


def test_integration_module():
    """Test importing integration"""
    from zeroai_tui import integration
    assert hasattr(integration, 'ZeroAIIntegration'), "integration missing ZeroAIIntegration"


def test_create_chat():
    """Test creating ZeroAIChat"""
    from zeroai_tui.zai_chat import ZeroAIChat
    chat = ZeroAIChat()
    assert chat is not None, "ZeroAIChat instance is None"


def test_create_message():
    """Test creating ChatMessage"""
    from zeroai_tui.zai_chat import ChatMessage
    msg = ChatMessage("user", "Hello")
    assert msg.role == "user", f"expected role 'user', got {msg.role!r}"
    assert msg.content == "Hello", f"expected content 'Hello', got {msg.content!r}"


def test_markdown():
    """Test markdown rendering"""
    from zeroai_tui import render_markdown
    lines = render_markdown("# Test\n**Bold**")
    assert len(lines) > 0, "render_markdown returned empty lines"


def test_highlight():
    """Test code highlighting"""
    from zeroai_tui import highlight_code
    tokens = highlight_code("x = 1 + 2")
    assert len(tokens) > 0, "highlight_code returned empty tokens"


if __name__ == "__main__":
    try:
        test_integration()
        sys.exit(0)
    except AssertionError:
        sys.exit(1)
