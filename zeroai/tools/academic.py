"""学术研究工具

迁移来源：tui_agent.py 行 3998-4088（LaTeX 常量）、4091-4389（_latex_to_unicode）、
4392-4973（学术搜索/引用校验/文献综述/公式渲染）

提供以下纯函数：
- _latex_to_unicode：将 LaTeX 公式转换为 Unicode 终端可显示文本
- academic_search：学术文献搜索（OpenAlex 主源 + Crossref 兜底）
- arxiv_search：arXiv 预印本论文搜索
- citation_check：校验文献引用真实性
- _format_citation_result：格式化引用校验结果
- literature_review：多文献综合对比分析
- _lit_review_search_papers：OpenAlex/Crossref 检索辅助
- _lit_review_search_arxiv：arXiv 检索辅助
- render_formula：渲染 LaTeX 公式

依赖：
- 标准库：re, json, time, urllib, difflib
- .network.web_fetch（可选，本模块实际直接使用 urllib.request 以获得更细粒度控制）
- zeroai.core.response_utils._jaccard_similarity（可选，本模块实际使用 difflib.SequenceMatcher 做标题相似度）

注意：本模块的学术检索函数直接使用 urllib.request 调用
OpenAlex / Crossref / arXiv API，未通过 web_fetch 中转，
以保证对 API 响应格式（JSON/XML）的精确控制。

检索后端迁移（2026-10-08）：
原本使用 Semantic Scholar Graph API。连续 5 次请求实测 5/5 返回 429
（"Too Many Requests"），无法使用，故迁到 OpenAlex（主）+ Crossref（备）。
两者均无需 API key，均为开放许可，但**都不等于不限流**：

- OpenAlex 对匿名调用发放**日预算**。本会话累计约 170 次请求后触发 429，
  响应为 `Retry-After: 49335`（约 13.7 小时后恢复）、
  body `{"error":"Rate limit exceeded","message":"Insufficient budget..."}`
  —— 是配额耗尽，不是突发。证据见
  evals/results/openalex_429_character.txt
- Crossref 无 key、无明确日配额，但服务端会间歇性返回 500
  （Elasticsearch `CancellationException`），实测单轮 6 次中出现 3 次 500。

因此**双源互备是必需设计而非可选优化**：任一源单独使用都会在某个时刻
不可用。任一源失效时另一源顶上，两者都失效时如实告知"检索失败"，
绝不谎报成功、也绝不用故障冒充"文献不存在"。

实测依据见 evals/results/ 下：
- lit_api_probe.md      首轮全量探针
- lit_api_recheck.md    三个异常的复测（含逐次延迟）
- lit_api_design.md     迁移设计的逐条验证

关键约束（实测得出，改动前请先看这三条）：
1. OpenAlex 的 sort=cited_by_count:desc 会绕过搜索条件 ——
   实测返回 AlphaFold、香农《A Mathematical Theory of Communication》
   等与查询无关的高被引论文。因此"按引用数排序"一律取相关性结果后
   在客户端重排，绝不传该 sort 参数。
2. 未知名 DOI 不总是返回 404：Crossref 实测 5 次中 4 次 404、1 次 500；
   因此 DOI 校验必须双源兜底，不能单看一次 404。
3. 虚构标题不会返回空结果：实测查询
   "A Complete Theory of Zero-Shot Quantum Consciousness Routing"
   仍返回 3 条无关论文（相似度 0.27）。判定虚构只能靠 SequenceMatcher
   相似度阈值，不能靠"结果为空"。
"""
import re
import json
import time
import urllib.request
import urllib.parse
import urllib.error


# ====== LaTeX 符号映射表 ======
# 迁移来源：tui_agent.py 行 3998-4088

# 希腊字母
_LATEX_GREEK = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\varepsilon": "ε", r"\zeta": "ζ", r"\eta": "η",
    r"\theta": "θ", r"\vartheta": "ϑ", r"\iota": "ι", r"\kappa": "κ",
    r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν", r"\xi": "ξ",
    r"\pi": "π", r"\varpi": "ϖ", r"\rho": "ρ", r"\varrho": "ϱ",
    r"\sigma": "σ", r"\varsigma": "ς", r"\tau": "τ", r"\upsilon": "υ",
    r"\phi": "φ", r"\varphi": "φ", r"\chi": "χ", r"\psi": "ψ", r"\omega": "ω",
    r"\Gamma": "Γ", r"\Delta": "Δ", r"\Theta": "Θ", r"\Lambda": "Λ",
    r"\Xi": "Ξ", r"\Pi": "Π", r"\Sigma": "Σ", r"\Upsilon": "Υ",
    r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
}

# 数学运算符与符号
_LATEX_OPERATORS = {
    r"\times": "×", r"\div": "÷", r"\pm": "±", r"\mp": "∓",
    r"\cdot": "·", r"\cdots": "⋯", r"\ldots": "…", r"\vdots": "⋮", r"\ddots": "⋱",
    r"\infty": "∞", r"\partial": "∂", r"\nabla": "∇", r"\forall": "∀", r"\exists": "∃",
    r"\neg": "¬", r"\land": "∧", r"\lor": "∨", r"\oplus": "⊕", r"\ominus": "⊖",
    r"\otimes": "⊗", r"\odot": "⊙", r"\cap": "∩", r"\cup": "∪", r"\setminus": "∖",
    r"\subset": "⊂", r"\supset": "⊃", r"\subseteq": "⊆", r"\supseteq": "⊇",
    r"\in": "∈", r"\notin": "∉", r"\ni": "∋",
    r"\leq": "≤", r"\geq": "≥", r"\neq": "≠", r"\approx": "≈", r"\equiv": "≡",
    r"\sim": "∼", r"\simeq": "≃", r"\cong": "≅", r"\propto": "∝",
    r"\to": "→", r"\rightarrow": "→", r"\leftarrow": "←", r"\gets": "←",
    r"\Rightarrow": "⇒", r"\Leftarrow": "⇐", r"\Leftrightarrow": "⇔", r"\iff": "⟺",
    r"\mapsto": "↦", r"\uparrow": "↑", r"\downarrow": "↓", r"\updownarrow": "↕",
    r"\sum": "Σ", r"\prod": "∏", r"\coprod": "∐", r"\int": "∫", r"\oint": "∮",
    r"\bigcup": "⋃", r"\bigcap": "⋂", r"\bigoplus": "⨁", r"\bigotimes": "⨂",
    r"\sqrt": "√", r"\cubert": "∛", r"\fourthroot": "∜",
    r"\angle": "∠", r"\perp": "⊥", r"\parallel": "∥", r"\triangle": "△",
    r"\circ": "∘", r"\bullet": "•", r"\star": "⋆", r"\dagger": "†", r"\ddagger": "‡",
    r"\aleph": "ℵ", r"\beth": "ℶ", r"\hbar": "ℏ", r"\ell": "ℓ",
    r"\Re": "ℜ", r"\Im": "ℑ", r"\wp": "℘", r"\mho": "℧",
    r"\angle": "∠", r"\measuredangle": "∡", r"\sphericalangle": "∢",
    r"\prime": "′", r"\backprime": "‵",
    r"\colon": ":", r"\vert": "|", r"\Vert": "‖", r"\backslash": "\\",
    r"\degree": "°", r"\circ": "∘",
    r"\leqq": "≦", r"\geqq": "≧", r"\lessgtr": "≶", r"\gtrless": "≷",
    r"\prec": "≺", r"\succ": "≻", r"\preceq": "≼", r"\succeq": "≽",
    r"\emptyset": "∅", r"\varnothing": "∅",
    r"\mathbb{R}": "ℝ", r"\mathbb{Z}": "ℤ", r"\mathbb{Q}": "ℚ",
    r"\mathbb{N}": "ℕ", r"\mathbb{C}": "ℂ", r"\mathbb{H}": "ℍ",
    r"\mathbb{A}": "𝔸", r"\mathbb{B}": "𝔹", r"\mathbb{D}": "𝔻",
    r"\mathbb{E}": "𝔼", r"\mathbb{F}": "𝔽", r"\mathbb{G}": "𝔾",
    r"\mathbb{I}": "𝕀", r"\mathbb{J}": "𝕁", r"\mathbb{K}": "𝕂",
    r"\mathbb{L}": "𝕃", r"\mathbb{M}": "𝕄", r"\mathbb{O}": "𝕆",
    r"\mathbb{P}": "ℙ", r"\mathbb{S}": "𝕊", r"\mathbb{T}": "𝕋",
    r"\mathbb{U}": "𝕌", r"\mathbb{V}": "𝕍", r"\mathbb{W}": "𝕎", r"\mathbb{X}": "𝕏",
    r"\mathbb{Y}": "𝕐",
}

# 下标映射（Unicode 下标字符）
_LATEX_SUBSCRIPT = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "o": "ₒ", "x": "ₓ", "h": "ₕ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "p": "ₚ",
    "s": "ₛ", "t": "ₜ", "i": "ᵢ", "j": "ⱼ", "u": "ᵤ", "v": "ᵥ",
}

# 上标映射（Unicode 上标字符）
_LATEX_SUPERSCRIPT = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ",
    "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ",
    "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ", "o": "ᵒ",
    "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
    "A": "ᴬ", "B": "ᴮ", "D": "ᴰ", "E": "ᴱ", "G": "ᴳ",
    "H": "ᴴ", "I": "ᴵ", "J": "ᴶ", "K": "ᴷ", "L": "ᴸ",
    "M": "ᴹ", "N": "ᴺ", "O": "ᴼ", "P": "ᴾ", "R": "ᴿ",
    "T": "ᵀ", "U": "ᵁ", "V": "ⱽ", "W": "ᵂ",
    "α": "ᵅ", "β": "ᵝ", "γ": "ᵞ", "δ": "ᵟ", "ε": "ᵋ",
    "θ": "ᶿ", "ι": "ᶥ", "φ": "ᵠ", "χ": "ᵡ", "ψ": "ᵧ",
    "n": "ⁿ", "-": "⁻",
}

# 函数名映射（保持原样，不转换）
_LATEX_FUNCTIONS = {
    r"\sin", r"\cos", r"\tan", r"\cot", r"\sec", r"\csc",
    r"\arcsin", r"\arccos", r"\arctan",
    r"\sinh", r"\cosh", r"\tanh", r"\coth",
    r"\log", r"\ln", r"\lg", r"\exp",
    r"\lim", r"\max", r"\min", r"\sup", r"\inf",
    r"\arg", r"\det", r"\dim", r"\gcd", r"\hom", r"\ker", r"\deg",
    r"\operatorname",
}


def _latex_to_unicode(latex: str) -> str:
    r"""将单个 LaTeX 公式转换为 Unicode 终端可显示文本

    支持：
    - 希腊字母：\\alpha → α, \\Sigma → Σ
    - 运算符：\\times → ×, \\sum → Σ, \\int → ∫
    - 上下标：x_1 → x₁, x^2 → x², x_{10} → x₁₀, x^{n+1} → xⁿ⁺¹
    - 分数：\\frac{a}{b} → a⁄b（使用 Unicode 分数斜杠 ⁄ U+2044，比普通 / 更贴近真分数排版）
    - 根号：\\sqrt{x} → √x, \\sqrt[3]{x} → ∛x
    - 求和/积分上下限：\\sum_{i=1}^{n} → Σᵢ₌₁ⁿ
    - 黑板粗体：\\mathbb{R} → ℝ
    - 函数名：\\sin \\cos \\log 等保持原样

    迁移来源：tui_agent.py 行 4091-4389
    """
    s = latex.strip()
    # 去除首尾 $ 符号（已在调用前处理）
    s = s.strip("$")

    # 0. 预处理：\dfrac \tfrac \cfrac 统一当作 \frac 处理
    s = re.sub(r"\\[dtc]frac\b", r"\\frac", s)

    # 0.1 处理 \left( \right) \left[ \right] \left\{ \right\} 等自适应定界符
    s = s.replace(r"\left(", "(").replace(r"\right)", ")")
    s = s.replace(r"\left[", "[").replace(r"\right]", "]")
    s = s.replace(r"\left\{", "{").replace(r"\right\}", "}")
    s = s.replace(r"\left|", "|").replace(r"\right|", "|")
    s = s.replace(r"\left\|", "‖").replace(r"\right\|", "‖")
    s = s.replace(r"\left.", "").replace(r"\right.", "")

    # 0.2 处理 \big \Big \bigg \Bigg 等尺寸前缀（直接去除，保留定界符本身）
    # 关键：必须用 \b 保护 \bigcup \bigcap \bigoplus \bigotimes 等以 big 开头的命令不被误删
    s = re.sub(r"\\[bB]ig[lmr]?(?![a-zA-Z])\s*", "", s)
    s = re.sub(r"\\big[lmr]?(?![a-zA-Z])\s*", "", s)

    # 0.3 处理装饰符号（矢量、帽子、横线、波浪号、点导数）
    # 按命令长度降序处理，避免 \dot 匹配 \ddot 的前缀
    _DECO_MAP = [
        (r"\\overline",  "̄"),   # 上划线（组合符 U+0304）
        (r"\\underline", "̱"),   # 下划线（组合符 U+0332）
        (r"\\widehat",   "^"),   # 宽帽子
        (r"\\widetilde", "~"),   # 宽波浪
        (r"\\mathring",  "̊"),   # 圈（组合符 U+030A）
        (r"\\ddot",      "̈"),   # 二阶导数双点（组合符 U+0308）
        (r"\\dot",       "̇"),   # 一阶导数点（组合符 U+0307）
        (r"\\vec",       "→"),   # 矢量箭头（前置）
        (r"\\hat",       "^"),   # 帽子
        (r"\\bar",       "̄"),   # 上横线
        (r"\\tilde",     "~"),   # 波浪号
    ]
    for cmd_pat, deco_sym in _DECO_MAP:
        def _deco_repl(m, ds=deco_sym):
            body_u = _latex_to_unicode(m.group(1))
            if ds == "→":
                return f"→{body_u}"  # 矢量箭头前置
            return f"{body_u}{ds}"   # 组合符号后置
        s = re.sub(cmd_pat + r"\{([^{}]*)\}", _deco_repl, s)

    # 0.4 处理矩阵 \begin{matrix}...\end{matrix} 等
    # 使用 Unicode 矩阵专用括号 ⎡⎢⎣⎤⎥⎦（U+23A1-23A6），比普通 () 更清晰
    def _matrix_repl(m):
        env = m.group(1)
        body = m.group(2)
        # 按 \\ 分行，按 & 分列
        rows = [r.strip() for r in body.split(r"\\") if r.strip()]
        rendered_rows = []
        for row in rows:
            cells = [c.strip() for c in row.split("&")]
            rendered_cells = [_latex_to_unicode(c) for c in cells]
            rendered_rows.append("  ".join(rendered_cells))
        # 单行矩阵：用紧凑形式
        if len(rendered_rows) == 1:
            inner = rendered_rows[0]
            if env == "pmatrix":
                return f"( {inner} )"
            if env == "bmatrix":
                return f"[ {inner} ]"
            if env == "Bmatrix":
                return f"{{ {inner} }}"
            if env == "vmatrix":
                return f"| {inner} |"
            if env == "Vmatrix":
                return f"‖ {inner} ‖"
            return inner
        # 多行矩阵：用矩阵专用括号 ⎡⎢⎣ ⎤⎥⎦
        n = len(rendered_rows)
        # 左括号：第一行⎡，中间行⎢，最后一行⎣
        # 右括号：第一行⎤，中间行⎥，最后一行⎦
        left_brackets = {"pmatrix": ("⎡", "⎢", "⎣"),
                         "bmatrix": ("⎡", "⎢", "⎣"),
                         "Bmatrix": ("⎧", "⎨", "⎩"),
                         "vmatrix": ("⎢", "⎢", "⎢"),
                         "Vmatrix": ("⎢", "⎢", "⎢")}
        right_brackets = {"pmatrix": ("⎤", "⎥", "⎦"),
                          "bmatrix": ("⎤", "⎥", "⎦"),
                          "Bmatrix": ("⎫", "⎬", "⎭"),
                          "vmatrix": ("⎥", "⎥", "⎥"),
                          "Vmatrix": ("⎥", "⎥", "⎥")}
        lb = left_brackets.get(env, ("", "", ""))
        rb = right_brackets.get(env, ("", "", ""))
        lines = []
        for i, row in enumerate(rendered_rows):
            if n == 1:
                l, r = lb[0], rb[0]
            elif i == 0:
                l, r = lb[0], rb[0]
            elif i == n - 1:
                l, r = lb[2], rb[2]
            else:
                l, r = lb[1], rb[1]
            lines.append(f"{l}{row}{r}")
        return "\n".join(lines)
    s = re.sub(r"\\begin\{(matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix)\}(.*?)\\end\{\1\}",
               _matrix_repl, s, flags=re.DOTALL)

    # 0.5 处理 cases 环境（分段函数）
    def _cases_repl(m):
        body = m.group(1)
        rows = [r.strip() for r in body.split(r"\\") if r.strip()]
        rendered = []
        for row in rows:
            parts = row.split("&")
            if len(parts) == 2:
                cond = _latex_to_unicode(parts[1].strip())
                val = _latex_to_unicode(parts[0].strip())
                rendered.append(f"{val}  当  {cond}")
            else:
                rendered.append(_latex_to_unicode(row.strip()))
        return " { " + " ; ".join(rendered) + " }"
    s = re.sub(r"\\begin\{cases\}(.*?)\\end\{cases\}", _cases_repl, s, flags=re.DOTALL)

    # 0.6 处理 \lim_{x \to a}（极限）
    # 纯 Unicode 下标紧凑表示：limₙ→∞（无花括号，不可映射字符保留原字符）
    def _lim_repl(m):
        sub = m.group(1)
        sub_u = _latex_to_unicode(sub)
        # 逐字符映射为 Unicode 真下标，不可映射字符保留原字符
        result = "".join(_LATEX_SUBSCRIPT.get(ch, ch) for ch in sub_u)
        return "lim" + result
    s = re.sub(r"\\lim_\{([^{}]*)\}", _lim_repl, s)
    s = re.sub(r"\\lim_([a-zA-Z])",
               lambda m: "lim" + _LATEX_SUBSCRIPT.get(m.group(1), m.group(1)), s)

    # 0.7 处理 \sum_{...}^{...} \prod_{...}^{...} \int_{...}^{...} 上下限
    # 纯 Unicode 上下标紧凑表示：Σᵢ₌₁ⁿ（无花括号，不可映射字符保留原字符）
    def _bigop_repl(m):
        op = m.group(1)
        # 补全反斜杠查找运算符符号
        op_u = _LATEX_OPERATORS.get("\\" + op, op)
        low = m.group(2) if m.group(2) else ""
        high = m.group(3) if m.group(3) else ""
        low_u = _latex_to_unicode(low) if low else ""
        high_u = _latex_to_unicode(high) if high else ""
        # 逐字符映射为 Unicode 真下标/上标，不可映射字符保留原字符
        low_result = "".join(_LATEX_SUBSCRIPT.get(ch, ch) for ch in low_u)
        high_result = "".join(_LATEX_SUPERSCRIPT.get(ch, ch) for ch in high_u)
        return f"{op_u}{low_result}{high_result}"
    s = re.sub(r"\\(sum|prod|coprod|int|oint|bigcup|bigcap|bigoplus|bigotimes)_\{([^{}]*)\}\^\{([^{}]*)\}",
               _bigop_repl, s)
    # 单独下标：group(3) 不存在，用空字符串
    def _bigop_low_only(m):
        class _M:
            def group(self, i):
                return [m.group(1), m.group(2), ""][i-1]
        return _bigop_repl(_M())
    s = re.sub(r"\\(sum|prod|coprod|int|oint|bigcup|bigcap|bigoplus|bigotimes)_\{([^{}]*)\}",
               _bigop_low_only, s)
    # 单独上标：group(2) 不存在，用空字符串
    def _bigop_high_only(m):
        class _M:
            def group(self, i):
                return [m.group(1), "", m.group(2)][i-1]
        return _bigop_repl(_M())
    s = re.sub(r"\\(sum|prod|coprod|int|oint|bigcup|bigcap|bigoplus|bigotimes)\^\{([^{}]*)\}",
               _bigop_high_only, s)

    # 1. 处理 \text{...} → 原样输出
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\mathbf\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\mathit\{([^}]*)\}", r"\1", s)

    # 2. 处理 \sqrt[n]{x}（n次根号）
    def _sqrt_n(m):
        n = m.group(1)
        body = _latex_to_unicode(m.group(2))
        root_sym = {"2": "√", "3": "∛", "4": "∜"}.get(n, "√")
        return f"{root_sym}({body})"
    s = re.sub(r"\\sqrt\[([^\]]+)\]\{([^{}]*)\}", _sqrt_n, s)

    # 3. 处理 \sqrt{x}（平方根）
    def _sqrt_simple(m):
        body = _latex_to_unicode(m.group(1))
        return f"√({body})"
    s = re.sub(r"\\sqrt\{([^{}]*)\}", _sqrt_simple, s)

    # 4. 处理 \frac{a}{b}（分数）
    # 使用 Unicode 分数斜杠 ⁄ (U+2044) 代替普通 / ，视觉上更接近真分数
    # 嵌套分数用不同括号区分层次：最内层()，中层[]，外层〔〕
    _FRAC_SLASH = "⁄"  # 分数斜杠（比普通 / 更短、更贴近真分数排版）
    def _frac(m):
        # 去除空格（LaTeX 中 \partial f 表示 ∂f，无空格）
        num = _latex_to_unicode(m.group(1)).replace(" ", "")
        den = _latex_to_unicode(m.group(2)).replace(" ", "")
        # 判断是否嵌套：分子或分母中已含分数斜杠 ⁄
        is_nested = "⁄" in num or "⁄" in den
        # 简单情况用 a⁄b（无括号）
        if len(num) <= 2 and len(den) <= 2 and not is_nested:
            return f"{num}{_FRAC_SLASH}{den}"
        # 嵌套用 []，非嵌套用 ()
        if is_nested:
            return f"〔{num}〕{_FRAC_SLASH}〔{den}〕"
        return f"({num}){_FRAC_SLASH}({den})"
    # 反复处理嵌套分数（8 轮覆盖学术公式嵌套深度）
    for _ in range(8):
        new_s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", _frac, s)
        if new_s == s:
            break
        s = new_s

    # 5. 处理 \binom{n}{k}（二项式系数）
    def _binom(m):
        return f"C({_latex_to_unicode(m.group(1))},{_latex_to_unicode(m.group(2))})"
    s = re.sub(r"\\binom\{([^{}]*)\}\{([^{}]*)\}", _binom, s)

    # 6. 处理 \mathbb{X}（黑板粗体）
    def _mathbb(m):
        ch = m.group(1)
        return _LATEX_OPERATORS.get(rf"\mathbb{{{ch}}}", ch)
    s = re.sub(r"\\mathbb\{([A-Z])\}", _mathbb, s)

    # 7. 替换希腊字母和运算符（按长度降序，避免 \alpha 被 \a 截断）
    # 必须在函数名替换之前，否则 \inf 会匹配 \infty 的前缀
    all_symbols = {**_LATEX_GREEK, **_LATEX_OPERATORS}
    for latex_cmd in sorted(all_symbols.keys(), key=len, reverse=True):
        if latex_cmd in s:
            s = s.replace(latex_cmd, all_symbols[latex_cmd])

    # 8. 处理函数名 \sin \cos 等（替换为纯文本，去掉反斜杠）
    for fn in _LATEX_FUNCTIONS:
        if fn in s:
            s = s.replace(fn, fn[1:])

    # 9. 处理上标 ^{...} 和 ^x
    def _sup_braced(m):
        content = m.group(1)
        # 递归处理内容（如 e^{-x^2} 中的 -x^2）
        content_u = _latex_to_unicode(content)
        result = ""
        for ch in content_u:
            result += _LATEX_SUPERSCRIPT.get(ch, ch)
        return result
    s = re.sub(r"\^\{([^{}]*)\}", _sup_braced, s)

    def _sup_single(m):
        ch = m.group(1)
        return _LATEX_SUPERSCRIPT.get(ch, f"^{ch}")
    s = re.sub(r"\^([a-zA-Z0-9+\-])", _sup_single, s)

    # 10. 处理下标 _{...} 和 _x
    def _sub_braced(m):
        content = m.group(1)
        # 递归处理内容
        content_u = _latex_to_unicode(content)
        # 检查是否所有字符都有 Unicode 下标映射
        all_mappable = all(ch in _LATEX_SUBSCRIPT for ch in content_u)
        if all_mappable:
            return "".join(_LATEX_SUBSCRIPT[ch] for ch in content_u)
        # 含未映射字符（如 b/c/d/f/g/q/r/w/y/z）→ 降级为 _{content}
        return f"_{{{content_u}}}"
    s = re.sub(r"_\{([^{}]*)\}", _sub_braced, s)

    def _sub_single(m):
        ch = m.group(1)
        return _LATEX_SUBSCRIPT.get(ch, f"_{ch}")
    s = re.sub(r"_([a-zA-Z0-9+\-])", _sub_single, s)

    # 11. 清理 LaTeX 空格命令 \, \; \: \! \quad \qquad
    s = re.sub(r"\\[,;:!]", " ", s)
    s = re.sub(r"\\quad\b", "  ", s)
    s = re.sub(r"\\qquad\b", "    ", s)
    # 清理 LaTeX 换行符 \\（含带间距版本 \\[2em]）和反斜杠空格 \ （必须在 \字母 清理之前）
    # \\[2em] → 换行；\\ → 换行；\ （反斜杠+空格）→ 空格
    s = re.sub(r"\\\\\[[^\]]*\]", "\n", s)   # \\[2em] 带间距换行
    s = re.sub(r"\\\\", "\n", s)             # \\ 换行
    s = re.sub(r"\\\s+", " ", s)             # \ + 空格（LaTeX 空格命令）
    # 清理剩余的 LaTeX 命令（\xxx 形式，保留文本）
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    # 清理孤立的反斜杠（\ + 非字母非数字非空格，如 \时 \趋近 等模型错误输出）
    s = re.sub(r"\\(?![a-zA-Z0-9\s])", "", s)

    # 12. 清理多余的空格和花括号
    # 清理花括号：保留 _{...} 和 ^{...} 中的花括号（LaTeX 风格下标/上标标记）
    # 只清理"独立的"花括号（不在 _ 或 ^ 后面的）
    s = re.sub(r"(?<![_^])\{([^{}]*)\}", r"\1", s)
    # 第二轮：清理剩余的独立花括号（第一轮可能产生新的独立花括号）
    s = re.sub(r"(?<![_^])\{([^{}]*)\}", r"\1", s)
    # 合并多余空格
    s = re.sub(r"  +", " ", s).strip()

    return s


# ====== 文献检索后端：OpenAlex（主）+ Crossref（备）+ arXiv（备）======
#
# 迁移原因见模块 docstring。所有常量与重试策略均由实测定出，
# 证据在 evals/results/lit_api_*.md，改动前请先读。

_UA = "ZeroAI/1.1 (academic-research; mailto:zeroai@example.com)"
_OA_BASE = "https://api.openalex.org"
_CR_BASE = "https://api.crossref.org"
_ARXIV_API = "https://export.arxiv.org/api/query"
_MAILTO = "zeroai@example.com"

# 实测：OpenAlex 成功请求 p50≈1.3-1.5s，但失败请求会挂到 21-33s 才超时
# （lit_api_recheck.md #07/#09/#19 三次 -1，分别 33386/31200/26156ms）。
# 故超时收紧到 10s，宁可早失败早走兜底，也不让用户干等半分钟。
_LIT_TIMEOUT = 10

# 实测：Crossref 的 500 是服务端抖动而非限流，重试一次成功率明显回升；
# OpenAlex 无 429 但约 15% 请求超时，同样值得重试一次。
_LIT_RETRY = 1
_LIT_BACKOFF = 0.5

# 可重试的**瞬时**错误。
#
# 429 被刻意排除在重试之外，理由是实测出来的，不是拍脑袋：
# OpenAlex 的 429 不是突发限流，而是**日预算耗尽** ——
#   Retry-After: 49335（约 13.7 小时后才恢复）
#   body: {"error":"Rate limit exceeded","message":"Insufficient budget..."}
# 对一个 13.7 小时后才恢复的错误做 0.5 秒重试毫无意义，只会白白多等；
# 直接交给兜底源更快也更诚实。
# 证据：evals/results/openalex_429_character.txt
# （本模块迁移初期曾把 429 放进可重试集合，是基于轻载下 0/20 的
#  采样得出的错误结论，采样不足已由上表推翻。）
_RETRYABLE = {500, 502, 503, 504}


def _json_or_none(body: bytes):
    """bytes -> dict/list，任何异常返回 None。"""
    try:
        return json.loads(body.decode("utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001
        return None


def _lit_get(url: str, timeout: int = _LIT_TIMEOUT):
    """GET 一个文献 API，返回 (status, body_bytes)。

    status < 0 表示网络层失败（超时/连接错误）。
    429/5xx 与网络失败各重试 _LIT_RETRY 次；其余 4xx 直接返回。
    **不向外抛异常** —— 调用方一律按 status 分支，避免一个接口挂掉
    把整个工具打成未捕获异常。
    """
    code, body = -1, b""
    for attempt in range(_LIT_RETRY + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept": "application/json, application/xml, */*",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            body = e.read() if e.fp else b""
            code = e.code
            if code in _RETRYABLE and attempt < _LIT_RETRY:
                time.sleep(_LIT_BACKOFF)
                continue
            return code, body
        except Exception:  # noqa: BLE001  超时/连接重置/DNS
            if attempt < _LIT_RETRY:
                time.sleep(_LIT_BACKOFF)
                continue
            return -1, b""
    return code, body


def _oa_abstract(work: dict) -> str:
    """把 OpenAlex 的 abstract_inverted_index 还原成可读文本。

    OpenAlex 不给摘要原文，只给 {词: [位置...]} 的倒排索引，必须重建。
    实测（lit_api_design.md D3）：还原后词序连贯，"The dominant sequence
    transduction models are based on complex recurrent..."，118 个 token
    还原为 1136 字符，无跳序、无重复。
    """
    inv = work.get("abstract_inverted_index")
    if not isinstance(inv, dict) or not inv:
        return ""
    pos = {}
    for word, offsets in inv.items():
        if not isinstance(offsets, list):
            continue
        for x in offsets:
            pos[x] = word
    return " ".join(pos[i] for i in sorted(pos))


def _strip_jats(text: str) -> str:
    """Crossref 摘要带 JATS XML 标签（<jats:p> 等），去掉标签留正文。"""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", t).strip()


def _oa_to_s2(work: dict) -> dict:
    """把 OpenAlex work 归一化成本模块沿用的 Semantic Scholar 形状。

    归一化而不是重写消费方，是为了让 _format_citation_result、
    literature_review 等下游拿到的字段名保持不变。

    差异（诚实标注，不编造）：
    - influentialCitationCount -> None：OpenAlex 不提供该指标，
      显示层据此隐藏"影响力"一列，而不是填 0 冒充。
    - citations 对应 cited_by_count。
    """
    if not isinstance(work, dict):
        return {}
    authors = []
    for a in (work.get("authorships") or []):
        name = ((a.get("author") or {}).get("display_name")) or ""
        if name:
            authors.append({"name": name})
    doi = work.get("doi") or ""
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    loc = work.get("primary_location") or {}
    url = loc.get("landing_page_url") or ""
    if not url and doi:
        url = "https://doi.org/" + doi
    if not url:
        url = work.get("id") or ""
    arxiv_id = ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?",
                  url or "", re.I)
    if m:
        arxiv_id = m.group(1)
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "authors": authors,
        "year": work.get("publication_year") or "未知",
        "citationCount": work.get("cited_by_count") or 0,
        "influentialCitationCount": None,
        "abstract": _oa_abstract(work),
        "externalIds": {"DOI": doi, "ArXiv": arxiv_id},
        "url": url,
        "_source": "OpenAlex",
    }


def _cr_to_s2(item: dict) -> dict:
    """把 Crossref work item 归一化成同上形状。"""
    if not isinstance(item, dict):
        return {}
    titles = item.get("title") or []
    title = titles[0] if titles else ""
    authors = []
    for a in (item.get("author") or []):
        nm = " ".join(x for x in (a.get("given") or "", a.get("family") or "")
                      if x).strip()
        if nm:
            authors.append({"name": nm})
    year = "未知"
    parts = ((item.get("issued") or {}).get("date-parts") or [])
    if parts and parts[0] and parts[0][0]:
        year = parts[0][0]
    doi = item.get("DOI") or ""
    url = item.get("URL") or (("https://doi.org/" + doi) if doi else "")
    arxiv_id = ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?",
                  url or "", re.I)
    if m:
        arxiv_id = m.group(1)
    return {
        "title": title,
        "authors": authors,
        "year": year,
        # Crossref 只有 is-referenced-by-count，语义接近被引数
        "citationCount": item.get("is-referenced-by-count") or 0,
        "influentialCitationCount": None,
        "abstract": _strip_jats(item.get("abstract") or ""),
        "externalIds": {"DOI": doi, "ArXiv": arxiv_id},
        "url": url,
        "_source": "Crossref",
    }


def _oa_search(query: str, limit: int, year_from: int = 0,
               year_to: int = 0):
    """OpenAlex 关键词检索。

    返回 (papers, total)；失败返回 None（调用方据此走 Crossref 兜底）。
    **不传 sort 参数** —— 实测 sort=cited_by_count:desc 会绕过搜索条件。
    """
    params = {"search": query, "per_page": str(limit), "mailto": _MAILTO}
    if year_from or year_to:
        params["filter"] = ("from_publication_date:%d-01-01,"
                            "to_publication_date:%d-12-31"
                            % (year_from or 1900, year_to or 2099))
    url = _OA_BASE + "/works?" + urllib.parse.urlencode(params)
    code, body = _lit_get(url)
    if code != 200:
        return None
    data = _json_or_none(body)
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list):
        return None
    total = ((data.get("meta") or {}).get("total")
             or (data.get("meta") or {}).get("count") or 0)
    return [_oa_to_s2(w) for w in results], total


def _cr_search(query: str, limit: int, year_from: int = 0,
               year_to: int = 0):
    """Crossref 关键词检索（兜底源）。

    用 query.title 而非 query.bibliographic：实测后者的相关性明显更差
    （搜 attention 却返回 CISO 论文，且 is-referenced-by-count 全为 0），
    而 query.title 能直接命中《Attention Is All You Need》。
    """
    params = {"query.title": query, "rows": str(limit), "mailto": _MAILTO}
    if year_from or year_to:
        params["filter"] = ("from-pub-date:%d-01-01,"
                            "until-pub-date:%d-12-31"
                            % (year_from or 1900, year_to or 2099))
    url = _CR_BASE + "/works?" + urllib.parse.urlencode(params)
    code, body = _lit_get(url)
    if code != 200:
        return None
    data = _json_or_none(body)
    if not isinstance(data, dict):
        return None
    msg = data.get("message")
    if not isinstance(msg, dict):
        return None
    items = msg.get("items")
    if not isinstance(items, list):
        return None
    return [_cr_to_s2(it) for it in items], msg.get("total-results") or 0


def _search_any(query: str, limit: int, year_from: int = 0,
                year_to: int = 0):
    """双源检索：OpenAlex 优先，失败落 Crossref。

    返回 (papers, total, source_name)；两个源都失败返回 (None, 0, None)。
    """
    res = _oa_search(query, limit, year_from, year_to)
    if res is not None:
        return res[0], res[1], "OpenAlex"
    res = _cr_search(query, limit, year_from, year_to)
    if res is not None:
        return res[0], res[1], "Crossref"
    return None, 0, None


def _sort_by_citations(papers: list) -> list:
    """按被引数降序，在**已取回的相关性结果内**重排。

    不能改用 OpenAlex 服务端 sort=cited_by_count:desc：实测该参数会
    让搜索条件失效（lit_api_design.md D1，两种写法都试过，均返回
    AlphaFold / HISTORIAE 等无关论文）。因此这里是刻意的客户端重排，
    排序范围受限于已取回条数，属于近似 —— 输出文案已注明。
    """
    return sorted(papers, key=lambda p: (p.get("citationCount") or 0),
                  reverse=True)


def academic_search(query: str, num_results: int = 5, year_from: int = 0,
                    year_to: int = 0, sort_by: str = "relevance") -> str:
    """学术文献搜索（OpenAlex 主源 + Crossref 兜底，均无需 API Key）

    参数：
    - query: 搜索关键词（中英文均可）
    - num_results: 返回结果数量，默认5，最大20
    - year_from: 起始年份（如 2020），0表示不限
    - year_to: 结束年份（如 2024），0表示不限
    - sort_by: 排序方式：relevance(相关性，默认) / citations(引用数)
               / influence(影响力，OpenAlex 无此指标，退化为按引用数)

    返回：格式化的文献列表，含标题、作者、年份、引用数、摘要、DOI

    迁移来源：tui_agent.py 行 4392-4491
    """
    try:
        limit = min(num_results, 20)
        want_citation_order = sort_by in ("citations", "influence")

        # 按引用数排序时多取一些再客户端重排：服务端 sort 会让搜索条件
        # 失效（见 _sort_by_citations 与模块 docstring 约束 1）。
        fetch = max(limit, 50) if want_citation_order else limit

        papers, total, source = _search_any(query, fetch, year_from, year_to)

        if papers is None:
            return (f"学术搜索失败：OpenAlex 与 Crossref 均未响应。"
                    f"可能是网络问题或两个源同时抖动，请稍后重试")
        if not papers:
            return f"(未找到关于「{query}」的学术文献，试试更换关键词或扩大年份范围)"

        if want_citation_order:
            papers = _sort_by_citations(papers)[:limit]

        results = []
        for i, p in enumerate(papers, 1):
            title = p.get("title") or "无标题"
            authors = p.get("authors") or []
            author_str = ", ".join(a.get("name", "?") for a in authors[:5])
            if len(authors) > 5:
                author_str += f" 等 {len(authors)} 人"
            year = p.get("year") or "未知年份"
            citations = p.get("citationCount")
            influential = p.get("influentialCitationCount")
            abstract = p.get("abstract") or ""
            if abstract:
                # 摘要截断到300字
                abstract = abstract[:300] + ("..." if len(abstract) > 300 else "")
            else:
                abstract = "(无摘要)"

            ext_ids = p.get("externalIds") or {}
            doi = ext_ids.get("DOI", "")
            arxiv_id = ext_ids.get("ArXiv", "")
            paper_url = p.get("url", "")

            # 格式化输出
            line = f"[{i}] {title}\n"
            line += f"    作者: {author_str}\n"
            meta = f"    年份: {year}    引用: {citations if citations is not None else '未知'}"
            # OpenAlex / Crossref 均不提供 influentialCitationCount，
            # 为 None 时隐藏该列，而不是显示 0 冒充真实指标。
            if influential is not None:
                meta += f"    影响力: {influential}"
            line += meta + "\n"
            if doi:
                line += f"    DOI: {doi}\n"
            if arxiv_id:
                line += f"    arXiv: {arxiv_id}\n"
            if paper_url:
                line += f"    链接: {paper_url}\n"
            line += f"    摘要: {abstract}\n"
            results.append(line)

        header = f"=== 学术搜索: 「{query}」 ===\n"
        # 「关键词命中数」而非「相关论文」—— OpenAlex 的 meta.count 与
        # Crossref 的 total-results 都只是匹配数，Crossref 实测
        # query.title 对该查询给出 1,033,389，说成"篇相关论文"会严重高估。
        header += (f"关键词命中 {total} 篇（匹配数），"
                   f"显示前 {len(papers)} 篇")
        notes = []
        if year_from or year_to:
            notes.append(f"年份: {year_from or '不限'}-{year_to or '至今'}")
        if sort_by != "relevance":
            if sort_by == "influence":
                notes.append("按引用数排序（数据源无影响力指标，此为近似）")
            else:
                notes.append("按引用数排序（在相关性结果内重排）")
        notes.append(f"数据源: {source}")
        if notes:
            header += "（" + "，".join(notes) + "）"
        header += "\n\n"

        return header + "\n".join(results)

    except Exception as e:  # noqa: BLE001  兜底，工具不能把异常抛给调用方
        return f"学术搜索错误：{e}"


def arxiv_search(query: str, num_results: int = 5, sort_by: str = "relevance",
                 category: str = "") -> str:
    """arXiv 预印本论文搜索（物理/数学/计算机科学/定量生物学/定量金融/统计学）

    参数：
    - query: 搜索关键词（英文效果更佳，支持标题/摘要/作者搜索）
    - num_results: 返回结果数量，默认5，最大20
    - sort_by: 排序方式：relevance(相关性，默认) / submittedDate(最新提交) / lastUpdatedDate(最近更新)
    - category: 学科分类筛选，如 cs.AI(人工智能) / cs.CL(计算语言学) / math.AG(代数几何) /
                physics(物理) / stat.ML(统计机器学习)。留空表示不限

    返回：格式化的论文列表，含标题、作者、摘要、arXiv ID、提交日期、PDF链接

    迁移来源：tui_agent.py 行 4494-4596
    """
    try:
        q = urllib.parse.quote(query)
        # 排序参数
        sort_map = {
            "relevance": "relevance",
            "submittedDate": "submittedDate",
            "lastUpdatedDate": "lastUpdatedDate",
        }
        sort_param = sort_map.get(sort_by, "relevance")

        # 分类筛选
        cat_filter = f"cat:{category}" if category else "all"

        # arXiv API（Atom XML 格式，完全免费）
        url = (f"http://export.arxiv.org/api/query?search_query={cat_filter}:{q}"
               f"&start=0&max_results={min(num_results, 20)}"
               f"&sortBy={sort_param}&sortOrder=descending")

        req = urllib.request.Request(url, headers={
            "User-Agent": "ZeroAI/1.0 (Academic Research)",
            "Accept": "application/atom+xml"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8", errors="ignore")

        # 解析 Atom XML（用正则避免引入 xml.etree，保持轻量）
        entries = re.findall(r'<entry>([\s\S]*?)</entry>', xml_data)
        if not entries:
            return f"(未找到关于「{query}」的 arXiv 论文，试试用英文关键词)"

        results = []
        for i, entry in enumerate(entries, 1):
            # 提取标题
            title_m = re.search(r'<title>([\s\S]*?)</title>', entry)
            title = title_m.group(1).strip() if title_m else "无标题"
            title = re.sub(r'\s+', ' ', title)  # 清理换行

            # 提取作者
            authors = re.findall(r'<name>([^<]+)</name>', entry)
            author_str = ", ".join(authors[:5])
            if len(authors) > 5:
                author_str += f" 等 {len(authors)} 人"

            # 提取摘要
            summary_m = re.search(r'<summary>([\s\S]*?)</summary>', entry)
            abstract = summary_m.group(1).strip() if summary_m else "(无摘要)"
            abstract = re.sub(r'<[^>]+>', '', abstract)
            abstract = re.sub(r'\s+', ' ', abstract)
            if len(abstract) > 300:
                abstract = abstract[:300] + "..."

            # 提取 arXiv ID 和链接
            id_m = re.search(r'<id>http://arxiv.org/abs/([^<]+)</id>', entry)
            arxiv_id = id_m.group(1).strip() if id_m else "未知"
            pdf_link = f"http://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id != "未知" else ""

            # 提取提交日期
            published_m = re.search(r'<published>([^<]+)</published>', entry)
            published = published_m.group(1)[:10] if published_m else "未知日期"

            # 提取分类
            categories = re.findall(r'term="([^"]+)"', entry)
            cat_str = ", ".join(categories[:3]) if categories else "未分类"

            # 格式化输出
            line = f"[{i}] {title}\n"
            line += f"    作者: {author_str}\n"
            line += f"    arXiv: {arxiv_id}    提交: {published}\n"
            line += f"    分类: {cat_str}\n"
            if pdf_link:
                line += f"    PDF: {pdf_link}\n"
            line += f"    摘要: {abstract}\n"
            results.append(line)

        total_m = re.search(r'<opensearch:totalResults[^>]*>([^<]+)</opensearch:totalResults>', xml_data)
        total = total_m.group(1) if total_m else str(len(entries))

        header = f"=== arXiv 搜索: 「{query}」 ===\n"
        header += f"共找到 {total} 篇预印本论文，显示前 {len(entries)} 篇"
        if category:
            header += f"（分类: {category}）"
        if sort_by != "relevance":
            sort_label = {"submittedDate": "最新提交", "lastUpdatedDate": "最近更新"}.get(sort_by, sort_by)
            header += f"，按{sort_label}排序"
        header += "\n\n"

        return header + "\n".join(results)

    except Exception as e:
        return f"arXiv 搜索错误：{e}"


def _format_citation_result(data: dict, method: str, query: str, similarity: float = 1.0) -> str:
    """格式化引用校验结果（citation_check 的辅助函数）

    迁移来源：tui_agent.py 行 4674-4712
    """
    title = data.get("title", "无标题")
    authors = data.get("authors", [])
    author_str = ", ".join(a.get("name", "?") for a in authors[:5])
    if len(authors) > 5:
        author_str += f" 等 {len(authors)} 人"
    year = data.get("year", "未知")
    # arXiv 官方 API 不提供被引数；OpenAlex/Crossref 之外的源也可能缺失。
    # 缺失时显示"未知"，不填 0 —— 0 是一个会被读成真实数据的数字。
    citations = data.get("citationCount")
    citations_str = "未知" if citations is None else str(citations)
    ext_ids = data.get("externalIds", {})
    doi = ext_ids.get("DOI", "")
    arxiv = ext_ids.get("ArXiv", "")
    paper_url = data.get("url", "")
    source = data.get("_source", "")

    # 判定状态
    if similarity >= 0.95:
        status = "✓ 验证通过：文献真实存在（标题精确匹配）"
    elif similarity >= 0.80:
        status = f"✓ 验证通过：文献真实存在（标题相似度 {similarity:.0%}，请核实标题是否完全一致）"
    elif similarity >= 0.60:
        status = f"⚠ 部分匹配（相似度 {similarity:.0%}）：找到相关文献，但标题不完全一致，请核实是否为同一篇"
    else:
        status = f"⚠ 匹配度低（{similarity:.0%}）：可能不是同一篇文献，请人工核实"

    result = f"=== 引用校验结果 ===\n"
    result += f"校验方式：{method}\n"
    result += f"查询条件：{query}\n"
    result += f"状态：{status}\n\n"
    result += f"文献信息：\n"
    result += f"  标题：{title}\n"
    result += f"  作者：{author_str}\n"
    result += f"  年份：{year}    引用数：{citations_str}\n"
    if source:
        result += f"  数据源：{source}\n"
    if doi:
        result += f"  DOI：{doi}\n"
    if arxiv:
        result += f"  arXiv：{arxiv}\n"
    if paper_url:
        result += f"  链接：{paper_url}\n"
    return result


def _oa_by_doi(doi: str):
    """OpenAlex 按 DOI 精确查。

    返回 (state, paper)，state ∈ {"found", "not_found",
                                   "rate_limited", "error"}。
    **必须区分 not_found 与 error** —— 把网络失败当成"文献不存在"，
    等于用工具的故障去指控用户编造引用，是最不能犯的错。
    单列 rate_limited 是因为实测 OpenAlex 的 429 源自日配额耗尽
    （Retry-After 49335s），混进 error 会让用户看不出真实原因，
    也看不出过几小时就能恢复。
    """
    url = _OA_BASE + "/works/https://doi.org/" + urllib.parse.quote(doi.strip())
    code, body = _lit_get(url)
    if code == 200:
        data = _json_or_none(body)
        if isinstance(data, dict) and data.get("id"):
            return "found", _oa_to_s2(data)
        return "error", None
    if code == 404:
        return "not_found", None
    if code == 429:
        return "rate_limited", None
    return "error", None


def _cr_by_doi(doi: str):
    """Crossref 按 DOI 精确查（DOI 注册机构，权威）。

    同样返回 (state, paper)。实测未知名 DOI 5 次中 4 次 404、1 次 500，
    所以单靠 Crossref 判"不存在"不可靠，必须双源交叉。

    更关键的是：Crossref 只收录其自己注册的 DOI，DataCite 系
    （10.48550/arXiv.*、10.5281/zenodo.* 等）在 Crossref 上一律 404。
    因此 **Crossref 单源 404 绝不能推出"该文献不存在"** ——
    这正是 citation_check 要求双源都明确 not_found 才下结论的原因。
    """
    url = _CR_BASE + "/works/" + urllib.parse.quote(doi.strip())
    code, body = _lit_get(url)
    if code == 200:
        data = _json_or_none(body)
        msg = data.get("message") if isinstance(data, dict) else None
        if isinstance(msg, dict) and msg.get("DOI"):
            return "found", _cr_to_s2(msg)
        return "error", None
    if code in (404, 400):
        return "not_found", None
    if code == 429:
        return "rate_limited", None
    return "error", None


def _arxiv_by_id(arxiv_id: str):
    """arXiv 官方 API 按 ID 查询。返回 (state, paper)。

    arXiv 是 arXiv 编号的权威源，但**不提供被引数**，故
    citationCount=None（显示层渲染为"未知"，不填 0）。
    """
    aid = (arxiv_id or "").strip()
    if not aid:
        return "error", None
    url = _ARXIV_API + "?id_list=" + urllib.parse.quote(aid)
    code, body = _lit_get(url, timeout=12)
    if code in (400, 404):
        return "not_found", None
    if code != 200 or not body:
        return "error", None
    txt = body.decode("utf-8", errors="ignore")
    if "<entry>" not in txt:
        return "not_found", None
    entry = txt.split("<entry>", 1)[1].split("</entry>", 1)[0]

    def _tag(name):
        m = re.search(r"<%s>(.*?)</%s>" % (name, name), entry, re.S)
        return m.group(1).strip() if m else ""

    title = re.sub(r"\s+", " ", _tag("title"))
    # arXiv 对不存在/格式非法的 ID 有时返回 HTTP 200 + 标题为 Error 的
    # 占位条目，不能当成真实文献。
    if not title or "error" in title.lower():
        return "not_found", None
    published = _tag("published")
    authors = [{"name": a.strip()}
               for a in re.findall(r"<author>\s*<name>(.*?)</name>",
                                   entry, re.S) if a.strip()]
    idm = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", entry)
    arx = re.sub(r"v\d+$", "", (idm.group(1).strip() if idm else aid))
    return "found", {
        "title": title,
        "authors": authors,
        "year": (published[:4] if published else "未知"),
        "citationCount": None,
        "influentialCitationCount": None,
        "abstract": re.sub(r"\s+", " ", _tag("summary")),
        "externalIds": {"DOI": _tag("doi"), "ArXiv": arx},
        "url": "https://arxiv.org/abs/" + arx,
        "_source": "arXiv",
    }


def _fabricated_warning(title: str, why: str, ratio=None) -> str:
    """标题校验不通过时的结论。

    这里刻意说得比原来更硬：实测虚构标题不会返回空结果（会返回无关
    论文，相似度 0.27），所以只可能在相似度过低时才走到这里，
    此时给出"很可能虚构"的结论是有依据的。
    """
    lines = [
        "⚠ 引用校验结果：未找到标题足够接近的文献",
        f"  查询条件：标题「{title}」",
        f"  {why}",
    ]
    if ratio is not None:
        lines.append(f"  最佳候选相似度：{ratio:.0%}（阈值 60%，低于此判定为不匹配）")
    lines.append("  该引用很可能是 AI 编造的虚构文献，请勿在学术写作中使用")
    lines.append("  建议：使用 academic_search 搜索真实存在的文献替代")
    return "\n".join(lines)


def citation_check(title: str = "", doi: str = "", arxiv_id: str = "") -> str:
    """校验文献引用真实性（防止 AI 编造不存在的文献）

    通过 OpenAlex + Crossref 双源交叉验证文献是否真实存在。
    支持三种查询方式：标题匹配、DOI 查询、arXiv ID 查询。

    参数：
    - title: 文献标题（精确或近似标题）
    - doi: 文献的 DOI（如 10.1038/s41586-021-03819-2）
    - arxiv_id: arXiv 编号（如 2301.00234）

    返回：校验结果，含文献真实状态、正确标题、作者、年份等元数据

    判定原则：
    - 只有「双源都明确查无此记录」才下「不存在」结论；
      任一源网络失败则如实说明无法确认，绝不把工具故障说成引用造假。
    - 标题匹配取多条结果里相似度最高的那条，不只看首条；
      相似度 < 60% 判为不匹配。

    迁移来源：tui_agent.py 行 4599-4671
    """
    try:
        # ── DOI：OpenAlex 优先，Crossref 兜底 ──
        if doi:
            oa_state, oa_paper = _oa_by_doi(doi)
            cr_state, cr_paper = _cr_by_doi(doi)
            if oa_state == "found":
                return _format_citation_result(oa_paper, "DOI", doi)
            if cr_state == "found":
                return _format_citation_result(cr_paper, "DOI", doi)
            # rate_limited 同样不能下「不存在」结论 —— 它表示源没查成，
            # 而不是查到了"没有"。
            if any(s in ("error", "rate_limited") for s in (oa_state, cr_state)):
                lines = [
                    "⚠ 引用校验未完成：无法确认该 DOI 是否真实存在",
                    f"  查询条件：DOI:{doi}",
                    f"  OpenAlex: {oa_state}    Crossref: {cr_state}",
                ]
                if "rate_limited" in (oa_state, cr_state):
                    lines.append(
                        "  原因：OpenAlex 日配额已用尽（服务端给出的恢复时间"
                        "约十几小时），与该引用本身无关。")
                lines.append("  注意：这**不代表该引用是假的**，"
                             "只是本次两个源没有全部查询成功。")
                lines.append("  请稍后重试，或用 academic_search 搜索确认。")
                return "\n".join(lines)
            return (
                "✗ 引用校验结果：文献不存在\n"
                f"  查询条件：DOI:{doi}\n"
                "  校验源：OpenAlex + Crossref（双源均明确返回「无此记录」）\n"
                "  该引用很可能是 AI 编造的虚构文献，请勿在学术写作中使用\n"
                "  建议：使用 academic_search 搜索该领域的真实文献")

        # ── arXiv ID：arXiv 官方 API（该编号的权威源）──
        if arxiv_id:
            state, paper = _arxiv_by_id(arxiv_id)
            if state == "found":
                return _format_citation_result(paper, "arXiv", arxiv_id)
            if state == "error":
                return (
                    "⚠ 引用校验未完成：arXiv API 未响应\n"
                    f"  查询条件：arXiv:{arxiv_id.strip()}\n"
                    "  注意：这**不代表该引用是假的**，请稍后重试。")
            return (
                "✗ 引用校验结果：文献不存在\n"
                f"  查询条件：arXiv:{arxiv_id.strip()}\n"
                "  校验源：arXiv 官方 API\n"
                "  该引用很可能是 AI 编造的虚构文献，请勿在学术写作中使用")

        # ── 标题：双源检索 + 最高相似度匹配 ──
        if title:
            papers, _total, _src = _search_any(title, 5)
            if papers is None:
                return (
                    "⚠ 引用校验未完成：OpenAlex 与 Crossref 均未响应\n"
                    f"  查询条件：标题「{title}」\n"
                    "  注意：这**不代表该引用是假的**，只是本次请求失败，"
                    "请稍后重试。")
            if not papers:
                return _fabricated_warning(
                    title, "OpenAlex 与 Crossref 均无任何匹配结果")

            from difflib import SequenceMatcher
            best, best_ratio = None, -1.0
            for p in papers:
                ratio = SequenceMatcher(
                    None, title.lower().strip(),
                    (p.get("title") or "").lower().strip()).ratio()
                if ratio > best_ratio:
                    best, best_ratio = p, ratio
            if best_ratio < 0.60:
                return _fabricated_warning(
                    title, "检索到的候选文献中没有标题足够接近的", best_ratio)
            return _format_citation_result(best, "标题匹配", title,
                                           similarity=best_ratio)

        return "请提供文献标题、DOI 或 arXiv ID 中的至少一个参数"

    except Exception as e:  # noqa: BLE001  工具不能把异常抛给调用方
        return f"引用校验错误：{e}"


def _lit_review_search_papers(topic: str, num: int, year_from: int,
                              year_to: int) -> tuple:
    """literature_review 辅助：从 OpenAlex 检索，失败自动落 Crossref。

    返回 (papers, source_name)：
      - papers      归一化后的文献列表，可能为空
      - source_name 实际**成功返回数据**的源名；两个源都没成功时为 None

    source_name 必须由调用方拿到而不是硬编码在报告里 —— 验收时发现过
    一次静默降级：OpenAlex 与 Crossref 全部失败、结果全来自 arXiv，
    报告却仍打印「数据来源：OpenAlex」，等于谎报出处（见
    evals/results/academic_migration.md E 组）。

    迁移来源：tui_agent.py 行 4866-4900
    （原实现用 Semantic Scholar 的 sort=citationCount:desc 做服务端引用排序）

    OpenAlex 做不到「保持相关性的同时按引用排序」—— 实测
    sort=cited_by_count:desc 会让搜索条件失效（见模块 docstring 约束 1）。
    因此改为多取一些**相关性**结果，排序交给 literature_review 拿到后
    按 citations 客户端完成。排序范围受限于取回条数，属近似。
    """
    papers = []
    try:
        fetch = min(100, max(num * 3, 30))
        rows, _total, source = _search_any(topic, fetch, year_from, year_to)
        if not rows:
            # rows 为 None（两源都失败）时 _search_any 已把 source 置为
            # None；rows 为 []（查询确实无结果）时 source 仍是成功的源名。
            return papers, source
        for p in rows:
            ext = p.get("externalIds") or {}
            papers.append({
                "title": p.get("title", ""),
                "authors": p.get("authors", []),
                "year": p.get("year", 0),
                "citations": p.get("citationCount") or 0,
                "abstract": p.get("abstract", ""),
                "doi": ext.get("DOI", ""),
                "arxiv_id": ext.get("ArXiv", ""),
                "url": p.get("url", ""),
                # 展示侧会截断到 10 字符，OpenAlex/Crossref 均放得下
                "source": source or "?",
            })
        return papers, source
    except Exception:  # noqa: BLE001
        return papers, None


def _lit_review_search_arxiv(topic: str, num: int) -> list:
    """literature_review 辅助：从 arXiv 检索最新论文

    迁移来源：tui_agent.py 行 4903-4942
    """
    papers = []
    try:
        q = urllib.parse.quote(topic)
        # 用 all: 搜索 + relevance 排序（确保结果相关性）
        url = (f"http://export.arxiv.org/api/query?search_query=all:{q}"
               f"&start=0&max_results={min(num, 10)}"
               f"&sortBy=relevance&sortOrder=descending")
        req = urllib.request.Request(url, headers={
            "User-Agent": "ZeroAI/1.0 (Academic Literature Review)",
            "Accept": "application/atom+xml"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            xml_data = resp.read().decode("utf-8", errors="ignore")
        entries = re.findall(r'<entry>([\s\S]*?)</entry>', xml_data)
        for entry in entries:
            title_m = re.search(r'<title>([\s\S]*?)</title>', entry)
            title = re.sub(r'\s+', ' ', title_m.group(1).strip()) if title_m else ""
            authors = re.findall(r'<name>([^<]+)</name>', entry)
            summary_m = re.search(r'<summary>([\s\S]*?)</summary>', entry)
            abstract = re.sub(r'\s+', ' ', summary_m.group(1).strip()) if summary_m else ""
            id_m = re.search(r'<id>http://arxiv.org/abs/([^<]+)</id>', entry)
            arxiv_id = id_m.group(1).strip() if id_m else ""
            published_m = re.search(r'<published>([^<]+)</published>', entry)
            year = int(published_m.group(1)[:4]) if published_m else 0
            papers.append({
                "title": title,
                "authors": [{"name": a} for a in authors],
                "year": year,
                "citations": 0,
                "abstract": abstract,
                "doi": "",
                "arxiv_id": arxiv_id,
                "url": f"http://arxiv.org/abs/{arxiv_id}",
                "source": "arXiv",
            })
    except Exception:
        pass
    return papers


def literature_review(topic: str, num_papers: int = 10, year_from: int = 0,
                      year_to: int = 0) -> str:
    """多文献综合对比分析（自动检索+结构化对比+研究空白识别）

    自动执行完整的文献综述流程：
    1. 检索相关文献（OpenAlex + arXiv 双源，OpenAlex 失败自动落 Crossref）
    2. 按引用数筛选高质量文献
    3. 结构化提取每篇文献的方法/结论/局限
    4. 生成对比分析表
    5. 识别研究空白和未来方向

    参数：
    - topic: 研究主题（中英文均可，如 '钠离子电池层状氧化物正极' 或 'sodium-ion battery layered oxide cathode'）
    - num_papers: 分析文献数量，默认10，最大20
    - year_from: 起始年份（如 2018），0表示不限
    - year_to: 结束年份（如 2025），0表示不限

    返回：结构化文献综述分析报告

    迁移来源：tui_agent.py 行 4715-4863
    """
    try:
        # ── 第1步：双源检索 ──
        all_papers = []

        # OpenAlex（取相关性结果，多取一些便于后续按引用数排序；
        # 无法用服务端引用排序，原因见 _lit_review_search_papers）
        scholar_papers, scholar_source = _lit_review_search_papers(
            topic, num_papers, year_from, year_to)
        all_papers.extend(scholar_papers)

        # arXiv（最新研究，按提交日期排序）
        arxiv_papers = _lit_review_search_arxiv(topic, min(num_papers // 2, 5))
        all_papers.extend(arxiv_papers)

        if not all_papers:
            return (f"=== 文献综述分析：{topic} ===\n\n"
                    f"未找到相关文献，请尝试更换关键词或扩大年份范围\n"
                    f"建议：使用英文关键词（如 'sodium-ion battery cathode'）效果更佳")

        # ── 第2步：去重（按标题模糊匹配） ──
        from difflib import SequenceMatcher
        unique_papers = []
        seen_titles = []
        for p in all_papers:
            is_dup = False
            for seen in seen_titles:
                if SequenceMatcher(None, p.get("title", "").lower(), seen.lower()).ratio() > 0.85:
                    is_dup = True
                    break
            if not is_dup:
                unique_papers.append(p)
                seen_titles.append(p.get("title", ""))

        # 按引用数排序，取前 num_papers 篇
        unique_papers.sort(key=lambda x: x.get("citations", 0), reverse=True)
        top_papers = unique_papers[:num_papers]

        # ── 第3步：生成综述报告 ──
        report = f"=== 文献综述分析报告 ===\n"
        report += f"研究主题：{topic}\n"
        report += f"检索范围：{year_from or '不限'} - {year_to or '至今'}\n"
        report += f"分析文献数：{len(top_papers)} 篇（去重后共 {len(unique_papers)} 篇）\n"
        # 数据来源必须反映**实际贡献了结果**的源，不能硬编码。
        # 验收时发现过一次静默降级：主源全挂、结果全来自 arXiv，
        # 报告却仍打印「数据来源：OpenAlex」（见 academic_migration.md E 组）。
        if scholar_source:
            report += f"数据来源：{scholar_source} + arXiv\n"
        else:
            report += "数据来源：仅 arXiv\n"
            report += ("⚠ 主检索源不可用：OpenAlex 与 Crossref 本次均未响应，"
                       "以下结果只覆盖 arXiv 预印本，领域覆盖不完整，"
                       "结论请勿直接引用。\n")
        if len(top_papers) < num_papers:
            report += (f"⚠ 仅凑到 {len(top_papers)} 篇（目标 {num_papers} 篇），"
                       f"可用文献不足，对比分析的样本量偏小。\n")
        report += "\n"

        # ── 文献概览表 ──
        report += "── 一、文献概览 ──\n\n"
        report += f"{'#':<4} {'年份':<6} {'引用':<8} {'标题':<50} {'来源':<12}\n"
        report += "-" * 85 + "\n"
        for i, p in enumerate(top_papers, 1):
            title_short = p.get("title", "无标题")[:48]
            year = str(p.get("year", "?"))[:4]
            cit = str(p.get("citations", 0))[:7]
            source = p.get("source", "?")[:10]
            report += f"{i:<4} {year:<6} {cit:<8} {title_short:<50} {source:<12}\n"

        # ── 详细分析 ──
        report += "\n── 二、逐篇分析 ──\n\n"
        for i, p in enumerate(top_papers, 1):
            report += f"[{i}] {p.get('title', '无标题')}\n"
            authors = p.get("authors", [])
            author_str = ", ".join(a if isinstance(a, str) else a.get("name", "?") for a in authors[:5])
            if len(authors) > 5:
                author_str += f" 等 {len(authors)} 人"
            report += f"    作者：{author_str}\n"
            report += f"    年份：{p.get('year', '?')}    引用数：{p.get('citations', 0)}\n"

            doi = p.get("doi", "")
            arxiv = p.get("arxiv_id", "")
            if doi:
                report += f"    DOI：{doi}\n"
            if arxiv:
                report += f"    arXiv：{arxiv}\n"

            abstract = p.get("abstract", "") or p.get("summary", "")
            if abstract:
                abstract = abstract[:400] + ("..." if len(abstract) > 400 else "")
                report += f"    摘要：{abstract}\n"
            report += "\n"

        # ── 研究趋势分析 ──
        report += "── 三、研究趋势分析 ──\n\n"
        years = [p.get("year", 0) for p in top_papers if p.get("year")]
        if years:
            y_min, y_max = min(years), max(years)
            report += f"时间跨度：{y_min} - {y_max}\n"
            # 按年份统计
            year_dist = {}
            for y in years:
                year_dist[y] = year_dist.get(y, 0) + 1
            report += "年度分布：\n"
            for y in sorted(year_dist.keys()):
                bar = "█" * year_dist[y]
                report += f"  {y}: {bar} ({year_dist[y]}篇)\n"

        # 引用分析
        total_cit = sum(p.get("citations", 0) for p in top_papers)
        avg_cit = total_cit / len(top_papers) if top_papers else 0
        report += f"\n总引用数：{total_cit}    平均引用：{avg_cit:.1f}\n"

        # ── 研究空白与未来方向 ──
        report += "\n── 四、研究空白与未来方向（自动识别） ──\n\n"
        report += "基于检索到的文献，以下方向值得关注（需结合专业知识进一步验证）：\n"
        # 基于文献年份和引用数推断
        recent_papers = [p for p in top_papers if p.get("year", 0) >= 2023]
        if recent_papers:
            report += f"1. 近期热点（{len(recent_papers)}篇2023年后文献）：关注该领域最新进展\n"
        old_high_cit = [p for p in top_papers if p.get("year", 0) < 2020 and p.get("citations", 0) > 100]
        if old_high_cit:
            report += f"2. 经典基础（{len(old_high_cit)}篇高引经典）：建议深入阅读这些奠基性工作\n"
        low_cit_recent = [p for p in top_papers if p.get("year", 0) >= 2022 and p.get("citations", 0) < 10]
        if low_cit_recent:
            report += f"3. 新兴方向（{len(low_cit_recent)}篇低引新文）：可能代表尚未被广泛关注的研究前沿\n"
        report += "4. 交叉领域：结合本主题与其他学科（如AI/材料/工程）的交叉研究\n"
        report += "5. 方法论改进：现有方法的局限性可作为改进方向\n\n"

        # ── PRISMA 筛选流程 ──
        report += "── 五、PRISMA 筛选流程 ──\n\n"
        report += f"检索总量：{len(all_papers)} 篇\n"
        report += f"去重后：{len(unique_papers)} 篇（去除 {len(all_papers) - len(unique_papers)} 篇重复）\n"
        report += f"纳入分析：{len(top_papers)} 篇（按引用数筛选）\n"
        report += f"排除：{len(unique_papers) - len(top_papers)} 篇（引用数较低）\n\n"

        report += "── 注意事项 ──\n"
        report += "1. 本分析基于自动检索结果，不含人工筛选和质量评估\n"
        report += "2. 建议在此基础上人工精读 top 3-5 篇高引文献\n"
        report += "3. 如需正式发表，请补充 Web of Science / Scopus 检索\n"
        report += "4. 引用文献时务必使用 citation_check 校验真实性\n"

        return report

    except Exception as e:
        return f"文献综述分析错误：{e}"


def render_formula(latex: str, style: str = "unicode") -> str:
    r"""渲染 LaTeX 公式为终端可显示的 Unicode 文本

    参数：
    - latex: LaTeX 公式字符串，如 "E=mc^2" 或 "\\sum_{i=1}^{n} x_i^2"
    - style: 渲染样式
      - "unicode"：纯 Unicode 数学符号（默认，终端显示）
      - "raw"：返回原始 LaTeX（用于文档生成）
      - "latex"：用 $$ 包裹（用于 Markdown 渲染）

    返回：渲染后的公式字符串

    用途：学术研究、数学公式展示、物理方程推导

    迁移来源：tui_agent.py 行 4945-4972
    """
    latex = latex.strip()
    # 去除外层 $ 或 $$
    if latex.startswith("$$") and latex.endswith("$$"):
        latex = latex[2:-2].strip()
    elif latex.startswith("$") and latex.endswith("$"):
        latex = latex[1:-1].strip()

    if style == "raw":
        return latex
    elif style == "latex":
        return f"$${latex}$$"
    else:  # unicode
        rendered = _latex_to_unicode(latex)
        return rendered
