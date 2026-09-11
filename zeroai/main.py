"""ZeroAI 统一入口

从 tui_agent.py 行14566-14614 迁移并增强。
支持两种 UI 模式：
- textual: 原始 Textual UI（tui_agent.py 中的 ZeroAI App）
- zeroai-tui: C/Zig 加速的新 TUI 框架

用法：
    python -m zeroai                    # 默认 textual UI
    python -m zeroai --ui textual       # 显式指定 textual UI
    python -m zeroai --ui zeroai-tui    # 使用 C/Zig 加速 TUI
    python -m zeroai --expert coder     # 直接指定专家
    python -m zeroai --version          # 显示版本号
"""
import os
import sys
import argparse


def _ensure_project_root_in_path():
    """确保项目根目录在 sys.path 中"""
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)


def _get_version() -> str:
    """获取版本号"""
    try:
        # 优先从 pyproject.toml 读取
        import tomllib  # Python 3.11+
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        _project_root = os.path.dirname(_script_dir)
        pyproject_path = os.path.join(_project_root, "pyproject.toml")
        if os.path.exists(pyproject_path):
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return data.get("project", {}).get("version", "unknown")
    except Exception:
        pass
    return "1.1.3"  # 回退版本号


def _is_console_stream(stream) -> bool:
    """判断某个标准流是否真的连着一个控制台/终端。

    为什么不能只用 sys.stdin.isatty()：
        Windows 上 `NUL` 设备会被 CPython 的 isatty() **误判为 True**。
        因此这里在 Windows 上额外用 GetConsoleMode 二次确认——它对
        NUL、管道、文件重定向一律失败，只有真控制台才成功。
    """
    if stream is None:
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False

    if os.name != "nt":
        return True

    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return False


def _has_interactive_terminal() -> bool:
    """stdin 与 stdout **都**真的连着控制台，才认为处于交互式终端。

    为什么两个流都要检查：Textual 既要读键盘（stdin）也要写屏幕（stdout），
    任何一个被重定向（`< nul`、`| tee`、`> out.txt`、CI 里的管道）都会让它
    永久挂起。实测：stdin 重定向到 nul 时 `python -m zeroai` 挂死 >20s。
    逃生舱：设置 ZEROAI_FORCE_TUI=1 可跳过本检查强制启动 UI。
    """
    return _is_console_stream(sys.stdin) and _is_console_stream(sys.stdout)


def main():
    """ZeroAI 主入口"""
    _ensure_project_root_in_path()

    # ---- 无头模式分流 ----
    # 命中这些开关时把控制权交给 zeroai.cli，完全不启动 TUI。
    # 这样在 CI / 管道 / cron / 非交互 SSH 里也能用，且不会挂死。
    _argv = sys.argv[1:]
    _headless = {"-t", "--task", "--check", "--dry-run"}
    if any(a in _headless or a.startswith("--task=") for a in _argv):
        from zeroai.cli import main as _cli_main
        return _cli_main(_argv)

    version = _get_version()
    parser = argparse.ArgumentParser(
        description="ZeroAI - 终端 AI 编程助手（多专家协作·语音对话·文档生成·安全审计）",
        prog="zeroai",
    )
    parser.add_argument("--ui", choices=["textual", "zeroai-tui"], default="textual",
                        help="UI framework to use (default: textual)")
    parser.add_argument("--expert", type=str, help="Direct expert mode (skip routing)")
    parser.add_argument("--version", action="version", version=f"ZeroAI v{version}")
    args, unknown = parser.parse_known_args()

    # ---- 非交互环境保护 ----
    # 与其让 app.run() 永久挂起，不如明确报错并告诉用户正确用法。
    if not _has_interactive_terminal() and not os.environ.get("ZEROAI_FORCE_TUI"):
        print("ZeroAI: 检测到非交互环境（stdin/stdout 不是真正的终端），"
              "已拒绝启动终端 UI 以避免挂起。", file=sys.stderr)
        print("  无头执行任务：  zeroai --task \"你的任务\" [--json]", file=sys.stderr)
        print("  环境自检：      zeroai --check", file=sys.stderr)
        print("  确实要启动 UI： 设置环境变量 ZEROAI_FORCE_TUI=1", file=sys.stderr)
        return 2

    try:
        if args.ui == "zeroai-tui":
            # 使用 zeroai-tui UI（C/Zig 加速）
            try:
                _script_dir = os.path.dirname(os.path.abspath(__file__))
                _project_root = os.path.dirname(_script_dir)
                _tui_dir = os.path.join(_project_root, "zeroai-tui")
                if _tui_dir not in sys.path:
                    sys.path.insert(0, _tui_dir)

                from zeroai_tui.integration import ZeroAIIntegration

                print(f"Starting ZeroAI v{version} with zeroai-tui (C-accelerated)...")
                print("Press Ctrl+C to exit")
                print()

                integration = ZeroAIIntegration()
                integration.start()

            except ImportError as e:
                print(f"zeroai-tui not available: {e}")
                print("Falling back to Textual UI...")
                args.ui = "textual"

        if args.ui == "textual":
            # 使用 Textual UI
            # 优先从 zeroai.tui 包导入（包装模式），回退到 tui_agent.py 直接导入
            try:
                from zeroai.tui.app import ZeroAI
                _import_source = "zeroai.tui.app"
            except ImportError:
                from tui_agent import ZeroAI
                _import_source = "tui_agent"

            print(f"Starting ZeroAI v{version} (UI: {_import_source})...", file=sys.stderr)
            app = ZeroAI()
            app.title = f"ZeroAI v{version}"
            app.run()
    finally:
        # 程序退出时清理运行时缓存
        try:
            from zeroai.core.runtime import runtime_cache
            runtime_cache.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    main()
