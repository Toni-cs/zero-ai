"""MCP 面板：挂载后后台初始化 MCP 工具并把结果提示给用户，/mcp 命令交互（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import asyncio
from rich.text import Text
from zeroai.tui.colors import C_CYAN, C_DIM, C_FG, C_YELLOW


class MCPCommandMixin:
    """MCP 面板：挂载后后台初始化 MCP 工具并把结果提示给用户，/mcp 命令交互"""

    def _auto_init_mcp(self) -> None:
        """后台异步初始化 MCP 工具

        读取 ~/.zeroai/mcp_config.json，连接所有 enabled 的服务器。
        首次启动或无配置时静默跳过，不影响主程序。
        """
        try:
            from zeroai.mcp import get_mcp_config
            config = get_mcp_config()
            servers = config.list_servers(only_enabled=True)
            if not servers:
                return  # 无配置，静默跳过

            # 异步执行初始化
            async def _do_init():
                try:
                    from zeroai.mcp import initialize_mcp_tools
                    # 【2026-09-16】超时从 10s 提到 30s。实测电脑操作类 MCP 服务器
                    # （computer-control-mcp，走 uvx 冷启动 + 加载 ONNX 模型）握手需
                    # 6.3–6.7s，10s 只剩约 3s 余量；首次冷缓存构建依赖时长达数分钟。
                    # 该参数是 asyncio.wait_for 的**上限**而非固定等待，
                    # 服务器响应快时不会增加任何启动延迟，故加大无副作用。
                    result = await initialize_mcp_tools(timeout_per_server=30.0)
                    # 【修复 2026-09-16】此前只在 tools_added > 0 时提示，且
                    # 外层是裸 `except Exception: pass` —— 后果是"uvx 不在 PATH /
                    # 服务器启动失败 / 配置写错"这类问题**完全静默**：用户只会
                    # 觉得"AI 不会用电脑"，无从排查。现在成功与失败都提示，
                    # 且仍不阻塞主程序（失败只显示，不抛出）。
                    self.call_after_refresh(lambda: self._notify_mcp_init(result))
                except Exception as e:
                    # 不再静默：把原因显示出来
                    self.call_after_refresh(lambda: self._notify_mcp_error(e))

            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_do_init())
                else:
                    loop.run_until_complete(_do_init())
            except RuntimeError:
                pass  # 无事件循环，跳过
        except Exception:
            pass  # MCP 模块导入失败，静默跳过
    def _notify_mcp_init(self, result: dict) -> None:
        """显示 MCP 初始化结果（成功与失败都提示）。

        【修复 2026-09-16】此前只在 ``tools_added > 0`` 时提示，服务器连接失败
        则完全静默。现在把 ``failed`` 列表也展示出来，用户能立刻知道是哪个
        服务器、什么原因（如 `uvx` 不在 PATH）。
        """
        try:
            added = int(result.get("tools_added") or 0)
            connected = result.get("connected") or []
            failed = result.get("failed") or []
            if not added and not failed:
                return
            lines = []
            if added:
                lines.append(f"  │ ✓ MCP 工具已加载：{added} 个\n")
                lines.append(f"  │   连接 {len(connected)} 个服务器\n")
            for srv, err in failed:
                lines.append(f"  │ ✗ {srv}: {str(err)[:60]}\n")
            self._add_static(Text.assemble(
                ("  ┌──────────────────────────────────┐\n", f"bold {C_FG}"),
                ("".join(lines), f"bold {C_FG}"),
                ("  └──────────────────────────────────┘\n", f"bold {C_FG}"),
            ))
        except Exception:
            pass  # 提示失败绝不影响主程序
    def _notify_mcp_error(self, exc: Exception) -> None:
        """显示 MCP 初始化异常（此前是裸 except: pass，完全静默）。"""
        try:
            self._add_static(Text.assemble(
                ("  ┌──────────────────────────────────┐\n", f"bold {C_FG}"),
                (f"  │ ✗ MCP 初始化失败：{type(exc).__name__}\n", f"bold {C_FG}"),
                (f"  │   {str(exc)[:60]}\n", f"bold {C_FG}"),
                ("  └──────────────────────────────────┘\n", f"bold {C_FG}"),
            ))
        except Exception:
            pass
    async def _handle_mcp_command(self, cmd: str):
        """处理 /mcp 命令系统

        子命令：
            /mcp                  显示 MCP 状态
            /mcp list             列出预设服务器
            /mcp install <name>   安装预设（如 filesystem）
            /mcp uninstall <name> 卸载服务器
            /mcp connect          连接所有已启用服务器
            /mcp disconnect       断开所有连接
            /mcp tools            列出已加载的 MCP 工具
            /mcp enable <name>    启用服务器
            /mcp disable <name>   禁用服务器
        """
        from zeroai.mcp import (
            get_mcp_config, get_mcp_registry,
            initialize_mcp_tools, shutdown_mcp_tools,
            list_presets, install_preset, uninstall_preset,
            check_preset_dependencies,
        )

        if not cmd or cmd in ("status", "状态"):
            # 显示 MCP 状态
            registry = get_mcp_registry()
            config = get_mcp_config()
            servers = config.list_servers()
            enabled_count = sum(1 for s in servers if s.enabled)

            status_lines = [
                ("  MCP 协议支持\n", f"bold {C_YELLOW}"),
                (f"  已配置服务器：{len(servers)} 个（{enabled_count} 个启用）\n", C_DIM),
                (f"  注册器状态：{'已初始化' if registry.is_initialized else '未初始化'}\n", C_DIM),
            ]

            if registry.is_initialized:
                status_text = registry.get_status_text()
                for line in status_text.split("\n"):
                    status_lines.append((f"  {line}\n", C_FG))
            else:
                status_lines.append((
                    "  输入 /mcp connect 初始化 MCP 工具\n", C_DIM
                ))

            status_lines.append(("  ───────────────────────────\n", C_DIM))
            status_lines.append(("  /mcp list     查看可用预设\n", C_FG))
            status_lines.append(("  /mcp install <name>  安装预设\n", C_FG))
            status_lines.append(("  /mcp connect  连接所有服务器\n", C_FG))
            status_lines.append(("  /mcp tools    查看已加载工具\n", C_FG))

            self._add_static(Text.assemble(*status_lines))
            return

        parts = cmd.split(maxsplit=1)
        action = parts[0].lower()

        if action in ("list", "ls", "预设", "presets"):
            # 列出预设
            presets = list_presets()
            lines = [("  可用 MCP 服务器预设\n", f"bold {C_YELLOW}")]
            for p in presets:
                status_icon = "✓" if p["dependencies_available"] else "✗"
                installed_icon = "+" if p["installed"] else " "
                color = C_FG if p["dependencies_available"] else C_DIM
                lines.append((
                    f"  {status_icon} [{installed_icon}] {p['name']:<20} {p['description']}\n",
                    color,
                ))
                if not p["dependencies_available"]:
                    lines.append((f"      └─ {p['install_hint']}\n", C_DIM))
            lines.append(("\n  说明：✓=依赖可用  ✗=缺失  [+=已安装]\n", C_DIM))
            lines.append(("  安装：/mcp install <名称>\n", C_FG))
            self._add_static(Text.assemble(*lines))
            return

        if action in ("install", "安装"):
            if len(parts) < 2:
                self._add_static(Text.assemble(
                    ("  用法：/mcp install <预设名> [参数]\n", C_DIM),
                    ("  示例：/mcp install filesystem paths=D:\\C\\C\n", C_FG),
                    ("        /mcp install fetch\n", C_FG),
                    ("        /mcp install sqlite db_path=test.db\n", C_FG),
                ))
                return

            args_str = parts[1]
            arg_parts = args_str.split(maxsplit=1)
            preset_name = arg_parts[0]
            extra_args = {}

            if len(arg_parts) > 1:
                # 解析 key=value 格式
                for kv in arg_parts[1].split():
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        extra_args[k.strip()] = v.strip()

            # 检查依赖
            dep = check_preset_dependencies(preset_name)
            if not dep["available"]:
                self._add_static(Text.assemble(
                    (f"  ✗ 依赖缺失：{dep['missing']}\n", f"bold {C_YELLOW}"),
                    (f"  {dep['install_hint']}\n", C_DIM),
                ))
                return

            # 安装
            if install_preset(preset_name, extra_args=extra_args):
                self._add_static(Text.assemble(
                    (f"  ✓ 已安装预设：{preset_name}\n", f"bold {C_FG}"),
                    ("  输入 /mcp connect 连接服务器\n", C_DIM),
                ))
            else:
                self._add_static(Text.assemble(
                    (f"  ✗ 安装失败：{preset_name}\n", f"bold {C_YELLOW}"),
                ))
            return

        if action in ("uninstall", "卸载", "remove"):
            if len(parts) < 2:
                self._add_static(Text.assemble(
                    ("  用法：/mcp uninstall <服务器名>\n", C_DIM),
                ))
                return
            name = parts[1].strip()
            if uninstall_preset(name):
                self._add_static(Text.assemble(
                    (f"  ✓ 已卸载：{name}\n", f"bold {C_FG}"),
                ))
            else:
                self._add_static(Text.assemble(
                    (f"  ✗ 未找到服务器：{name}\n", f"bold {C_YELLOW}"),
                ))
            return

        if action in ("connect", "连接", "init"):
            # 连接所有已启用的 MCP 服务器
            block = self._add_block("MCP 初始化", C_CYAN)
            block.update(Text.assemble(
                (f"  正在连接 MCP 服务器…\n", f"bold {C_CYAN}"),
            ))
            try:
                # 【2026-09-16】同 _auto_init_mcp：uvx 类服务器冷启动较慢，提到 30s。
                # 上限而非固定等待，不影响快速服务器。
                result = await initialize_mcp_tools(timeout_per_server=30.0)
                lines = [
                    (f"  MCP 工具初始化完成\n", f"bold {C_FG}"),
                    (f"  成功连接：{len(result['connected'])} 个服务器\n", C_FG),
                ]
                if result["connected"]:
                    lines.append(("    " + ", ".join(result["connected"]) + "\n", C_DIM))
                if result["failed"]:
                    lines.append((f"  连接失败：{len(result['failed'])} 个\n", f"bold {C_YELLOW}"))
                    for name, err in result["failed"]:
                        lines.append((f"    {name}: {err}\n", C_DIM))
                lines.append((f"  加载工具：{result['tools_added']} 个\n", f"bold {C_FG}"))
                if result["tools_added"] > 0:
                    lines.append(("  MCP 工具已注入 TOOL_MAP，Agent 可透明调用\n", C_DIM))
                self._add_static(Text.assemble(*lines))
            except Exception as e:
                self._add_static(Text.assemble(
                    ("  └─ MCP 初始化错误：", C_DIM),
                    (f"{e}\n", f"bold {C_YELLOW}"),
                ))
            return

        if action in ("disconnect", "断开", "shutdown"):
            try:
                await shutdown_mcp_tools()
                self._add_static(Text.assemble(
                    ("  ✓ 已断开所有 MCP 连接\n", f"bold {C_FG}"),
                ))
            except Exception as e:
                self._add_static(Text.assemble(
                    ("  └─ 断开错误：", C_DIM),
                    (f"{e}\n", f"bold {C_YELLOW}"),
                ))
            return

        if action in ("tools", "工具"):
            # 列出已加载的 MCP 工具
            registry = get_mcp_registry()
            if not registry.is_initialized:
                self._add_static(Text.assemble(
                    ("  MCP 未初始化，输入 ", C_DIM),
                    ("/mcp connect", f"bold {C_FG}"),
                    (" 连接服务器\n", C_DIM),
                ))
                return
            tools = registry.get_tools_schema()
            if not tools:
                self._add_static(Text.assemble(
                    ("  无 MCP 工具\n", C_DIM),
                ))
                return
            lines = [(f"  MCP 工具列表（{len(tools)} 个）\n", f"bold {C_YELLOW}")]
            for t in tools[:30]:
                fn = t.get("function", {})
                lines.append((f"  • {fn.get('name', '?')}\n", C_FG))
            if len(tools) > 30:
                lines.append((f"  ... 还有 {len(tools) - 30} 个\n", C_DIM))
            self._add_static(Text.assemble(*lines))
            return

        if action in ("enable", "启用"):
            if len(parts) < 2:
                self._add_static(Text.assemble(("  用法：/mcp enable <名称>\n", C_DIM)))
                return
            name = parts[1].strip()
            if get_mcp_config().enable_server(name):
                self._add_static(Text.assemble((f"  ✓ 已启用：{name}\n", f"bold {C_FG}")))
            else:
                self._add_static(Text.assemble((f"  ✗ 未找到：{name}\n", f"bold {C_YELLOW}")))
            return

        if action in ("disable", "禁用"):
            if len(parts) < 2:
                self._add_static(Text.assemble(("  用法：/mcp disable <名称>\n", C_DIM)))
                return
            name = parts[1].strip()
            if get_mcp_config().disable_server(name):
                self._add_static(Text.assemble((f"  ✓ 已禁用：{name}\n", f"bold {C_FG}")))
            else:
                self._add_static(Text.assemble((f"  ✗ 未找到：{name}\n", f"bold {C_YELLOW}")))
            return

        # 未知命令
        self._add_static(Text.assemble(
            (f"  未知 MCP 命令：{action}\n", f"bold {C_YELLOW}"),
            ("  输入 /mcp 查看所有命令\n", C_DIM),
        ))
