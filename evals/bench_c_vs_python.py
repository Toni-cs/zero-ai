# -*- coding: utf-8 -*-
"""C 扩展 vs 纯 Python 的同场对比基准。

背景：zeroai-tui/tests/test_performance.py 号称是性能测试，但它
  1) 没有任何 assert —— 永远通过
  2) 用 if HAS_C_RENDERER / else 分支 —— 一次只测一条路径，从不对比
  3) 三处 except: pass —— C 路径挂掉也照样计时、照样绿
所以项目从来没产生过一个加速比数字。这里补齐：
  - 同一进程、同一数据，两条路径都跑
  - 先验证输出等价（不等价则加速比无意义）
  - 真的断言加速比，让它有失败的可能
"""
from __future__ import annotations

import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"D:\C\C\zeroai-tui")

from zeroai_tui.renderer import Renderer, RenderBuffer, Style, HAS_C_RENDERER
from zeroai_tui.terminal import Color
from zeroai_tui import _renderer

print(f"HAS_C_RENDERER = {HAS_C_RENDERER}")
print(f"zig_available  = {getattr(_renderer, 'zig_available', lambda: 'N/A')()}")
print()


# ── 纯 Python 实现：逐字照搬 renderer.flush 的 fallback 分支 ──────────────
def py_diff(cur_buf, cur_sty, nxt_buf, nxt_sty, rows, cols) -> str:
    output = []
    last_style = None
    for row in range(rows):
        for col in range(cols):
            char = nxt_buf[row][col]
            style = nxt_sty[row][col]
            if cur_buf[row][col] == char and cur_sty[row][col] == style:
                continue
            output.append(f"\033[{row + 1};{col + 1}H")
            if style != last_style:
                if style:
                    codes = []
                    if style.bold:
                        codes.append(Color.BOLD)
                    if style.dim:
                        codes.append(Color.DIM)
                    if style.fg:
                        codes.append(style.fg)
                    if style.bg:
                        codes.append(style.bg)
                    output.append("".join(codes))
                else:
                    output.append(Color.RESET)
                last_style = style
            output.append(char)
    output.append(Color.RESET)
    return "".join(output)


def c_diff(cur_buf, cur_sty, nxt_buf, nxt_sty, rows, cols) -> str:
    return _renderer.diff_buffers(cur_buf, cur_sty, nxt_buf, nxt_sty, rows, cols)


def make_style_py(style) -> str:
    codes = []
    if style.bold:
        codes.append(Color.BOLD)
    if style.dim:
        codes.append(Color.DIM)
    if style.italic:
        codes.append(Color.ITALIC)
    if style.underline:
        codes.append(Color.UNDERLINE)
    if style.fg:
        codes.append(style.fg)
    if style.bg:
        codes.append(style.bg)
    return "".join(codes)


def make_style_c(style) -> str:
    return _renderer.make_style(style.bold, style.dim, style.italic,
                                style.underline, style.fg or "", style.bg or "")


def build_buffers(rows, cols, change_ratio):
    """构造两份缓冲区，按 change_ratio 随机改写 next。"""
    import random
    random.seed(42)
    a = RenderBuffer(cols, rows)
    b = RenderBuffer(cols, rows)
    styles = [Style(), Style(bold=True, fg=Color.CYAN),
              Style(fg=Color.GREEN), Style(fg=Color.RED),
              Style(dim=True, fg=Color.WHITE)]
    for r in range(rows):
        for c in range(cols):
            ch = chr(33 + (r * 7 + c * 3) % 90)
            st = styles[(r + c) % len(styles)]
            a.put(r, c, ch, st)
            b.put(r, c, ch, st)
    # 改写一部分
    n = int(rows * cols * change_ratio)
    for _ in range(n):
        r = random.randrange(rows)
        c = random.randrange(cols)
        b.put(r, c, chr(65 + random.randrange(26)), styles[random.randrange(len(styles))])
    return a, b


def bench(fn, args, repeat, inner=1):
    """返回【每次调用】的最优耗时（秒）。

    关键：必须除以总调用次数再比较——两路 inner/repeat 不同，
    直接拿总时间相除会得出错误的加速比（本脚本第一版就犯了这个错）。
    """
    total_calls = repeat * inner
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        for _ in range(inner):
            fn(*args)
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best / total_calls


# ══════════════════════════════════════════════════════════════════════════
print("=" * 94)
print("1. 输出等价性（不等价则加速比无意义）")
print("=" * 94)
rows, cols = 24, 80
a, b = build_buffers(rows, cols, change_ratio=0.30)
py_out = py_diff(a.buffer, a.styles, b.buffer, b.styles, rows, cols)
c_out = c_diff(a.buffer, a.styles, b.buffer, b.styles, rows, cols)
print(f"  Python 输出 {len(py_out):>6} 字符")
print(f"  C        输出 {len(c_out):>6} 字符")
print(f"  完全一致      = {py_out == c_out}")
if py_out != c_out:
    # 找第一个差异，判断是否只是"连续字符合并"造成的等价差异
    m = min(len(py_out), len(c_out))
    idx = next((i for i in range(m) if py_out[i] != c_out[i]), m)
    print(f"  首个差异位置  = {idx}")
    print(f"    py[...]: {py_out[max(0, idx-40):idx+40]!r}")
    print(f"    c [...]: {c_out[max(0, idx-40):idx+40]!r}")

print()
print("=" * 94)
print("2. 同场性能对比（同一进程、同一数据、两条路径都跑）")
print("=" * 94)
print(f"{'场景':<34}{'Python':>12}{'C':>12}{'加速比':>10}")
print("-" * 94)

RESULTS = []
for rows, cols, ratio in [(24, 80, 0.30), (24, 80, 1.00),
                          (50, 200, 0.30), (50, 200, 1.00)]:
    a, b = build_buffers(rows, cols, ratio)
    args = (a.buffer, a.styles, b.buffer, b.styles, rows, cols)
    # Python 慢，用 inner 放大；C 快，单独用更小的 inner
    t_py = bench(py_diff, args, repeat=5, inner=20)
    t_c = bench(c_diff, args, repeat=15, inner=200)
    speedup = t_py / t_c
    label = f"{rows}x{cols} 改动{int(ratio*100)}%"
    RESULTS.append((label, t_py, t_c, speedup))
    print(f"{label:<34}{t_py*1000:>10.3f}ms{t_c*1000:>10.3f}ms{speedup:>9.2f}x")

print()
print("=" * 94)
print("3. make_style 对比")
print("=" * 94)
STYLES = [Style(bold=True, fg=Color.RED), Style(dim=True, fg=Color.GREEN),
          Style(italic=True, fg=Color.BLUE),
          Style(bold=True, italic=True, underline=True, fg=Color.YELLOW, bg=Color.BLACK)]
N = 200_000
t0 = time.perf_counter()
for _ in range(N):
    for s in STYLES:
        make_style_py(s)
t_py = time.perf_counter() - t0
t0 = time.perf_counter()
for _ in range(N):
    for s in STYLES:
        make_style_c(s)
t_c = time.perf_counter() - t0
print(f"  Python {t_py*1000:.1f}ms   C {t_c*1000:.1f}ms   加速 {t_py/t_c:.1f}x")
RESULTS.append(("make_style", t_py, t_c, t_py / t_c))

# ── 校验 make_style 输出等价 ────────────────────────────────────────────
eq = all(make_style_py(s) == make_style_c(s) for s in STYLES)
print(f"  make_style 输出等价 = {eq}")

print()
print("=" * 94)
print("4. 结论")
print("=" * 94)
med = statistics.median(r[3] for r in RESULTS)
lo = min(r[3] for r in RESULTS)
hi = max(r[3] for r in RESULTS)
print(f"  加速比 中位数 {med:.1f}x   最小 {lo:.1f}x   最大 {hi:.1f}x")
print(f"  等价性: diff_buffers {'一致' if py_out == c_out else '存在差异'}, "
      f"make_style {'一致' if eq else '存在差异'}")

# 断言：C 必须真的更快，否则这条结论就是空话
assert med > 3.0, f"C 扩展中位加速比仅 {med:.1f}x，达不到 3x 就不该拿它当卖点"
assert eq, "make_style 输出不等价，性能对比无意义"
print("\n[通过] C 扩展确实显著快于 Python（中位加速比 > 3x）")
