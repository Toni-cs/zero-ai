"""剪贴板与代码块：提取/复制最近回复里的代码块（Ctrl+Y）、粘贴剪贴板图片（Ctrl+G）（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
import base64
import subprocess
from rich.text import Text
from zeroai.tools.clipboard import _copy_to_clipboard
from zeroai.tools.file_manager import read_image
from zeroai.tui.colors import C_CYAN, C_DIM, C_FG
from zeroai.tui.widgets import MessageInput


class ClipboardMixin:
    """剪贴板与代码块：提取/复制最近回复里的代码块（Ctrl+Y）、粘贴剪贴板图片（Ctrl+G）"""

    def _extract_code_blocks(self, text: str) -> list:
        """从 Markdown 文本中提取所有代码块内容

        返回 [{"lang": "python", "code": "...", "preview": "..."}, ...]
        """
        blocks = []
        if not text:
            return blocks
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if stripped.startswith("```"):
                lang = stripped[3:].strip() or "text"
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                code = "\n".join(code_lines)
                preview = code[:60].replace("\n", " ")
                blocks.append({"lang": lang, "code": code, "preview": preview})
            i += 1
        return blocks
    def _copy_code_block_by_index(self, n: int):
        """复制最近回复中第 N 个代码块（从 1 开始计数）"""
        if not self._last_reply_text:
            self.notify("暂无回复可复制")
            return
        blocks = self._extract_code_blocks(self._last_reply_text)
        if not blocks:
            self.notify("最近回复中没有代码块")
            return
        if n < 1 or n > len(blocks):
            self.notify(f"序号超出范围（1-{len(blocks)}）")
            return
        code = blocks[n - 1]["code"]
        if _copy_to_clipboard(code):
            preview = code[:50].replace("\n", " ")
            self.notify(f"已复制代码块 [{n}/{len(blocks)}]：{preview}…")
        else:
            try:
                import subprocess
                subprocess.run("clip", input=code, text=True, check=True)
                self.notify(f"已复制代码块 [{n}/{len(blocks)}]")
            except Exception:
                self.notify("复制失败")
    def _list_code_blocks(self):
        """列出最近回复中的所有代码块"""
        if not self._last_reply_text:
            self.notify("暂无回复")
            return
        blocks = self._extract_code_blocks(self._last_reply_text)
        if not blocks:
            self.notify("最近回复中没有代码块")
            return
        parts = [("  📋 最近回复中的代码块：\n\n", f"bold {C_FG}")]
        for i, b in enumerate(blocks, 1):
            parts.append((f"  [{i}] {b['lang']}  ", f"bold {C_CYAN}"))
            parts.append((f"{b['preview']}…\n", C_DIM))
        parts.append(("\n  使用 /copy N 复制第 N 个代码块\n", C_DIM))
        self._add_static(Text.assemble(*parts))
    def action_copy_last_reply(self):
        """Ctrl+Y：复制最近一次助手回复到剪贴板"""
        if not self._last_reply_text:
            self.notify("无内容可复制")
            return
        # 优先用 Windows API（完整 Unicode 支持），失败时回退到 clip.exe
        if _copy_to_clipboard(self._last_reply_text):
            preview = self._last_reply_text[:30].replace("\n", " ")
            self.notify(f"已复制：{preview}…")
        else:
            # 回退方案：clip.exe（可能有编码问题）
            try:
                import subprocess
                subprocess.run("clip", input=self._last_reply_text, text=True, check=True)
                preview = self._last_reply_text[:30].replace("\n", " ")
                self.notify(f"已复制：{preview}…")
            except Exception:
                self.notify("复制失败")
    def action_paste_image(self):
        """Ctrl+G 或 /图片：检测剪贴板图片，如果有则暂存并预览"""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
            if img is None:
                # 没有图片，检查是否有文本
                try:
                    import subprocess
                    text = subprocess.run(["powershell", "-c", "Get-Clipboard -Format Text"],
                                         capture_output=True, text=True, timeout=2).stdout
                    if text.strip():
                        # 有文本，粘贴到输入框
                        inp = self.query_one("#input", MessageInput)
                        inp.value += text.rstrip("\r\n")
                        self.notify("已粘贴文本（剪贴板无图片）")
                    else:
                        self.notify("剪贴板为空！请先截图（Win+Shift+S）或复制图片")
                except Exception:
                    self.notify("无法读取剪贴板，请先截图（Win+Shift+S）")
                return
            # 有图片！转为 base64
            import io
            if isinstance(img, list):
                # 某些情况下返回文件路径列表
                for f in img:
                    if str(f).lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
                        b64 = read_image(str(f))
                        if b64.startswith("data:"):
                            self._pending_images.append(b64)
                            self._add_static(Text.assemble(
                                (" [Image ", "bold white on #000000"),
                                (f"{len(self._pending_images)}", "bold white on #000000"),
                                ("] ", "bold white on #000000"),
                                (f"{f}\n", C_DIM),
                            ))
                            # 显示图片预览（已按用户要求停用照片预览，仅保留 [Image N] 标签）
                            # self._add_static(render_image_preview(b64))
                            self.notify(f"已附加图片 {len(self._pending_images)}，输入文字后回车发送")
                            return
                return
            # PIL Image 对象
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            b64_data = base64.b64encode(data).decode("ascii")
            b64_uri = f"data:image/png;base64,{b64_data}"
            self._pending_images.append(b64_uri)
            self._add_static(Text.assemble(
                (" [Image ", "bold white on #000000"),
                (f"{len(self._pending_images)}", "bold white on #000000"),
                ("] ", "bold white on #000000"),
                (f"剪贴板图片（{img.width}x{img.height}）\n", C_DIM),
            ))
            # 显示图片预览（已按用户要求停用照片预览，仅保留 [Image N] 标签）
            # self._add_static(render_image_preview(b64_uri))
            self.notify(f"已附加图片 {len(self._pending_images)}，输入文字后回车发送")
        except Exception as e:
            # ImageGrab 不可用时回退为普通文本粘贴
            try:
                import subprocess
                text = subprocess.run(["powershell", "-c", "Get-Clipboard -Format Text"],
                                     capture_output=True, text=True, timeout=2).stdout
                if text.strip():
                    inp = self.query_one("#input", MessageInput)
                    inp.value += text.rstrip("\r\n")
            except Exception:
                pass
