"""输入分发：on_input_submitted 把用户输入路由到斜杠命令 / 普通对话 / 图片粘贴（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import os
import re
from pathlib import Path
from rich.text import Text
from textual.widgets import Input
from zeroai.core.agents_md import _auto_generate_agents_md
from zeroai.core.constants import EXPERT_TEAM, MODEL_CONFIGS, set_work_mode
from zeroai.core.paths import WORK_DIR
from zeroai.core.model_manager import get_model_display_name
from zeroai.tools.file_manager import read_image
from zeroai.tools.render import _safe_markdown, render_image_preview, render_latex_in_text
from zeroai.tools.security import security_audit
from zeroai.tools.voice import speak_tts
from zeroai.tui.colors import C_BLUE, C_CYAN, C_DIM, C_FG, C_RED, C_YELLOW
from zeroai.tui.icons import _load_svg_icon
from zeroai.tui.widgets import InfoBar, MessageInput


class CommandMixin:
    """输入分发：on_input_submitted 把用户输入路由到斜杠命令 / 普通对话 / 图片粘贴"""

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        if not user_input:
            return

        # ── 硬编码高频身份问题：避免任何模型调用，避免污染对话上下文 ──
        _identity_question_pattern = re.compile(
            r"^(你是什么模型|你是谁|你的模型是什么|你叫什么|你是什么|你叫啥|你是哪个模型|你的底层模型|你基于什么模型)[?？]*$"
        )
        if _identity_question_pattern.match(user_input.strip()):
            standard_reply = "我是 ZeroAI，一个终端 AI 编程助手。"
            self.messages.append({"role": "user", "content": user_input})
            self.messages.append({"role": "assistant", "content": standard_reply})
            self._add_block("构建 · 通用", C_RED)
            self._add_static(Text(standard_reply, style=C_FG))
            self._add_static(Text("  └─", style=C_DIM))
            self._last_reply_text = standard_reply
            self._last_reply_code_blocks = []
            self.query_one("#input", MessageInput).value = ""
            self._keep_input_focus()
            return

        # 命令（中英文双语支持）
        if user_input in ("/exit", "/quit", "/退出"):
            self.exit()
            return
        if user_input in ("/clear", "/清屏"):
            self.action_clear_log()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/new", "/新对话"):
            self.action_clear_history()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/help", "/帮助"):
            self._show_help()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/copy", "/复制"):
            self.action_copy_last_reply()
            self.query_one("#input", MessageInput).value = ""
            return
        # /copy N 或 /复制 N：复制最近回复中第 N 个代码块
        m = re.match(r"^/(?:copy|复制)\s+(\d+)$", user_input.strip())
        if m:
            n = int(m.group(1))
            self._copy_code_block_by_index(n)
            self.query_one("#input", MessageInput).value = ""
            return
        # /copy list 或 /复制 列表：列出最近回复中的所有代码块
        if user_input.strip() in ("/copy list", "/复制 列表", "/copy ls"):
            self._list_code_blocks()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/image", "/img", "/图片", "/粘贴图片"):
            self.action_paste_image()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/voice", "/语音", "/朗读"):
            # 切换 TTS 朗读开关
            self._tts_enabled = not self._tts_enabled
            if self._tts_enabled:
                self._add_static(Text.assemble(
                    ("  🎤 语音朗读已开启\n", f"bold {C_FG}"),
                    ("  AI 回复后将自动朗读（按 Ctrl+T 说话输入）\n", C_DIM),
                    (f"  当前音色：{self._tts_voice} · 语速：{self._tts_rate}\n", C_DIM),
                    ("  再次输入 /语音 关闭\n", C_DIM),
                ))
                self.notify("语音朗读已开启")
            else:
                self._add_static(Text("  🎤 语音朗读已关闭\n", style=C_DIM))
                self.notify("语音朗读已关闭")
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/dialog", "/对话"):
            # 切换语音对话模式
            self.action_voice_dialog()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/stop", "/停止"):
            # 停止语音对话模式
            if self._voice_dialog_active:
                self._voice_dialog_active = False
                self._add_static(Text.assemble(
                    ("  🎤 语音对话已停止\n", f"bold {C_FG}"),
                    ("  （再次按 Ctrl+D 或输入 /对话 重新开始）\n", C_DIM),
                ))
                self.notify("语音对话已停止")
            else:
                self._add_static(Text("  （当前未开启语音对话模式）\n", style=C_DIM))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/voice_female", "/女声"):
            self._tts_voice = "zh-CN-XiaoxiaoNeural"
            self._add_static(Text.assemble(
                ("  🎤 音色已切换为：", C_DIM),
                ("女声（晓晓）\n", f"bold {C_FG}"),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/voice_male", "/男声"):
            self._tts_voice = "zh-CN-YunxiNeural"
            self._add_static(Text.assemble(
                ("  🎤 音色已切换为：", C_DIM),
                ("男声（云希）\n", f"bold {C_FG}"),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input.startswith("/voice_rate ") or user_input.startswith("/语速 "):
            # 设置语速：/语速 +10% 或 /语速 -10%
            rate = user_input.split(" ", 1)[1].strip()
            if rate.startswith(("+", "-")) and rate.endswith("%"):
                self._tts_rate = rate
                self._add_static(Text.assemble(
                    ("  🎤 语速已设置为：", C_DIM),
                    (f"{rate}\n", f"bold {C_FG}"),
                ))
            else:
                self._add_static(Text.assemble(
                    ("  格式错误，正确格式：", C_DIM),
                    ("/语速 +10%\n", C_FG),
                ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/init", "/初始化", "/项目初始化"):
            # 仿 OpenCode：分析项目结构，生成 AGENTS.md
            self._init_project_agents()
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/audit", "/安全", "/安全审计", "/漏洞扫描"):
            # 对当前工作目录执行完整安全审计
            self._add_block("安全审计", C_YELLOW)
            self._add_static(Text.assemble(
                (f"  {_load_svg_icon('search')} 正在对当前项目进行安全审计…\n", f"bold {C_YELLOW}"),
                ("  │ 扫描项：代码漏洞 / 敏感信息 / 依赖漏洞 / 配置安全\n", C_DIM),
            ))
            report = security_audit(WORK_DIR, "all")
            try:
                self._add_static(_safe_markdown(report, code_theme="monokai"))
            except Exception:
                self._add_static(Text(report, style=C_FG))
            self._add_static(Text("  └─", style=C_DIM))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/ssh", "/SSH", "/远程", "/部署"):
            self._add_block("SSH 远程部署 + AI 远程运维", C_YELLOW)
            self._add_static(Text.assemble(
                (f"  {_load_svg_icon('terminal')} SSH 远程部署 + AI 远程运维（15 个工具）\n", f"bold {C_YELLOW}"),
                ("  │ 部署能力 + 运维能力 双重合一\n", C_DIM),
                ("\n  部署工具（7 个）：\n", f"bold {C_FG}"),
                ("    • ssh_connect      连接服务器（密码/密钥认证）\n", C_FG),
                ("    • ssh_exec         远程执行命令（危险命令二次确认）\n", C_FG),
                ("    • ssh_upload       上传文件（SFTP）\n", C_FG),
                ("    • ssh_download    下载文件（SFTP）\n", C_FG),
                ("    • ssh_deploy      一键自动化部署（7 步骤）\n", C_FG),
                ("    • ssh_list        查看连接状态/审计日志\n", C_FG),
                ("    • ssh_disconnect  断开连接\n", C_FG),
                ("\n  运维工具（8 个，语义化封装，AI 优先调用）：\n", f"bold {C_FG}"),
                ("    • ssh_service_manage  服务管理（status/start/stop/restart）\n", C_FG),
                ("    • ssh_log_view        日志查看（自动异常统计）\n", C_FG),
                ("    • ssh_process_check   进程查看（按 CPU/内存排序）\n", C_FG),
                ("    • ssh_disk_analyze    磁盘分析（df+du Top10）\n", C_FG),
                ("    • ssh_network_diag    网络诊断（端口/ping/连接）\n", C_FG),
                ("    • ssh_docker_manage   Docker 管理（容器/镜像/日志）\n", C_FG),
                ("    • ssh_firewall_manage 防火墙管理（ufw/firewalld/iptables）\n", C_FG),
                ("    • ssh_health_check    一键健康体检（综合报告+AI分析）\n", C_FG),
                ("\n  使用方式：直接告诉 AI 你的需求，例如：\n", f"bold {C_FG}"),
                ("    「连接到 192.168.10.20，用户 root，密码 xxx」\n", C_DIM),
                ("    「看下 nginx 状态」→ ssh_service_manage\n", C_DIM),
                ("    「服务器卡了」→ ssh_health_check 综合体检\n", C_DIM),
                ("    「查 mysql 错误日志」→ ssh_log_view(keyword=error)\n", C_DIM),
                ("    「看磁盘占用」→ ssh_disk_analyze\n", C_DIM),
                ("    「重启 web 容器」→ ssh_docker_manage(action=restart)\n", C_DIM),
                ("    「开放 8080 端口」→ ssh_firewall_manage(action=open, port=8080)\n", C_DIM),
                ("    「一键部署：上传项目→安装依赖→重启服务」\n", C_DIM),
                ("\n  安全保障：\n", f"bold {C_FG}"),
                ("    • 危险命令黑名单（rm -rf /、mkfs、dd 等 11 类）\n", C_DIM),
                ("    • 主机地址校验（IP/域名格式+内网IP可选阻断）\n", C_DIM),
                ("    • 审计日志（最多 200 条，可追溯）\n", C_DIM),
                ("    • 输出截断保护（8000 字符，防止刷屏）\n", C_DIM),
            ))
            self._add_static(Text("  └─", style=C_DIM))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/expert", "/专家"):
            self.work_mode = "expert"
            set_work_mode("expert")
            self.query_one("#info", InfoBar).refresh()
            self._add_static(Text.assemble(
                ("  已切换到 ", C_DIM),
                ("专家模式", f"bold {C_FG}"),
                ("（自动路由最合适的专家模型）\n", C_DIM),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/hybrid", "/混合"):
            self.work_mode = "hybrid"
            set_work_mode("hybrid")
            self.query_one("#info", InfoBar).refresh()
            self._add_static(Text.assemble(
                ("  已切换到 ", C_DIM),
                ("混合思考", f"bold {C_FG}"),
                ("（多专家协作，深度处理）\n", C_DIM),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/manual", "/手动"):
            self.work_mode = "manual"
            set_work_mode("manual")
            self.query_one("#info", InfoBar).refresh()
            label = MODEL_CONFIGS[self.model_key]["label"]
            self._add_static(Text.assemble(
                ("  已切换到 ", C_DIM),
                ("手动模式", f"bold {C_FG}"),
                (f"（使用 {label}）\n", C_DIM),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/react", "/智能体"):
            # 切换 ReAct Agent 模式
            self.react_enabled = not self.react_enabled
            status = "开启" if self.react_enabled else "关闭"
            self._add_static(Text.assemble(
                ("  ReAct Agent 模式已", C_DIM),
                (f"{status}", f"bold {C_FG}"),
                ("\n", ""),
                ("  观察→思考→行动 循环，支持自我纠错和 RAG 检索\n", C_DIM) if self.react_enabled else ("  已回退到普通工具调用模式\n", C_DIM),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/react plan", "/智能体 plan", "/智能体 规划"):
            # 切换 Plan-and-Execute 模式
            self._react_plan_mode = not getattr(self, "_react_plan_mode", False)
            status = "开启" if self._react_plan_mode else "关闭"
            self._add_static(Text.assemble(
                ("  Plan-and-Execute 模式已", C_DIM),
                (f"{status}\n", f"bold {C_FG}"),
                ("  先制定完整计划，再逐步执行\n", C_DIM),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/index", "/索引"):
            # 构建项目向量索引
            self._add_static(Text.assemble(
                ("  正在构建项目向量索引…\n", f"bold {C_FG}"),
            ))
            try:
                import asyncio as _asyncio
                from zeroai.memory import index_project, get_retriever

                project_root = os.getcwd()
                progress_block = self._add_block("索引进度", C_DIM)
                progress_block.update(Text.assemble(
                    (f"  扫描 {project_root} …\n", C_DIM),
                ))

                def _on_progress(current, total, fpath):
                    if current % 10 == 0 or current == total:
                        progress_block.update(Text.assemble(
                            (f"  索引中 {current}/{total}：{os.path.basename(fpath)}\n", C_DIM),
                        ))

                stats = _asyncio.get_event_loop().run_until_complete(
                    index_project(project_root, on_progress=_on_progress)
                )
                self._retriever = get_retriever()
                self._add_static(Text.assemble(
                    ("  └─ 索引完成：", C_DIM),
                    (f"{stats['indexed_files']} 文件 / {stats['total_chunks']} 块", f"bold {C_FG}"),
                    (f" / {stats['elapsed']:.1f}s\n", C_DIM),
                ))
            except Exception as e:
                self._add_static(Text.assemble(
                    ("  └─ 索引失败：", C_DIM),
                    (f"{e}\n", f"bold {C_YELLOW}"),
                ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/memory", "/记忆"):
            # 查看向量记忆统计
            try:
                from zeroai.memory import get_retriever
                retriever = get_retriever()
                stats = retriever.get_stats()
                if stats["total_chunks"] == 0:
                    self._add_static(Text.assemble(
                        ("  向量记忆为空，输入 ", C_DIM),
                        ("/索引", f"bold {C_FG}"),
                        (" 构建项目索引\n", C_DIM),
                    ))
                else:
                    by_source = stats.get("by_source", {})
                    top_sources = sorted(by_source.items(), key=lambda x: -x[1])[:5]
                    sources_text = " | ".join(f"{s}:{c}" for s, c in top_sources)
                    self._add_static(Text.assemble(
                        ("  向量记忆统计\n", f"bold {C_FG}"),
                        (f"  总块数：{stats['total_chunks']}\n", C_DIM),
                        (f"  文件数：{stats['total_sources']}\n", C_DIM),
                        (f"  向量维度：{stats['vector_dim']}\n", C_DIM),
                        (f"  Top 文件：{sources_text}\n", C_DIM),
                    ))
            except Exception as e:
                self._add_static(Text.assemble(
                    ("  └─ 查询失败：", C_DIM),
                    (f"{e}\n", f"bold {C_YELLOW}"),
                ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/mcp", "/MCP"):
            await self._handle_mcp_command("")
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input.startswith("/mcp ") or user_input.startswith("/MCP "):
            cmd = user_input[5:].strip()
            await self._handle_mcp_command(cmd)
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input in ("/model", "/模型"):
            # 显示当前模型和可用模型
            mode_label = {"expert": "专家模式", "hybrid": "混合思考", "manual": "手动模式"}.get(self.work_mode, "未知")
            current = get_model_display_name(self.model_key)
            available = " | ".join(f"{k}:{v['label']}" for k, v in MODEL_CONFIGS.items())
            # 专家团队列表
            team_list = " | ".join(f"{k}:{v['label']}" for k, v in EXPERT_TEAM.items())
            self._add_static(Text.assemble(
                ("  当前模式：", C_DIM),
                (f"{mode_label}\n", f"bold {C_FG}"),
                ("  当前模型：", C_DIM),
                (f"{current}\n", f"bold {C_FG}"),
                ("  可用模型：", C_DIM),
                (f"{available}\n", C_FG),
                ("  专家团队：", C_DIM),
                (f"{team_list}\n", C_FG),
                ("  模式切换：", C_DIM),
                ("/专家 | /混合 | /手动\n", C_FG),
                ("  模型切换：", C_DIM),
                ("/模型 glm | /模型 openrouter | /模型 ollama\n", C_FG),
            ))
            self.query_one("#input", MessageInput).value = ""
            return
        if user_input.startswith(("/model ", "/模型 ")):
            prefix_len = 7 if user_input.startswith("/model ") else 4
            key = user_input[prefix_len:].strip().lower()
            if self.switch_model(key):
                # 切换模型时自动切换到手动模式
                self.work_mode = "manual"
                set_work_mode("manual")
                self.query_one("#info", InfoBar).refresh()
                self._add_static(Text.assemble(
                    ("  已切换到：", C_DIM),
                    (f"{MODEL_CONFIGS[key]['label']}", f"bold {C_FG}"),
                    ("（手动模式·对话已清空）\n", C_DIM),
                ))
            else:
                available = " | ".join(MODEL_CONFIGS.keys())
                self._add_static(Text.assemble(
                    ("  未知模型：", C_DIM),
                    (f"{key}\n", C_FG),
                    ("  可用：", C_DIM),
                    (f"{available}\n", C_FG),
                ))
            self.query_one("#input", MessageInput).value = ""
            return

        # 用户消息 - Markdown 渲染
        self._add_block("你", C_BLUE)

        # 检测图片路径（支持 @图片路径 或直接图片路径）
        image_urls = list(self._pending_images)  # 先加上 Ctrl+V 粘贴的图片
        self._pending_images = []  # 清空暂存
        display_text = user_input
        IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

        # 检测 @路径 语法
        at_pattern = re.compile(r'@([\w:\\/.]+\.(?:png|jpg|jpeg|gif|bmp|webp))', re.IGNORECASE)
        at_matches = at_pattern.findall(user_input)
        # 收集需要预览的图片（路径列表，避免重复读取）
        preview_paths = []
        for img_path in at_matches:
            b64 = read_image(img_path)
            if b64.startswith("data:"):
                image_urls.append(b64)
                display_text = display_text.replace(f"@{img_path}", f"[图片: {img_path}]")
                preview_paths.append(img_path)
            else:
                display_text = display_text.replace(f"@{img_path}", f"[{b64}]")

        # 检测输入中直接的图片路径
        for word in re.findall(r'[\w:\\/.]+\.(?:png|jpg|jpeg|gif|bmp|webp)', user_input, re.IGNORECASE):
            if word not in at_matches and Path(word).exists():
                b64 = read_image(word)
                if b64.startswith("data:"):
                    image_urls.append(b64)
                    display_text = display_text.replace(word, f"[图片: {word}]")
                    preview_paths.append(word)

        # 显示图片预览
        for img_path in preview_paths:
            self._add_static(Text(f"  {_load_svg_icon('document')} 图片预览：{img_path}\n", style=C_DIM))
            self._add_static(render_image_preview(img_path))

        try:
            self._add_static(_safe_markdown(render_latex_in_text(display_text), code_theme="monokai"))
        except Exception:
            self._add_static(Text(f"  {display_text}", style=C_FG))

        # 显示图片缩略信息（黑白方块标签样式：[Image 1] [Image 2]）
        if image_urls:
            badge_parts = []
            for i in range(len(image_urls)):
                badge_parts.append((" [Image ", "bold white on #000000"))
                badge_parts.append((f"{i+1}", "bold white on #000000"))
                badge_parts.append(("] ", "bold white on #000000"))
            self._add_static(Text.assemble(*badge_parts))
        self._add_static(Text("  └─", style=C_DIM))

        self.query_one("#input", MessageInput).value = ""

        # 注入伴随模式屏幕上下文
        companion_ctx = self._get_companion_context()
        if companion_ctx:
            self.messages.append({"role": "system", "content": companion_ctx})

        # 构造消息：如果有图片则用多模态格式
        if image_urls:
            content_parts = [{"type": "text", "text": user_input}]
            for url in image_urls:
                content_parts.append({"type": "image_url", "image_url": {"url": url}})
            self.messages.append({"role": "user", "content": content_parts})
        else:
            self.messages.append({"role": "user", "content": user_input})

        await self._run_turn()

        # 生成完成后重新聚焦输入框（确保可以继续打字）
        self._keep_input_focus()

        # ── TTS 自动朗读：回复完成后，如果开启则朗读最近回复 ──
        if self._tts_enabled and self._last_reply_text.strip() and not self._stop_generation:
            import threading
            reply_text = self._last_reply_text
            voice = self._tts_voice
            rate = self._tts_rate
            def _do_tts():
                err = speak_tts(reply_text, voice=voice, rate=rate)
                if err:
                    def _show_err():
                        self._add_static(Text.assemble(
                            ("  🎤 朗读失败：", C_DIM),
                            (f"{err}\n", C_FG),
                        ))
                    self.call_after_refresh(_show_err)
            t = threading.Thread(target=_do_tts, daemon=True)
            t.start()
    def _init_project_agents(self):
        """手动重新生成 AGENTS.md（/init 命令入口）

        启动时已自动生成，此命令用于项目结构变更后手动刷新。
        """
        project_dir = WORK_DIR
        agents_path = os.path.join(project_dir, "AGENTS.md")

        self._add_block("项目初始化", C_CYAN)
        self._add_static(Text.assemble(
            (f"  {_load_svg_icon('folder')} 正在重新分析项目结构：{project_dir}\n", f"bold {C_CYAN}"),
        ))

        # 调用静态函数生成
        agents_content = _auto_generate_agents_md(project_dir)

        if not agents_content:
            self._add_static(Text("  ⚠️ 项目文件过少或扫描失败，未生成 AGENTS.md\n", style=C_YELLOW))
            return

        # 加载到 system prompt
        self._agents_md_content = agents_content
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._get_system_prompt()

        # 解析显示信息
        import re as _re
        name_m = _re.search(r'\*\*名称\*\*：(.+)', agents_content)
        ver_m = _re.search(r'\*\*版本\*\*：(.+)', agents_content)
        tech_m = _re.search(r'\*\*技术栈\*\*：(.+)', agents_content)
        project_name = name_m.group(1) if name_m else os.path.basename(project_dir)
        project_version = ver_m.group(1) if ver_m else ""
        tech_stack = tech_m.group(1) if tech_m else "未检测到"

        # 统计目录和文件数
        dir_count = agents_content.count("/")
        file_count = len([l for l in agents_content.split("\n") if l.startswith("- `")])

        self._add_static(Text.assemble(
            (f"  ✅ AGENTS.md 已刷新：{agents_path}\n", f"bold {C_FG}"),
            (f"  │ 项目：{project_name}" + (f" v{project_version}" if project_version else "") + "\n", C_DIM),
            (f"  │ 技术栈：{tech_stack}\n", C_DIM),
            ("\n  💡 AI 已更新项目上下文，回答代码相关问题时会更精准。\n", C_CYAN),
        ))
    def _show_help(self):
        self._add_static(Text.assemble(
            ("  命令：\n", f"bold {C_YELLOW}"),
            ("    /帮助          显示帮助\n", C_FG),
            ("    /清屏          清空屏幕\n", C_FG),
            ("    /新对话        开始新对话\n", C_FG),
            ("    /初始化        分析项目结构，生成 AGENTS.md（项目上下文文件）\n", C_FG),
            ("    /专家          切换到专家模式（自动路由）\n", C_FG),
            ("    /混合          切换到混合思考（多专家协作）\n", C_FG),
            ("    /手动          切换到手动模式（指定模型）\n", C_FG),
            ("    /智能体        切换 ReAct Agent 模式（观察→思考→行动）\n", C_FG),
            ("    /索引          构建项目向量索引（启用 RAG 检索）\n", C_FG),
            ("    /记忆          查看向量记忆统计\n", C_FG),
            ("    /mcp           MCP 协议管理（list/install/connect/tools）\n", C_FG),
            ("    /模型          查看当前模型和专家团队\n", C_FG),
            ("    /模型 glm      切换到智谱GLM（手动模式）\n", C_FG),
            ("    /模型 glm-v    切换到智谱GLM-4V（多模态，支持图片）\n", C_FG),
            ("    /模型 openrouter 切换到OpenRouter（手动模式）\n", C_FG),
            ("    /模型 ollama   切换到 Ollama（手动模式）\n", C_FG),
            ("    /复制          复制最近回复\n", C_FG),
            ("    /copy N        复制最近回复中第 N 个代码块\n", C_FG),
            ("    /copy list     列出最近回复中的所有代码块\n", C_FG),
            ("    /图片          粘贴剪贴板图片\n", C_FG),
            ("    /安全          安全审计（扫描漏洞/敏感信息/依赖/配置）\n", C_FG),
            ("    /ssh           SSH 远程部署（连接/执行/上传/部署）\n", C_FG),
            ("    /退出          退出\n", C_FG),
            ("\n  语音交互：\n", f"bold {C_YELLOW}"),
            ("    /语音          开启/关闭 AI 回复自动朗读\n", C_FG),
            ("    /对话          开启语音对话模式（说话即提问，AI 语音回答）\n", C_FG),
            ("    /停止          停止语音对话模式\n", C_FG),
            ("    /女声          切换为女声（晓晓）\n", C_FG),
            ("    /男声          切换为男声（云希）\n", C_FG),
            ("    /语速 +10%     设置语速（+10% 加速 / -10% 减速）\n", C_FG),
            ("    Ctrl+T         单次语音输入（停顿自动停止）\n", C_FG),
            ("    Ctrl+D         语音对话模式（持续听→答→听循环）\n", C_FG),
            ("\n  发送图片：\n", f"bold {C_YELLOW}"),
            ("    /图片          粘贴剪贴板图片（截图后输入 /图片）\n", C_FG),
            ("    Ctrl+G         粘贴剪贴板图片快捷键\n", C_FG),
            ("    直接输入图片路径：D:/图片/screenshot.png\n", C_FG),
            ("    @图片路径 语法：@D:/图片/test.jpg 描述一下这张图\n", C_FG),
            ("    支持格式：png / jpg / jpeg / gif / bmp / webp\n", C_FG),
            ("\n  快捷键：\n", f"bold {C_YELLOW}"),
            ("    Ctrl+C 停止/复制选中/退出 · Ctrl+G 粘贴图片 · Ctrl+T 语音输入 · Ctrl+D 语音对话 · Ctrl+W 伴随模式 · Ctrl+Y 复制回复 · Ctrl+P 设置 · Ctrl+N 新对话\n", C_FG),
        ))
