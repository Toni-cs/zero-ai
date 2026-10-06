"""语音与伴随模式：按住说话（Ctrl+T）、语音对话（Ctrl+D）、伴随模式（Ctrl+W）后台监听（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import subprocess
from rich.text import Text
from zeroai.tools.voice import listen_asr
from zeroai.tools.window_mgr import read_screen_content
from zeroai.tui.colors import C_BLUE, C_DIM, C_FG
from zeroai.tui.icons import _load_svg_icon
from zeroai.tui.screens import VoiceDialogScreen
from zeroai.tui.widgets import MessageInput


class VoiceMixin:
    """语音与伴随模式：按住说话（Ctrl+T）、语音对话（Ctrl+D）、伴随模式（Ctrl+W）后台监听"""

    def action_push_to_talk(self):
        """Ctrl+T：按住说话（Push-to-Talk）

        单击触发：开始录音 → 静音检测自动停止 → 识别 → 填入输入框
        用户可在输入框中编辑后按回车发送
        """
        if self._is_listening:
            # 正在录音中，忽略重复触发
            return
        if self._is_generating:
            self._add_static(Text(f"  {_load_svg_icon('warning')} AI 正在生成中，请稍后再试\n", style=C_FG))
            return
        # 启动录音线程（避免阻塞 UI）
        import threading
        self._is_listening = True
        # 显示录音提示
        listen_block = self._add_block("语音输入", C_BLUE)
        listen_block.update(Text.assemble(
            ("  🎤 正在录音…", f"bold {C_FG}"),
            ("（说话即可，停顿自动停止）\n", C_DIM),
        ))
        def _do_listen():
            try:
                text = listen_asr(max_seconds=10, silence_seconds=1.5)
                self._is_listening = False
                # 通过 call_after_refresh 更新 UI
                def _update():
                    if text.startswith("错误") or text.startswith("（"):
                        listen_block.update(Text.assemble(
                            ("  🎤 语音识别失败\n", f"bold {C_FG}"),
                            (f"  {text}\n", C_DIM),
                        ))
                    else:
                        listen_block.update(Text.assemble(
                            ("  🎤 识别结果：", f"bold {C_FG}"),
                            (f"{text}\n", C_FG),
                            ("  （已填入输入框，回车发送，Ctrl+J 换行编辑）\n", C_DIM),
                        ))
                        # 填入输入框
                        try:
                            inp = self.query_one("#input", MessageInput)
                            inp.value = text
                            inp.focus()
                        except Exception:
                            pass
                self.call_after_refresh(_update)
            except Exception as e:
                self._is_listening = False
                def _err():
                    listen_block.update(Text.assemble(
                        ("  🎤 录音异常\n", f"bold {C_FG}"),
                        (f"  {e}\n", C_DIM),
                    ))
                self.call_after_refresh(_err)
        t = threading.Thread(target=_do_listen, daemon=True)
        t.start()
    def action_voice_dialog(self):
        """Ctrl+D：打开讨论助手（全屏沉浸式语音对话 Modal）

        进入后自动循环：
        听你说 → 识别 → 显示气泡 → AI 流式回答（打字机）→ 朗读 → 再听
        可随时说话打断 AI 朗读
        再次按 Ctrl+D 或 Esc 或点 × 退出
        """
        # 自动开启 TTS
        if not self._tts_enabled:
            self._tts_enabled = True
        # 推送全屏语音对话 Modal
        self.push_screen(VoiceDialogScreen(self))
    async def _voice_dialog_send(self, user_text: str):
        """保留兼容：旧的单轮对话方法（已被 VoiceDialogScreen 替代）"""
        # 此方法已废弃，由 VoiceDialogScreen 直接处理
        pass
    def action_toggle_companion(self):
        """Ctrl+W：切换伴随模式"""
        self._companion_mode = not self._companion_mode
        if self._companion_mode:
            self._companion_log = []
            self._last_window_title = ""
            self._last_clipboard_text = ""
            self._start_companion_thread()
            self._add_static(Text.assemble(
                (f"  {_load_svg_icon('monitor')} 伴随模式已开启\n", f"bold {C_FG}"),
                ("  AI 正在观察你的屏幕，关键变化会自动记录\n", C_DIM),
                ("  发消息时 AI 会知道你刚才在做什么\n", C_DIM),
            ))
            self.notify("伴随模式已开启")
        else:
            self._add_static(Text(f"  {_load_svg_icon('monitor')} 伴随模式已关闭\n", style=C_DIM))
            self.notify("伴随模式已关闭")
    def _start_companion_thread(self):
        """启动后台监听线程"""
        import threading
        def monitor():
            while self._companion_mode:
                try:
                    # 1. 监听窗口切换
                    try:
                        import ctypes
                        user32 = ctypes.windll.user32
                        hwnd = user32.GetForegroundWindow()
                        length = user32.GetWindowTextLengthW(hwnd) + 1
                        title = ctypes.create_unicode_buffer(length)
                        user32.GetWindowTextW(hwnd, title, length)
                        current_title = title.value.strip()
                        if current_title and current_title != self._last_window_title and "ZeroAI" not in current_title:
                            import time as _t
                            ts = _t.strftime("%H:%M:%S", _t.localtime())
                            self._companion_log.append(f"[{ts}] 切换窗口 → {current_title}")
                            self._last_window_title = current_title
                    except Exception:
                        pass

                    # 2. 监听剪贴板变化（只读文本）
                    try:
                        r = subprocess.run(["powershell", "-c", "Get-Clipboard -Format Text"],
                                           capture_output=True, text=True, timeout=2)
                        clip_text = r.stdout.strip()[:200] if r.stdout else ""
                        if clip_text and clip_text != self._last_clipboard_text and len(clip_text) > 5:
                            import time as _t
                            ts = _t.strftime("%H:%M:%S", _t.localtime())
                            self._companion_log.append(f"[{ts}] 复制了内容 → {clip_text[:80]}")
                            self._last_clipboard_text = clip_text
                    except Exception:
                        pass

                    # 保留最近 30 条日志
                    if len(self._companion_log) > 30:
                        self._companion_log = self._companion_log[-30:]

                except Exception:
                    pass
                import time as _t
                _t.sleep(2)  # 每2秒检测一次

        t = threading.Thread(target=monitor, daemon=True)
        t.start()
        self._companion_thread = t
    def _get_companion_context(self) -> str:
        """获取伴随模式的屏幕上下文（对话前注入）"""
        if not self._companion_mode or not self._companion_log:
            return ""
        # 取最近10条日志
        recent = self._companion_log[-10:]
        # 加上当前窗口内容
        try:
            current = read_screen_content(max_length=500)
            return f"【伴随模式·屏幕感知】\n最近活动：\n" + "\n".join(recent) + f"\n\n当前屏幕：\n{current}"
        except Exception:
            return f"【伴随模式·屏幕感知】\n最近活动：\n" + "\n".join(recent)
