"""Markdown / LaTeX / 图片预览渲染（聚合入口）

本模块是 tui_agent.py 渲染函数的模块化聚合入口，提供以下能力：
- Markdown 渲染（基于 rich.markdown.Markdown）
- 学术 Markdown 规范化预处理
- LaTeX 公式 → Unicode 转换（含希腊字母、上下标、矩阵、cases 等）
- 图片预览（基于 PIL 半块字符渲染）
- LaTeX 在文本中的自动检测与渲染

依赖方向（X1 之后）：
    zeroai.tui.markdown → zeroai.tools.render / zeroai.tools.academic
本模块**不再**引用 tui_agent，tui_agent 也不引用本模块。
tui_agent 侧仍保留一份同名实现作为回退备份（未删除）。

迁移来源：tui_agent.py 行 3602, 3626, 3679, 4249, 5243, 10332
（真实实现现位于 zeroai/tools/render.py 与 zeroai/tools/academic.py）
"""
# 【X1，2026-09-15】切断对 tui_agent 的反向依赖。
#
# 原写法 `from tui_agent import (...)` 让本模块成为一条循环边的起点：
#     zeroai.tui.app → zeroai.tui.markdown → tui_agent → zeroai.tui.app
# 于是 `import zeroai.tui.app` 会拿到一个「部分初始化」的 zeroai.tui.markdown。
# 沙箱实验（exp_sandbox_test.py）证实：只改 app.py 一侧完全无效，
# 必须由本模块自己承担这一刀。
#
# 改为指向 zeroai.tools.render / zeroai.tools.academic —— 它们才是
# 阶段 D.4 抽取的**真实实现**（不是转发壳），且已逐函数验证与 tui_agent
# 版本行为一致：_safe_markdown 15/15、render_image_preview 3/3、
# render_latex_in_text 5/5、_latex_to_unicode 20/20 输出相同，AST 相同。
from zeroai.tools.render import (
    # Markdown 渲染
    render_markdown,
    _safe_markdown,
    _normalize_markdown_for_academic,
    # LaTeX 渲染
    render_latex_in_text,
    # 图片预览
    render_image_preview,
)
from zeroai.tools.academic import _latex_to_unicode

__all__ = [
    # Markdown 渲染
    "render_markdown",
    "_safe_markdown",
    "_normalize_markdown_for_academic",
    # LaTeX 渲染
    "render_latex_in_text",
    "_latex_to_unicode",
    # 图片预览
    "render_image_preview",
]


def is_loaded_from_tui_agent() -> bool:
    """检查当前模块的实现是否来自 tui_agent.py

    用于诊断和迁移进度跟踪。
    返回 True 表示仍是包装模式（实现住在 tui_agent）；
    返回 False 表示本模块已直接指向 zeroai.tools 中的真实实现。
    """
    import zeroai.tui.markdown as _self
    return _self.render_markdown.__module__ == "tui_agent"


def get_module_info() -> dict:
    """返回模块信息（用于自检和诊断）"""
    return {
        "mode": "wrapper" if is_loaded_from_tui_agent() else "standalone",
        "source": "tui_agent.py" if is_loaded_from_tui_agent() else "zeroai.tui.markdown",
        "exports": list(__all__),
        "count": len(__all__),
    }
