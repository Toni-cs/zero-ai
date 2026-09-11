"""ZeroAI 无头（非交互）命令行入口

本模块提供 **可脚本化、可 CI 化** 的 ZeroAI 调用方式。它完全不依赖 TUI，
因此在没有 TTY 的环境（CI、cron、管道、SSH 非交互会话）中不会挂起。

设计契约（可被测试验证）：

    退出码
        0   任务正常完成
        1   任务未能完成（规划失败 / 达到最大步数 / 运行期异常）
        2   用法或配置错误（缺参数、模型不存在、缺 API Key）
        124 超时（--timeout 触发）
        130 用户中断（Ctrl+C）

    输出
        默认：人类可读文本输出到 stdout，过程信息到 stderr
        --json：stdout 只输出 **一行** JSON 信封，过程信息仍然到 stderr
        --dry-run：不调用任何网络接口，只做配置解析并输出将要执行的内容

用法：
    python -m zeroai --task "把 README 的标题列出来"
    python -m zeroai --task "统计代码行数" --json --model glm --max-steps 6
    python -m zeroai --check
    python -m zeroai --task "hello" --dry-run --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# 退出码契约
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 124
EXIT_INTERRUPTED = 130

MAX_STEP_RESULT_CHARS = 2000  # JSON 信封里单步结果的截断长度，防止爆内存

# 规划器在达到步数上限时返回的固定文案（见 zeroai/core/agent.py）
_MAX_STEPS_SENTINEL = "已达到最大步数"


def _configure_std_streams() -> None:
    """在 Windows 上强制 UTF-8，避免中文/emoji 触发 UnicodeEncodeError。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _emit_envelope(envelope: Dict[str, Any], as_json: bool) -> None:
    """输出统一信封。--json 时严格单行，便于 `| jq` 与测试断言。"""
    if as_json:
        print(json.dumps(envelope, ensure_ascii=False, default=str), flush=True)
    else:
        if envelope.get("ok"):
            print(envelope.get("answer", ""), flush=True)
        else:
            err = envelope.get("error") or {}
            print(f"[错误] {err.get('message', '未知错误')}", file=sys.stderr, flush=True)


def _truncate(text: Any, limit: int = MAX_STEP_RESULT_CHARS) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"...[已截断，原长 {len(s)}]"


# ---------------------------------------------------------------------------
# --check / doctor：无网络、无重依赖，纯环境自检
# ---------------------------------------------------------------------------
def run_check(as_json: bool = False) -> int:
    """环境自检。

    说明（实测，别信"秒级"这种宣传）：本函数不导入 Agent 引擎
    （zeroai.core.agent）与工具注册表，也不会发起任何网络请求；但由于
    `import zeroai.cli` 会先执行包的 __init__.py（其中导入 core.llm /
    tools.base / mcp），整体耗时约 2s 量级 —— 可接受，但并不轻量。
    自检本身只读本地配置，不触网。
    """
    checks: List[Dict[str, Any]] = []
    problems = 0   # 致命问题：会导致工具不可用
    warnings = 0   # 警告：影响可用性，但可通过环境变量/配置修复

    checks.append({"name": "python", "ok": sys.version_info >= (3, 10),
                   "detail": sys.version.split()[0]})

    # 核心依赖
    for mod in ("openai", "textual", "yaml", "requests", "numpy"):
        try:
            __import__(mod)
            checks.append({"name": f"dep:{mod}", "ok": True, "detail": "importable"})
        except Exception as exc:  # noqa: BLE001
            problems += 1
            checks.append({"name": f"dep:{mod}", "ok": False, "detail": repr(exc)})

    # zeroai 包自身
    try:
        import zeroai  # noqa: F401
        checks.append({"name": "package:zeroai", "ok": True,
                       "detail": getattr(zeroai, "__version__", "unknown")})
    except Exception as exc:  # noqa: BLE001
        problems += 1
        checks.append({"name": "package:zeroai", "ok": False, "detail": repr(exc)})

    # 凭据 / 代理状态（只报告是否存在，绝不回显内容）
    # credential 属"警告"级而非"致命"级：只要代理可用，客户端不需要
    # 上游真实 Key 也能工作，所以它不参与退出码判定。
    try:
        from zeroai.core.secrets import PROXY_CONFIG, _get_api_key
        glm_key = _get_api_key("glm", "")
        proxy_ok = bool(PROXY_CONFIG.get("enabled") and PROXY_CONFIG.get("base_url")
                        and PROXY_CONFIG.get("token"))
        cred_ok = bool(glm_key) or proxy_ok
        if not cred_ok:
            warnings += 1
        checks.append({
            "name": "credential:glm", "ok": cred_ok, "warn": True,
            "detail": ("已提供" if glm_key else "缺失") +
                      f"；代理={'可用' if proxy_ok else '不可用'}",
        })
        checks.append({"name": "proxy:builtin", "ok": True,
                       "detail": f"enabled={PROXY_CONFIG.get('enabled')} "
                                 f"host={PROXY_CONFIG.get('base_url', '')}"})
    except Exception as exc:  # noqa: BLE001
        warnings += 1
        checks.append({"name": "credential:glm", "ok": False, "warn": True,
                       "detail": repr(exc)})

    # 可用模型
    try:
        from zeroai.core.constants import MODEL_CONFIGS
        checks.append({"name": "models", "ok": True, "detail": ",".join(sorted(MODEL_CONFIGS))})
    except Exception as exc:  # noqa: BLE001
        problems += 1
        checks.append({"name": "models", "ok": False, "detail": repr(exc)})

    envelope = {"ok": problems == 0, "action": "check", "checks": checks,
                "problems": problems, "warnings": warnings,
                "exit_code": EXIT_OK if problems == 0 else EXIT_USAGE}

    if as_json:
        print(json.dumps(envelope, ensure_ascii=False, default=str), flush=True)
    else:
        for c in checks:
            if c["ok"]:
                mark = "OK  "
            elif c.get("warn"):
                mark = "WARN"
            else:
                mark = "FAIL"
            print(f"[{mark}] {c['name']:<22} {c['detail']}")
        total = len(checks)
        print(f"\n自检完成：{total - problems - warnings} 通过 / {warnings} 警告 / "
              f"{problems} 失败", file=sys.stderr)
    return int(envelope["exit_code"])


# ---------------------------------------------------------------------------
# Agent 执行
# ---------------------------------------------------------------------------
def _resolve_model(model: Optional[str]) -> str:
    """决定使用哪个模型键。优先级：--model > 环境变量 > 内置默认 glm。"""
    if model:
        return model
    env_model = (os.environ.get("ZEROAI_MODEL") or "").strip()
    if env_model:
        return env_model
    return "glm"


def _resolve_credentials(model_key: str, require_key: bool = True) -> Optional[str]:
    """返回缺失凭据的错误说明；凭据齐备时返回 None。

    Args:
        model_key: 模型键
        require_key: 是否要求凭据必须齐备。--dry-run 传 False：
                     仍然校验模型名是否存在，但不要求一定有 Key。
    """
    try:
        from zeroai.core.constants import MODEL_CONFIGS
        from zeroai.core.secrets import PROXY_CONFIG, _get_api_key
    except Exception as exc:  # noqa: BLE001
        return f"无法加载模型配置：{exc!r}"

    if model_key not in MODEL_CONFIGS:
        return (f"模型 '{model_key}' 不存在。可用：{', '.join(sorted(MODEL_CONFIGS))}")

    if not require_key:
        return None

    # 代理模式：客户端只需要代理 Token，不需要上游真实 Key
    if PROXY_CONFIG.get("enabled") and PROXY_CONFIG.get("token") and PROXY_CONFIG.get("base_url"):
        return None

    if not _get_api_key(model_key, ""):
        env_name = f"ZEROAI_API_KEY_{model_key.upper()}"
        return (f"模型 '{model_key}' 缺少 API Key。请设置环境变量 {env_name}，"
                f"或在 ~/.zeroai_config.json 中配置。")
    return None


async def _run_agent(task: str, model_key: str, max_steps: int, workspace_root: str,
                     quiet: bool, json_mode: bool, diff_review: bool = False,
                     action_fusion: bool = False, observation_pack: bool = False) -> Dict[str, Any]:
    """真正驱动 AgentLoop。导入放在函数内，保证 --check / --dry-run 不被拖慢。"""
    # 通过模块属性延迟取值：既保持懒加载，也让测试可以 monkeypatch
    # （monkeypatch zeroai.core.agent.ReActPlanner 后，这里取到的就是新值）
    from zeroai.core import agent as agent_mod

    def _progress(msg: str) -> None:
        if not quiet:
            _eprint(msg)

    _progress(f"[zeroai] 模型={model_key} 最大步数={max_steps} 工作目录={workspace_root}")

    planner = agent_mod.ReActPlanner(model_key=model_key)
    loop = agent_mod.AgentLoop(
        planner=planner,
        max_steps=max_steps,
        workspace_root=workspace_root,
        enable_checkpoint=False,   # 无头模式默认不建检查点，避免污染目标目录
        enable_cost_tracking=True,
        enable_session=False,      # 一次性任务不落盘会话
        enable_task_manager=False,
        enable_diff_review=diff_review,
        diff_review_callback=(_make_diff_review_callback(quiet) if diff_review else None),
        enable_action_fusion=action_fusion,
        enable_observation_pack=observation_pack,
    )

    async def _on_thought(text: str) -> None:
        _progress(f"[think] {_truncate(text, 400)}")

    async def _on_tool_call(name: str, args: Dict[str, Any]) -> None:
        _progress(f"[tool ] {name} {_truncate(args, 300)}")

    async def _on_tool_result(name: str, result: str) -> None:
        _progress(f"[result] {name} -> {_truncate(result, 300)}")

    loop.on_thought = _on_thought
    loop.on_tool_call = _on_tool_call
    loop.on_tool_result = _on_tool_result

    messages: List[Dict[str, Any]] = []
    answer, steps = await loop.run(task, messages)

    cost_report = None
    try:
        llm = getattr(planner, "llm", None)
        if llm is not None and getattr(llm, "cost_tracker", None) is not None:
            cost_report = llm.get_cost_report()
    except Exception:  # noqa: BLE001
        cost_report = None

    return {"answer": answer, "steps": steps, "cost_report": cost_report}


class _TaskTimeout(Exception):
    """内部信号：任务超过 --timeout 仍未结束。"""


# 便于测试替换：生产环境就是 os._exit，测试里换成记录器，
# 以免测试进程自己被干掉。
_hard_exit = os._exit


def _validate_runtime_args(args: argparse.Namespace, task: str) -> Optional[str]:
    """在动手之前把参数边界卡死。

    之前这些边界全都不校验，后果是错误被推迟到运行期，而且表现为
    似是而非的退出码（比如 --timeout -5 会抛 ValueError 被吞成 exit 1，
    而不是它该有的 2）。
    """
    if not task:
        return "必须通过 --task/-t 提供任务内容。"
    if args.max_steps < 1:
        return f"--max-steps 必须 >= 1，实际为 {args.max_steps}。"
    if not (args.timeout > 0 and args.timeout != float("inf")):
        return f"--timeout 必须是有限正数，实际为 {args.timeout}。"
    cwd = args.cwd or os.getcwd()
    if not os.path.isdir(cwd):
        return f"--cwd 指向的目录不存在：{cwd}"
    return None


def _make_diff_review_callback(quiet: bool) -> Callable[[str, Dict[str, Any]], bool]:
    """构造 diff 审批回调（同步函数，AgentLoop 在工具执行前调用）。

    安全策略 fail-closed：
    - 交互终端：打印写入目标与参数预览，人工 y/N 决定
    - 非交互终端（管道/CI/< nul）：自动拒绝并提示如何关闭审批
      （Windows 上不能只信 stdin.isatty()——它对 NUL 设备返回 True，
      这里复用 main._is_console_stream 的 GetConsoleMode 检查）
    """

    def _cb(tool_name: str, tool_args: Dict[str, Any]) -> bool:
        try:
            from zeroai.main import _is_console_stream
            interactive = _is_console_stream(sys.stdin)
        except Exception:  # noqa: BLE001
            interactive = sys.stdin.isatty()

        path = ""
        for k in ("path", "file_path", "filepath", "file", "path_or_pattern", "target"):
            v = tool_args.get(k)
            if isinstance(v, str) and v.strip():
                path = v.strip()
                break

        if not interactive:
            _eprint(f"[diff-review] 非交互终端：自动拒绝 {tool_name} -> {path}"
                    f"（如需无审批写入，请去掉 --diff-review）")
            return False

        _eprint(f"[diff-review] {tool_name} 请求写入: {path}")
        _eprint(f"  args: {_truncate(tool_args, 300)}")
        try:
            ans = input("  批准? [y/N] ").strip().lower()
        except (EOFError, OSError):
            return False
        return ans in ("y", "yes")

    return _cb


def _execute_with_hard_timeout(task: str, model_key: str, max_steps: int,
                               workspace_root: str, quiet: bool,
                               timeout: float, diff_review: bool = False,
                               action_fusion: bool = False,
                               observation_pack: bool = False) -> Dict[str, Any]:
    """在独立线程里跑 Agent，用 join(timeout) 实现**硬超时**。

    为什么不用 asyncio.wait_for：
        它只能取消「协程」，取消不掉已经跑到线程池里的同步工具
        （例如 command_exec 内部的 subprocess.run）；而且 asyncio.run 收尾时
        会等待默认线程池结束，于是"超时"会被实际拖长，--timeout 形同虚设。
    这里让 Agent 跑在自己的 daemon 线程里，主线程只负责等；一旦超时，
    调用方写完结果就直接结束进程，超时才是真正落地的保证。
    """
    import threading

    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = asyncio.run(
                _run_agent(task, model_key, max_steps, workspace_root, quiet, False,
                           diff_review=diff_review, action_fusion=action_fusion,
                           observation_pack=observation_pack))
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    worker = threading.Thread(target=_worker, name="zeroai-agent", daemon=True)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        raise _TaskTimeout()
    if "error" in box:
        raise box["error"]
    return box["result"]  # type: ignore[return-value]


def run_task(args: argparse.Namespace) -> int:
    task = (args.task or "").strip()
    model_key = _resolve_model(args.model)

    arg_err = _validate_runtime_args(args, task)
    if arg_err:
        code = "empty_task" if not task else "invalid_argument"
        return _fail(code, arg_err, args.json, EXIT_USAGE)

    cred_err = _resolve_credentials(model_key, require_key=not args.dry_run)
    if cred_err:
        return _fail("credential_error", cred_err, args.json, EXIT_USAGE)

    workspace_root = os.path.abspath(args.cwd or os.getcwd())

    if args.dry_run:
        envelope = {
            "ok": True, "action": "dry-run", "task": task, "model": model_key,
            "max_steps": args.max_steps, "timeout": args.timeout,
            "workspace_root": workspace_root,
            "credential_note": cred_err or "ok",
            "would_run": "zeroai.core.agent.AgentLoop.run",
            "exit_code": EXIT_OK,
        }
        _emit_envelope(envelope, args.json)
        if not args.json:
            _eprint("[dry-run] 未调用任何网络接口。")
        return EXIT_OK

    started = time.monotonic()
    try:
        result = _execute_with_hard_timeout(task, model_key, args.max_steps,
                                            workspace_root, args.quiet, args.timeout,
                                            diff_review=bool(getattr(args, "diff_review", False)),
                                            action_fusion=bool(getattr(args, "action_fusion", False)),
                                            observation_pack=bool(getattr(args, "observation_pack", False)))
    except _TaskTimeout:
        _fail("timeout", f"任务在 {args.timeout}s 内未完成，已中止。", args.json,
              EXIT_TIMEOUT, extra={"task": task, "model": model_key})
        # 硬超时兜底：Agent 线程里可能仍卡着同步工具，正常退出会被它拖住
        # （asyncio 收尾会等待线程池），所以写完结果后直接结束进程。
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        _hard_exit(EXIT_TIMEOUT)
        return EXIT_TIMEOUT
    except KeyboardInterrupt:
        return _fail("interrupted", "用户中断。", args.json, EXIT_INTERRUPTED,
                     extra={"task": task, "model": model_key})
    except Exception as exc:  # noqa: BLE001
        return _fail("runtime_error", f"{type(exc).__name__}: {exc}", args.json,
                     EXIT_TASK_FAILED, extra={"task": task, "model": model_key})

    elapsed = round(time.monotonic() - started, 3)
    answer = result["answer"] or ""
    steps = result["steps"] or []
    hit_max_steps = _MAX_STEPS_SENTINEL in answer

    envelope = {
        "ok": not hit_max_steps,
        "action": "task",
        "task": task,
        "model": model_key,
        "answer": answer,
        "steps_count": len(steps),
        "steps": [
            {
                "index": i,
                "thought": _truncate(s.get("thought"), 500),
                "action_type": s.get("action_type"),
                "tool_name": s.get("tool_name"),
                "args": s.get("args"),
                "result": _truncate(s.get("result")),
            }
            for i, s in enumerate(steps, 1)
        ],
        "elapsed_sec": elapsed,
        "cost_report": result.get("cost_report"),
        "exit_code": EXIT_TASK_FAILED if hit_max_steps else EXIT_OK,
    }
    if hit_max_steps:
        envelope["error"] = {"code": "max_steps_exceeded",
                             "message": f"达到最大步数 {args.max_steps}，任务未完成。"}

    _emit_envelope(envelope, args.json)
    if not args.json:
        _eprint(f"[zeroai] 完成，用时 {elapsed}s，步数 {len(steps)}")
    return int(envelope["exit_code"])


def _fail(code: str, message: str, as_json: bool, exit_code: int,
          extra: Optional[Dict[str, Any]] = None) -> int:
    envelope: Dict[str, Any] = {"ok": False, "error": {"code": code, "message": message},
                                "exit_code": exit_code}
    if extra:
        envelope.update(extra)
    _emit_envelope(envelope, as_json)
    return exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser(prog: str = "zeroai") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="ZeroAI 无头模式：一条命令完成「输入任务 → 执行 → 输出结果」",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0=成功 1=任务未完成 2=用法/配置错误 124=超时 130=中断",
    )
    parser.add_argument("-t", "--task", type=str, default=None,
                        help="要执行的任务文本（无头模式的必填项）")
    parser.add_argument("--model", type=str, default=None,
                        help="模型键（默认取 $ZEROAI_MODEL，否则 glm）")
    parser.add_argument("--max-steps", type=int, default=8, dest="max_steps",
                        help="Agent 循环最大步数（默认 8）")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="整体超时秒数（默认 300）")
    parser.add_argument("--cwd", type=str, default=None,
                        help="工作目录，工具的相对路径基准（默认当前目录）")
    parser.add_argument("--json", action="store_true",
                        help="stdout 输出单行 JSON 信封（便于管道与测试）")
    parser.add_argument("--quiet", action="store_true", help="抑制 stderr 过程输出")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="只校验配置，不调用任何网络接口")
    parser.add_argument("--diff-review", action="store_true", dest="diff_review",
                        help="文件写入类工具执行前逐个审批；非交互终端下自动拒绝（fail-closed）")
    parser.add_argument("--action-fusion", action="store_true", dest="action_fusion",
                        help="启用 Action Fusion：edit/write 后自动执行跟进验证命令，"
                             "结果合并进同一次 observation（省一轮模型往返）")
    parser.add_argument("--observation-pack", action="store_true", dest="observation_pack",
                        help="启用 ObservationPack：工具输出超过 2000 字符时完整落盘归档，"
                             "observation 替换为预览+归档路径（原始结果零截断，可分页召回）")
    parser.add_argument("--check", action="store_true",
                        help="环境自检（依赖/凭据/模型），秒级返回且不触网")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _configure_std_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return run_check(as_json=args.json)
    if args.task is None:
        parser.print_help(sys.stderr)
        _eprint("\n[错误] 无头模式需要 --task/-t；如需自检请用 --check。")
        return EXIT_USAGE
    return run_task(args)


if __name__ == "__main__":
    raise SystemExit(main())
