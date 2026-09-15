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
    """获取版本号。

    唯一来源是 `zeroai.__version__`（pyproject.toml 的 version 也是从它动态
    读取的，见 [tool.setuptools.dynamic]）。

    历史实现读 pyproject.toml 的 `project.version`：装到 site-packages 后
    那里根本没有 pyproject.toml，于是落到硬编码回退值 —— 实测安装 1.1.4 后
    `--version` 仍然打印 "ZeroAI v1.1.3"。现改为三级来源，全部失败才
    返回 "unknown"，不再硬编码任何版本号。
    """
    # 1. 包内 __version__（权威来源）
    try:
        from . import __version__ as _v
        if _v:
            return _v
    except Exception:
        pass
    # 2. 已安装发行版的元数据
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("zero-ai-cli")
    except Exception:
        pass
    return "unknown"


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
            #
            # 【为什么不用 `except ImportError: from tui_agent import ZeroAI`】
            # ModuleNotFoundError 是 ImportError 的子类（已实测确认），所以
            # 「zeroai.tui 这个包不存在」和「zeroai.tui 内部的某个依赖 import 失败」
            # 会被同一条 except 混为一谈，静默降级到 tui_agent。用户只会看到
            # "(UI: tui_agent)"，完全不知道真正的病因 —— 1.1.5 的 TUI 全平台
            # 崩溃就是这样被掩盖成一次"正常降级"的。
            #
            # 判别依据是 e.name：它指出**具体哪个模块**没找到，而不是哪个 import
            # 语句失败。据此分三类：
            #   a) e.name 以 zeroai.tui 开头 → 包自身缺损（打包问题）
            #   b) e.name 是 tui_agent       → 过渡期依赖缺失（见下方说明）
            #   c) 其他（如 rich / textual） → 第三方依赖没装上
            #
            # 关于 (b)：zeroai/tui/* 目前有 6 个模块转发自 tui_agent.py。等到
            # tui_agent.py 被真正剥离、但如果那时仍有转发残留，就会命中这里。
            # 这是**已知的过渡期状态**，所以走降级并给出明确提示，而不是报错。
            #
            # 【为什么 except 同时接住 ImportError 而不只是 ModuleNotFoundError】
            # 实测发现：依赖损坏不一定表现为 ModuleNotFoundError。当某个包的
            # 目录存在但内部文件残损时（例如 pip 卸载/安装被中断、磁盘写入不完整），
            # Python 会抛**普通 ImportError**，典型信息形如
            #     ImportError: cannot import name 'OpenAI' from 'openai' (unknown location)
            # —— 注意 "(unknown location)"，表示包被找到了但内容不完整。
            # 此时 e.name 仍会被填上出问题的模块名，所以下面的 e.name 判别依旧可用；
            # 但 except 子句若只写 ModuleNotFoundError，这类故障会直接冒泡成
            # 未捕获回溯，用户看到一大片 traceback 而非一句可读的提示。
            try:
                from zeroai.tui.app import ZeroAI
                _import_source = "zeroai.tui.app"
            except ImportError as e:
                _missing = getattr(e, "name", "") or ""
                _detail = str(e)

                if _missing.startswith("zeroai.tui"):
                    # (a) zeroai.tui 包自身缺损，安装包损坏。
                    print(f"ZeroAI: 终端 UI 不可用 —— 安装包缺少 {_missing!r}。",
                          file=sys.stderr)
                    print("  该发行版未正确包含 zeroai.tui 子包，请重装：",
                          file=sys.stderr)
                    print("      pip install --upgrade --force-reinstall zero-ai-cli",
                          file=sys.stderr)
                    print("  无头模式不受影响：zeroai --task \"你的任务\"", file=sys.stderr)
                    return 3

                if _missing == "tui_agent":
                    # (b) 过渡期：尝试兼容旧版入口，并把真实状态如实告诉用户。
                    print("ZeroAI: 提示 —— zeroai.tui 尚未完全解耦，"
                          "正在回退到 tui_agent。", file=sys.stderr)
                    try:
                        from tui_agent import ZeroAI
                        _import_source = "tui_agent"
                    except ImportError as e2:
                        _m2 = getattr(e2, "name", "") or "（未知）"
                        print(f"ZeroAI: 终端 UI 不可用 —— 缺少模块 {_m2!r}。",
                              file=sys.stderr)
                        print("  请重装：pip install --upgrade --force-reinstall zero-ai-cli",
                              file=sys.stderr)
                        return 3
                elif _missing:
                    # (c1) 已知具体是哪个模块出的问题。
                    # **不降级** —— 降级会掩盖病因，而 tui_agent 的依赖集合是
                    # zeroai.tui 的超集，这里缺的东西到那边同样缺，降级过去只会
                    # 换个地方崩，还把真实原因藏得更深。
                    print(f"ZeroAI: 无法加载终端 UI —— 模块 {_missing!r} 不可用。",
                          file=sys.stderr)
                    if "unknown location" in _detail:
                        print("  该模块已安装但内容不完整（常见于安装/卸载被中断）。",
                              file=sys.stderr)
                    print("  可尝试：", file=sys.stderr)
                    print(f"      pip install --force-reinstall {_missing.split('.')[0]}",
                          file=sys.stderr)
                    print("  或完整重装：pip install --upgrade --force-reinstall zero-ai-cli",
                          file=sys.stderr)
                    print("  用 zeroai --check 可查看依赖自检结果。", file=sys.stderr)
                    return 3
                else:
                    # (c2) 拿不到 e.name（部分异常不填该属性）。此时无法给出
                    # 针对性建议，但把原始信息原样透出，好过吞掉。
                    print("ZeroAI: 无法加载终端 UI。原始错误：", file=sys.stderr)
                    print(f"      {type(e).__name__}: {_detail}", file=sys.stderr)
                    print("  请尝试重装：pip install --upgrade --force-reinstall zero-ai-cli",
                          file=sys.stderr)
                    return 3

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
