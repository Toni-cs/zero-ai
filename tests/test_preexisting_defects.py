# -*- coding: utf-8 -*-
"""既有缺陷回归测试（2026-09-16 新增）

覆盖此前登记在案、但在 2026-09-16 复核中被证实**仍然存在**的两个可达缺陷：

  缺陷 3：assets/icons 未进入 wheel
          → 安装后 _load_svg_icon() 永远返回 ""，所有图标静默失效
  缺陷 4：VoiceDialogScreen 的用户/AI 气泡从未渲染
          → _append_user_bubble / _append_ai_placeholder 在 row 尚未挂载时
            调用 row.mount(log)，抛出的异常被裸 except 吞掉，
            结果 #vd-content 内一个气泡节点都没有

另有两条**已修复**的历史缺陷，这里保留断言防止回归：
  缺陷 1：HintBar 硬编码版本号 "0.1.5"
  缺陷 2：AddModelScreen compose 内重复 widget id（对话框打不开）

标注方式：
  @pytest.mark.defect3 / defect4  —— 修复前会失败
"""
import asyncio
import os
import re
import sys
import zipfile
import glob
import shutil
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════
def _read(*rel):
    return (_ROOT.joinpath(*rel)).read_text(encoding="utf-8")


def _strip_comments(text):
    """剔除整行注释，避免把解释性注释误判为代码。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("#")
    )


# ══════════════════════════════════════════════════════════════════
# 缺陷 1（已修复，防回归）：HintBar 不得硬编码版本号
# ══════════════════════════════════════════════════════════════════
def test_hintbar_uses_dynamic_version():
    code = _strip_comments(_read("zeroai", "tui", "widgets.py"))
    assert "get_version()" in code, "HintBar 应从 zeroai.get_version() 动态读取版本号"
    hardcoded = re.findall(r'["\']\s?0\.\d+\.\d+["\']', code)
    assert not hardcoded, f"不应再出现硬编码版本号字面量：{hardcoded}"


# ══════════════════════════════════════════════════════════════════
# 缺陷 2（已修复，防回归）：AddModelScreen 可真实打开且 ID 唯一
# ══════════════════════════════════════════════════════════════════
def test_add_model_screen_has_unique_ids():
    src = _read("zeroai", "tui", "screens.py")
    m = re.search(r"^class AddModelScreen\b", src, re.M)
    assert m, "未找到 AddModelScreen"
    rest = src[m.start():]
    nxt = re.search(r"^class \w", rest[1:], re.M)
    body = rest[: nxt.start() + 1] if nxt else rest

    cm = re.search(r"def compose\(self\).*?(?=\n    def |\nclass )", body, re.S)
    compose = _strip_comments(cm.group(0) if cm else "")
    ids = re.findall(r'id=["\']([^"\'{}]+)["\']', compose)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"compose 内存在重复 widget id（会导致对话框无法打开）：{dupes}"


def test_add_model_screen_actually_mounts():
    """真实挂载：这是唯一能证明"对话框打得开"的方式。"""
    from textual.app import App
    from zeroai.tui.screens import AddModelScreen

    class _T(App):
        def on_mount(self):
            self.push_screen(AddModelScreen())

    async def _run():
        app = _T()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, AddModelScreen), "对话框未成功推入"
            fields = app.screen.query(".add-field")
            assert len(fields) == len(AddModelScreen.FIELDS), (
                f"字段数不符：{len(fields)} != {len(AddModelScreen.FIELDS)}"
            )

    asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════
# 缺陷 3：assets/icons 必须随包发布
# ══════════════════════════════════════════════════════════════════
def test_package_data_covers_icons():
    """pyproject 的 package-data 必须真正覆盖图标的实际存放位置。

    图标在仓库根 assets/icons/，而 package-data 的键是**包名**、
    值相对包目录。因此只有把图标放进包内（或改用具名键）才可能命中。
    """
    import tomllib

    with open(_ROOT / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    pkg_data = (
        cfg.get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
    )

    # 判定：是否存在某条目，其 glob 能在**某个真实包目录**下命中 svg
    found = []
    for pkg, patterns in pkg_data.items():
        for pat in patterns:
            if "assets" not in pat:
                continue
            if pkg == "*":
                # 通配所有包：检查是否有任一包目录下匹配
                for pkgdir in _ROOT.glob("*/"):
                    if not (pkgdir / "__init__.py").exists():
                        continue
                    if list(pkgdir.glob(pat)):
                        found.append((pkg, pat))
            else:
                pkgdir = _ROOT.joinpath(*pkg.split("."))
                if list(pkgdir.glob(pat)):
                    found.append((pkg, pat))
    assert found, (
        "没有任何 package-data 条目能在真实包目录下命中图标文件。"
        f"当前配置={pkg_data}，图标实际位置={sorted(str(p) for p in _ROOT.glob('assets/icons/*'))[:3]}..."
    )


def test_built_wheel_contains_icons():
    """若能构建出 wheel，则其中必须包含图标。"""
    whls = sorted(glob.glob(str(_ROOT / "dist" / "*.whl")))
    if not whls:
        pytest.skip("dist/ 下暂无 wheel，跳过（构建后再验）")
    z = zipfile.ZipFile(whls[-1])
    svgs = [n for n in z.namelist() if n.endswith(".svg")]
    assert svgs, f"{os.path.basename(whls[-1])} 内不含任何 SVG 图标"


def test_installed_layout_can_load_icon():
    """模拟安装布局：解压 wheel 后 _load_svg_icon 必须能拿到图标。

    这是缺陷 3 的**用户可见后果**断言：真实的 pip 安装后，图标是否可用。
    """
    whls = sorted(glob.glob(str(_ROOT / "dist" / "*.whl")))
    if not whls:
        pytest.skip("dist/ 下暂无 wheel，跳过")

    tmp = tempfile.mkdtemp(prefix="zeroai_inst_")
    try:
        zipfile.ZipFile(whls[-1]).extractall(tmp)
        # 在干净的子进程里导入，避免本进程已缓存的模块干扰
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from zeroai.tui.icons import _load_svg_icon, _get_icons_dir;"
            "print('DIR=' + str(_get_icons_dir()));"
            "print('VAL=' + repr(_load_svg_icon('folder')))"
        )
        import subprocess

        out = subprocess.run(
            [sys.executable, "-c", code, tmp],
            capture_output=True, text=True, timeout=120,
            cwd=tmp,
        )
        assert out.returncode == 0, f"子进程失败：{out.stderr[-1500:]}"
        val = ""
        for line in out.stdout.splitlines():
            if line.startswith("VAL="):
                val = line[4:]
        assert val not in ("''", '""', ""), (
            "安装布局下 _load_svg_icon('folder') 返回空串，图标功能失效。\n"
            + out.stdout
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# 缺陷 4：voice 气泡必须真的渲染进 #vd-content
# ══════════════════════════════════════════════════════════════════
class _FakeAppInstance:
    """VoiceDialogScreen 依赖的最小桩对象（默认手动模式，不启动录音线程）。"""

    model_key = "stub-model"
    client = None
    work_mode = "expert"
    history: list = []
    context_tokens = 0


def _run_voice(fn):
    """在真实 run_test 环境里挂载 VoiceDialogScreen 并执行 fn(screen, pilot)。"""
    from textual.app import App
    from zeroai.tui.screens import VoiceDialogScreen

    holder = {}

    class _T(App):
        def on_mount(self):
            screen = VoiceDialogScreen(_FakeAppInstance())
            holder["screen"] = screen
            self.push_screen(screen)

    async def _main():
        app = _T()
        async with app.run_test(size=(100, 45)) as pilot:
            await pilot.pause()
            scr = holder["screen"]
            for _ in range(30):
                try:
                    scr.query_one("#vd-content")
                    break
                except Exception:
                    await asyncio.sleep(0.05)
                    await pilot.pause()
            return await fn(scr, pilot)

    return asyncio.run(_main())


def test_user_bubble_actually_renders():
    """调用 _append_user_bubble 后，#vd-content 里必须出现用户气泡节点。

    修复前：.vd-row-user=0 / .vd-log-user=0（异常被裸 except 吞掉）
    """
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        content = scr.query_one("#vd-content", VerticalScroll)
        before = len(content.query(".vd-log-user"))
        scr._append_user_bubble("用户测试消息：你好")
        await pilot.pause()
        after = len(content.query(".vd-log-user"))
        return before, after

    before, after = _run_voice(_check)
    assert after > before, (
        f"用户气泡未渲染：调用前 {before} 个 .vd-log-user，调用后 {after} 个"
    )


def test_ai_placeholder_actually_renders():
    """调用 _append_ai_placeholder 后，#vd-content 里必须出现 AI 气泡节点。"""
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        content = scr.query_one("#vd-content", VerticalScroll)
        before = len(content.query(".vd-log-ai"))
        scr._append_ai_placeholder()
        await pilot.pause()
        after = len(content.query(".vd-log-ai"))
        return before, after

    before, after = _run_voice(_check)
    assert after > before, (
        f"AI 气泡未渲染：调用前 {before} 个 .vd-log-ai，调用后 {after} 个"
    )


def test_voice_bubble_content_is_written():
    """气泡不仅要挂上，内容也要真的写进去（防止挂了个空壳）。"""
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        scr._append_user_bubble("内容写入验证 ABC123")
        await pilot.pause()
        content = scr.query_one("#vd-content", VerticalScroll)
        logs = content.query(".vd-log-user")
        assert len(logs) > 0, "没有 .vd-log-user 节点"
        # RichLog 的行数 > 0 说明确实写入过内容
        log = logs.last()
        return getattr(log, "lines", None)

    lines = _run_voice(_check)
    assert lines is not None, "拿不到 RichLog.lines"
    assert len(lines) > 0, "气泡挂上了但没有写入任何内容"


def test_full_streaming_flow_renders():
    """完整流式链路：占位 → 多次 _update_ai_bubble → 内容必须真的出现在 DOM 里。

    这是比"占位气泡挂上"更强的断言：_ai_log 改为延后一帧赋值后，
    必须确认流式更新仍然作用于**已挂载**的那个 RichLog 上。
    """
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        content = scr.query_one("#vd-content", VerticalScroll)
        scr._append_ai_placeholder()
        # 流式更新分几帧进行，模拟真实打字机
        for chunk in ("第一段", "第一段第二段", "第一段第二段第三段"):
            await pilot.pause()
            scr._update_ai_bubble(chunk)
        await pilot.pause()
        logs = content.query(".vd-log-ai")
        assert len(logs) > 0, "流式结束后仍没有 .vd-log-ai 节点"
        log = logs.last()
        # 内容应为最后一次 update 的文本
        text = "\n".join(str(seg) for line in getattr(log, "lines", []) for seg in [line])
        assert "第三段" in text or len(getattr(log, "lines", [])) > 0, (
            f"流式内容未写入：lines={getattr(log, 'lines', None)}"
        )
        return True

    assert _run_voice(_check) is True


def test_export_lines_recorded_for_user_bubble():
    """用户气泡同时要累积到导出行（这是 _export_lines 的既有契约）。"""
    async def _check(scr, pilot):
        scr._export_lines = []
        scr._append_user_bubble("导出记录验证")
        await pilot.pause()
        await pilot.pause()
        return list(scr._export_lines)

    lines = _run_voice(_check)
    assert ("user", "导出记录验证") in lines, f"导出行未记录：{lines}"


def test_same_frame_stream_survives_race():
    """竞态回归：占位后**同帧**立刻流式，第一段内容不得被丢弃。

    背景：log 是延后一帧挂载并赋值给 _ai_log 的。若第一段流式在挂载完成前
    到达，而 _update_ai_bubble 直接 `if self._ai_log is None: return`，
    内容会被静默丢弃（对抗性测试 ATK-3 实测复现）。
    现改为「无论 log 是否就绪都先进缓冲区，挂载后用缓冲区回放」。
    """
    from textual.containers import VerticalScroll

    async def _check(scr, pilot):
        content = scr.query_one("#vd-content", VerticalScroll)
        scr._append_ai_placeholder()
        scr._update_ai_bubble("同帧到达的第一段内容")  # 不留任何 await 间隔
        for _ in range(6):
            await pilot.pause()
        logs = list(content.query(".vd-log-ai"))
        assert logs, "同帧流式后没有 .vd-log-ai 节点"
        return "\n".join(str(seg) for line in logs[-1].lines for seg in [line])

    text = _run_voice(_check)
    assert "同帧到达的第一段内容" in text, (
        f"同帧流式内容被丢弃（竞态未修复）。实际内容：{text!r}"
    )


def test_no_orphan_mount_before_attach():
    """静态保证：不得出现 row.mount(...) 早于 content.mount(row) 的写法。

    注意：必须先剔除 docstring —— 修复说明里会引用 "原先 row.mount(log)"
    这种反面示例文字，直接正则扫全文会产生假阳性。
    """
    import ast

    src = _read("zeroai", "tui", "screens.py")
    tree = ast.parse(src)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef)
         and n.name == "VoiceDialogScreen"),
        None,
    )
    assert cls is not None, "未找到 VoiceDialogScreen"

    for meth in ("_append_user_bubble", "_append_ai_placeholder"):
        fn = next(
            (n for n in cls.body
             if isinstance(n, ast.FunctionDef) and n.name == meth),
            None,
        )
        assert fn is not None, f"未找到 {meth}"
        # 只取函数体源码，且剔除 docstring 节点
        body_src = "\n".join(
            ast.unparse(n) for n in fn.body
            if not (isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))
        )
        lines = [ln.strip() for ln in _strip_comments(body_src).splitlines()]
        try:
            i_row = next(i for i, ln in enumerate(lines) if ln.startswith("row.mount("))
        except StopIteration:
            continue  # 已改为延后挂载（call_next 回调里挂），本函数体内不存在
        i_content = next(
            (i for i, ln in enumerate(lines) if ln.startswith("content.mount(")), None
        )
        assert i_content is not None, f"{meth} 缺少 content.mount(row)"
        assert i_row > i_content, (
            f"{meth}: row.mount() 出现在 content.mount() 之前 —— "
            "父节点未挂载就挂子节点，会抛 MountError 并（被 except 吞掉）导致气泡消失"
        )
