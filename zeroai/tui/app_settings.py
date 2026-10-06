"""模型与设置面板：当前客户端、切换模型、Ctrl+P 设置、代理配置、增删自定义模型、扫描 Ollama（从 zeroai/tui/app.py 拆出，2026-10-06）

ZeroAI 巨类拆分的一环：本文件只放一个 mixin，方法体自 app.py 原样搬来，
逐字节未改。拆分脚本 _split_app.py，验收见其文件头的五道关卡。
"""
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static
from zeroai.core.constants import EXPERT_TEAM, MODEL_CONFIGS, set_work_mode
from zeroai.core.expert_route import get_expert_config
from zeroai.core.model_manager import _save_custom_models, detect_ollama_models, get_model_display_name
from zeroai.core.secrets import _load_config, _load_proxy_config, _make_openai_sync_client, _refresh_proxy_config, _save_config, _save_proxy_config
from zeroai.tui.colors import C_BG, C_BLUE, C_BORDER, C_DIM, C_FG
from zeroai.tui.screens import AddModelScreen, SettingsScreen
from zeroai.tui.widgets import InfoBar


class SettingsMixin:
    """模型与设置面板：当前客户端、切换模型、Ctrl+P 设置、代理配置、增删自定义模型、扫描 Ollama"""

    def get_current_client(self):
        """获取当前模型的客户端

        【修复 2026-09-16】此前直接 `OpenAI(base_url=cfg["base_url"], api_key=... )`，
        绕过 secrets 工厂 —— 代理启用时仍直连上游，且代理模式下本地无真实 Key，
        构造即抛 OpenAIError: Missing credentials（已实测复现）。
        当前该方法无调用点，属**潜在陷阱**而非线上故障；但一行即可消除，
        避免后来者调用时踩中与 llm.py 同款的代理断裂。
        """
        return _make_openai_sync_client(self.model_key)
    def get_current_model(self):
        """获取当前模型名"""
        return MODEL_CONFIGS[self.model_key]["model"]
    def switch_model(self, key: str) -> bool:
        """切换模型，成功返回 True"""
        if key not in MODEL_CONFIGS:
            return False
        self.model_key = key
        # 切换模型时清空历史（不同模型的 system prompt 格式可能不同）
        self.messages = [{"role": "system", "content": self._get_system_prompt()}]
        # 刷新顶部信息栏
        self.query_one("#info", InfoBar).refresh()
        return True
    def action_open_settings(self):
        """Ctrl+P 打开设置面板"""
        def on_result(result):
            if not result or not isinstance(result, dict):
                return
            action = result.get("action")
            if action == "switch_mode":
                mode = result["mode"]
                self.work_mode = mode
                set_work_mode(mode)
                self.query_one("#info", InfoBar).refresh()
                mode_labels = {"expert": "专家模式", "hybrid": "混合思考", "manual": "手动模式"}
                ml = mode_labels.get(mode, mode)
                self._add_static(Text.assemble(
                    ("  已切换到 ", C_DIM),
                    (f"{ml}", f"bold {C_FG}"),
                    ("\n", C_DIM),
                ))
            elif action == "expert_info":
                ek = result["key"]
                expert = EXPERT_TEAM[ek]
                e_cfg = get_expert_config(ek)
                self._add_static(Text.assemble(
                    ("  ◆ 专家信息\n", f"bold {C_BLUE}"),
                    ("  名称：", C_DIM), (f"{expert['label']}\n", C_FG),
                    ("  说明：", C_DIM), (f"{expert['desc']}\n", C_FG),
                    ("  模型：", C_DIM), (f"{e_cfg['model']}\n", C_FG),
                    ("  平台：", C_DIM), (f"{e_cfg.get('label', expert['label'])}\n", C_FG),
                    ("  关键词：", C_DIM), (f"{', '.join(expert['keywords'][:8]) if expert['keywords'] else '默认兜底'}\n", C_DIM),
                ))
            elif action == "switch_model":
                key = result["key"]
                if key != self.model_key and self.switch_model(key):
                    # 从设置面板切换模型时自动切到手动模式
                    self.work_mode = "manual"
                    set_work_mode("manual")
                    self.query_one("#info", InfoBar).refresh()
                    label = MODEL_CONFIGS[key]["label"]
                    self._add_static(Text.assemble(
                        ("  已切换模型：", C_DIM),
                        (f"{label}", f"bold {C_FG}"),
                        ("（手动模式·对话已清空）\n", C_DIM),
                    ))
            elif action == "set_temperature":
                self.temperature = result["value"]
                self._add_static(Text.assemble(
                    ("  温度：", C_DIM),
                    (f"{self.temperature}", f"bold {C_FG}"),
                    ("\n", C_DIM),
                ))
            elif action == "set_stream":
                self.stream_enabled = result["value"]
                label = "开" if self.stream_enabled else "关"
                self._add_static(Text.assemble(
                    ("  流式输出：", C_DIM),
                    (f"{label}", f"bold {C_FG}"),
                    ("\n", C_DIM),
                ))
            elif action == "set_max_turns":
                self.max_turns = result["value"]
                self._add_static(Text.assemble(
                    ("  最大调用轮次：", C_DIM),
                    (f"{self.max_turns}", f"bold {C_FG}"),
                    ("\n", C_DIM),
                ))
            elif action == "set_context_limit":
                self.context_limit = result["value"]
                self._add_static(Text.assemble(
                    ("  上下文长度：", C_DIM),
                    (f"{self.context_limit}", f"bold {C_FG}"),
                    ("\n", C_DIM),
                ))
            elif action == "add_model":
                self._open_add_model()
            elif action == "scan_ollama":
                self._scan_ollama_models()
            elif action == "remove_model":
                self._remove_custom_model()
            elif action == "about":
                current_model = get_model_display_name(self.model_key)
                self._add_static(Text.assemble(
                    ("  ◆ ZeroAI\n", f"bold {C_BLUE}"),
                    ("  终端 AI 编程助手\n\n", C_DIM),
                    ("  当前模型：", C_DIM), (f"{current_model}\n", f"bold {C_FG}"),
                    ("  模式：", C_DIM), ("/专家（自动路由） / /混合（多专家协作） / /手动（指定模型）\n\n", C_FG),
                    ("  专家团队（7位）：\n", C_DIM),
                    ("    项目经理 · 编程 · 推理\n", C_FG),
                    ("    通用 · 中文 · 多模态\n", C_FG),
                    ("    学术\n\n", C_FG),
                    ("  工具（31个）：\n", C_DIM),
                    ("    读写文件 · 行编辑 · 列目录 · 执行命令 · 搜索代码\n", C_FG),
                    ("    打开应用 · 联网搜索 · 抓取网页 · 版本控制\n", C_FG),
                    ("    删除/移动/复制文件 · 创建目录 · 系统信息 · 进程列表\n", C_FG),
                    ("    Python沙箱 · 包管理 · 端口检测 · 文件对比\n", C_FG),
                    ("    图片理解 · 窗口感知 · 屏幕阅读 · 安全审计 · Word文档\n", C_FG),
                    ("    学术文献搜索 · arXiv预印本 · LaTeX公式渲染\n\n", C_FG),
                    ("  快捷键：\n", C_DIM),
                    ("    Ctrl+C  ", C_FG), ("停止生成 / 复制回复（按两次退出）\n", C_DIM),
                    ("    Ctrl+G  ", C_FG), ("粘贴剪贴板图片\n", C_DIM),
                    ("    Ctrl+T  ", C_FG), ("语音输入（按住说话）\n", C_DIM),
                    ("    Ctrl+J  ", C_FG), ("输入框换行（多行输入）\n", C_DIM),
                    ("    Ctrl+Y  ", C_FG), ("复制最近回复\n", C_DIM),
                    ("    Ctrl+P  ", C_FG), ("设置面板\n", C_DIM),
                    ("    Ctrl+N  ", C_FG), ("新对话\n", C_DIM),
                    ("    Ctrl+W  ", C_FG), ("伴随模式（屏幕感知）\n", C_DIM),
                    ("    Ctrl+L  ", C_FG), ("清屏\n", C_DIM),
                    ("    PageUp  ", C_FG), ("上翻页\n", C_DIM),
                    ("    PageDn  ", C_FG), ("下翻页\n", C_DIM),
                    ("    Esc     ", C_FG), ("关闭弹窗\n\n", C_DIM),
                    ("  命令：\n", C_DIM),
                    ("    /帮助  /清屏  /新对话  /图片  /安全  /复制 /copy N  /退出\n", C_FG),
                ))
            elif action in ("proxy_toggle", "proxy_url", "proxy_token"):
                # v1.1.0 代理服务器配置
                self._open_proxy_config(action)

        self.push_screen(SettingsScreen(
            self.model_key, self.temperature, self.stream_enabled,
            self.max_turns, self.context_limit, self.work_mode,
        ), on_result)
    def _open_proxy_config(self, action: str):
        """v1.1.0 打开代理服务器配置对话框"""
        current = _load_proxy_config()

        if action == "proxy_toggle":
            # 切换启用/禁用
            new_enabled = not current.get("enabled", False)
            if new_enabled and not current.get("base_url"):
                self._add_static(Text.assemble(
                    ("  ", C_DIM),
                    ("⚠ 请先配置代理地址和 Token，再启用代理\n", f"bold {C_FG}"),
                ))
                return
            _save_proxy_config(
                new_enabled,
                current.get("base_url", ""),
                current.get("token", ""),
            )
            # 刷新 secrets 模块里的全局缓存（不是本地赋值，理由见文件顶部 import 注释）
            _refresh_proxy_config()
            status = "已启用" if new_enabled else "已禁用"
            self._add_static(Text.assemble(
                ("  代理模式：", C_DIM),
                (f"{status}\n", f"bold {C_FG}"),
            ))
            return

        # 输入对话框（proxy_url / proxy_token）
        field = "base_url" if action == "proxy_url" else "token"
        label = "代理地址（如 http://192.168.10.6:8000/v1）" if action == "proxy_url" else "访问 Token"
        default = current.get(field, "")

        from textual.widgets import Input
        from textual.containers import Vertical
        from textual.screen import ModalScreen

        class ProxyInputScreen(ModalScreen):
            CSS = f"""
            ProxyInputScreen {{ align: center middle; }}
            #proxy-input-dialog {{
                width: 60; height: auto; max-height: 20;
                background: {C_BG}; padding: 1 2;
                border: solid {C_BORDER};
            }}
            #proxy-input-title {{ color: {C_FG}; text-style: bold; padding: 0 0 1 0; }}
            #proxy-input-hint {{ color: {C_DIM}; padding: 0 0 1 0; }}
            #proxy-input-field {{ width: 100%; }}
            #proxy-input-footer {{ color: {C_DIM}; padding: 1 0 0 0; }}
            """
            BINDINGS = [Binding("escape", "close", "关闭", show=False)]

            def __init__(self, title: str, hint: str, default_val: str, field_name: str):
                super().__init__()
                self.title = title
                self.hint = hint
                self.default_val = default_val
                self.field_name = field_name

            def compose(self):
                with Vertical(id="proxy-input-dialog"):
                    yield Static(self.title, id="proxy-input-title")
                    yield Static(self.hint, id="proxy-input-hint")
                    yield Input(value=self.default_val, id="proxy-input-field",
                                placeholder=self.hint)
                    yield Static("回车保存 · Esc 取消", id="proxy-input-footer")

            def action_close(self):
                self.dismiss(None)

            def on_input_submitted(self, event):
                val = event.value.strip()
                self.dismiss({"field": self.field_name, "value": val})

        def on_proxy_result(res):
            if not res or not res.get("value"):
                return
            new_val = res["value"]
            _save_proxy_config(
                current.get("enabled", False),
                new_val if action == "proxy_url" else current.get("base_url", ""),
                new_val if action == "proxy_token" else current.get("token", ""),
            )
            # 刷新 secrets 模块里的全局缓存（不是本地赋值，理由见文件顶部 import 注释）
            _refresh_proxy_config()
            self._add_static(Text.assemble(
                ("  ", C_DIM),
                (f"✓ {label} 已保存\n", f"bold {C_FG}"),
            ))

        self.push_screen(ProxyInputScreen(
            title=f"配置 {label}",
            hint=label,
            default_val=default,
            field_name=field,
        ), on_proxy_result)
    def _open_add_model(self):
        def on_add_result(result):
            if not result or result.get("action") != "add_model":
                if result and result.get("action") == "error":
                    self._add_static(Text(f"  {result['msg']}\n", style=C_FG))
                return
            vals = result["values"]
            key = vals["key"].strip()
            api_key = vals.get("api_key", "") or ""
            MODEL_CONFIGS[key] = {
                "label": vals.get("label", key) or key,
                "base_url": vals["base_url"].rstrip("/"),
                "api_key": api_key,
                "model": vals["model"].strip(),
            }
            # 保存到自定义模型文件（混淆）和配置文件
            _save_custom_models()
            _save_config({key: {"api_key": api_key}})
            # 追加到已有配置文件
            existing = _load_config()
            existing[key] = {"api_key": api_key}
            _save_config(existing)
            self._add_static(Text.assemble(
                ("  已添加模型：", C_DIM),
                (f"{MODEL_CONFIGS[key]['label']}", f"bold {C_FG}"),
                (f"  标识 {key}\n", C_DIM),
            ))
        self.push_screen(AddModelScreen(), on_add_result)
    def _scan_ollama_models(self):
        models = detect_ollama_models()
        if not models:
            self._add_static(Text("  未检测到本地模型服务或无可用模型\n", style=C_FG))
            return
        added = []
        for mid in models:
            key = f"ollama_{mid.replace(':', '_').replace('/', '_')}"
            if key not in MODEL_CONFIGS:
                MODEL_CONFIGS[key] = {
                    "label": f"Ollama {mid}",
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "ollama",
                    "model": mid,
                }
                added.append(mid)
        _save_custom_models()
        if added:
            names = " · ".join(added)
            self._add_static(Text.assemble(
                ("  扫描到 ", C_DIM),
                (f"{len(added)}", f"bold {C_FG}"),
                (" 个新模型：", C_DIM),
                (f"{names}\n", C_FG),
                ("  按 Ctrl+P 可切换模型\n", C_DIM),
            ))
        else:
            self._add_static(Text("  ℹ 本地模型已在列表中\n", style=C_DIM))
    def _remove_custom_model(self):
        custom_keys = [k for k in MODEL_CONFIGS if k not in ("glm", "glm-v", "openrouter", "ollama")]
        if not custom_keys:
            self._add_static(Text("  ℹ 没有自定义模型可删除\n", style=C_DIM))
            return
        items = []
        for key in custom_keys:
            items.append(ListItem(Label(f"  {MODEL_CONFIGS[key]['label']}  [{key}]"), name=f"del_model:{key}"))

        class RemoveModelScreen(ModalScreen):
            CSS = SettingsScreen.CSS
            BINDINGS = [Binding("escape", "close_rm", "关闭", show=False)]
            def __init__(self, item_list):
                super().__init__()
                self.item_list = item_list
            def compose(self):
                with Vertical(id="settings-dialog"):
                    yield Static("删除自定义模型", id="settings-title")
                    yield Static("回车删除 · Esc 取消", id="settings-hint")
                    yield ListView(*self.item_list)
                    yield Static("Esc 取消", id="settings-footer")
            def action_close_rm(self):
                self.dismiss(None)
            def on_list_view_selected(self, event):
                self.dismiss(event.item.name)

        def on_rm_result(name):
            if not name or not name.startswith("del_model:"):
                return
            key = name.split(":", 1)[1]
            if key in MODEL_CONFIGS and key not in ("glm", "glm-v", "openrouter", "ollama"):
                label = MODEL_CONFIGS[key]['label']
                del MODEL_CONFIGS[key]
                _save_custom_models()
                if self.model_key == key:
                    self.model_key = "glm"
                    self.messages = [{"role": "system", "content": self._get_system_prompt()}]
                    self.query_one("#info", InfoBar).refresh()
                    self._add_static(Text.assemble(
                        ("  已删除：", C_DIM),
                        (f"{label}", f"bold {C_FG}"),
                        ("（已回退到智谱GLM）\n", C_DIM),
                    ))
                else:
                    self._add_static(Text.assemble(
                        ("  已删除：", C_DIM),
                        (f"{label}\n", f"bold {C_FG}"),
                    ))
        self.push_screen(RemoveModelScreen(items), on_rm_result)
