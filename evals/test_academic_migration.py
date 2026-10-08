# -*- coding: utf-8 -*-
"""academic.py 迁移后的真实验收（会实际请求 OpenAlex / Crossref / arXiv）。

覆盖四类：
  A. 正常检索 —— academic_search 的相关性 / 引用排序 / 年份筛选
  B. 引用校验 —— 真 DOI、假 DOI、真标题、假标题、真 arXiv、假 arXiv
  C. 关键正确性 —— 网络故障**不得**被输出成「文献不存在」
                   （该分支用 monkeypatch 离线触发，不依赖真的断网）
  D. 429 验收 —— 连打 12 次，统计输出里是否出现 429 / 限流字样

输出 evals/results/academic_migration.md
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.tools import academic as A  # noqa: E402

OUT = os.path.join("evals", "results", "academic_migration.md")
L = []


def p(s=""):
    L.append(str(s))


def first_lines(s, n=6):
    return "\n".join((s or "").splitlines()[:n])


# ═══════════════════ A. 检索 ═══════════════════
p("# academic.py 迁移验收（实调 OpenAlex / Crossref / arXiv）")
p()
p("## A. academic_search")
p()

cases = [
    ("默认相关性", dict(query="attention is all you need transformer",
                        num_results=5)),
    ("按引用数排序", dict(query="attention is all you need transformer",
                         num_results=5, sort_by="citations")),
    ("年份筛选 2017-2018", dict(query="attention is all you need",
                               num_results=5, year_from=2017, year_to=2018)),
    ("中文查询", dict(query="Transformer 注意力机制 综述", num_results=3)),
]
search_ok = 0
for label, kw in cases:
    t0 = time.time()
    out = A.academic_search(**kw)
    dt = time.time() - t0
    n_results = len(re.findall(r"^\[\d+\] ", out, re.M))
    src = (re.search(r"数据源: (\S+)", out) or [None, "?"])[1] \
        if isinstance(re.search(r"数据源: (\S+)", out), re.Match) else "?"
    m = re.search(r"数据源: ([^）\n]+)", out)
    src = m.group(1).strip() if m else "?"
    has_429 = any(t in out for t in (
        "过于频繁", "Too Many Requests", "限流",
        "Semantic Scholar 限制", "HTTP 429"))
    # 注意：不能用裸子串 "429" 判限流 —— 输出正文里的引用数、DOI
    # （如 引用: 1429、10.1038/s42915-...）都会命中，制造假阳性。
    # A 组「年份筛选」曾因此被误判 FAIL。
    ok = ("学术搜索失败" not in out) and n_results > 0 and not has_429
    search_ok += ok
    p("### %s  -> %s  (%.1fs, %d 条, 数据源=%s)" % (label, "PASS" if ok else "FAIL",
                                                    dt, n_results, src))
    p("```")
    p(first_lines(out, 4))
    p("```")
    p()

p("**A 小结：%d/%d 通过**" % (search_ok, len(cases)))
p()

# ═══════════════════ B. 引用校验 ═══════════════════
p("## B. citation_check")
p()
# expect 可以是字符串或字符串元组（任一命中即通过）；
# 第 5 项是「绝不能出现」的断言，防止用错误理由蒙对。
cases = [
    ("真 DOI（Nature 深度学习）", dict(doi="10.1038/nature14539"),
     "验证通过", "应命中", ("文献不存在",)),
    ("假 DOI", dict(doi="10.9999/zeroai.not-a-real-doi-2026"),
     ("文献不存在", "无法确认"),
     "双源都明确 not_found 才能说「不存在」；若主源正在限流，"
     "只能如实说「无法确认」——两种都是正确行为，但都绝不能说「验证通过」",
     ("验证通过",)),
    ("真标题", dict(title="Attention Is All You Need"),
     "验证通过", "相似度应 >= 95%", ("虚构",)),
    ("假标题（虚构文献）",
     dict(title="A Complete Theory of Zero-Shot Quantum Consciousness Routing"),
     "虚构", "相似度过低 => 明确说很可能虚构", ()),
    ("真 arXiv ID", dict(arxiv_id="1706.03762"),
     "验证通过", "arXiv 官方 API", ("文献不存在",)),
    ("假 arXiv ID", dict(arxiv_id="9999.99999"),
     "文献不存在", "arXiv 无此条目", ()),
    ("无参数", dict(), "请提供文献标题", "参数校验", ()),
]
cit_ok = 0
for label, kw, expect, why, *rest in cases:
    forbid = rest[0] if rest else ()
    expects = expect if isinstance(expect, tuple) else (expect,)
    t0 = time.time()
    out = A.citation_check(**kw)
    dt = time.time() - t0
    hit = next((e for e in expects if e in out), None)
    leaked = [f for f in forbid if f in out]
    ok = hit is not None and not leaked
    cit_ok += ok
    p("### %s  -> %s  (%.1fs)" % (label, "PASS" if ok else "FAIL", dt))
    p("- 期望命中其一：%s" % " / ".join("「%s」" % e for e in expects))
    p("- 实际命中：%s" % ("「%s」" % hit if hit else "**无**"))
    if forbid:
        p("- 禁止出现：%s -> %s"
          % (" / ".join("「%s」" % f for f in forbid),
             "命中 ❌ %s" % leaked if leaked else "未命中 ✓"))
    p("依据：%s" % why)
    p("```")
    p(first_lines(out, 7))
    p("```")
    p()

p("**B 小结：%d/%d 通过**" % (cit_ok, len(cases)))
p()

# ═══════════════ C. 关键正确性：网络故障 ≠ 文献不存在 ═══════════════
p("## C. 关键正确性：网络故障不得被输出成「文献不存在」")
p()
p("这是本次迁移最不能出错的地方 —— 把工具故障说成「用户编造引用」，")
p("代价远高于多报一次网络错误。用 monkeypatch 让两个源都返回网络失败：")
p()

real_lit_get = A._lit_get


def _always_network_fail(url, timeout=10):
    return -1, b""


A._lit_get = _always_network_fail
try:
    c_ok = 0
    for label, kw, must_not, must_have in [
        ("DOI + 双源网络故障", dict(doi="10.1038/nature14539"),
         "文献不存在", "无法确认"),
        ("标题 + 双源网络故障", dict(title="Attention Is All You Need"),
         "虚构", "未完成"),
        ("arXiv + 网络故障", dict(arxiv_id="1706.03762"),
         "文献不存在", "未完成"),
    ]:
        out = A.citation_check(**kw)
        leaked = must_not in out
        got = must_have in out
        ok = (not leaked) and got
        c_ok += ok
        p("### %s -> %s" % (label, "PASS" if ok else "FAIL"))
        p("- 错误地出现「%s」：%s" % (must_not, "是 ❌" if leaked else "否 ✓"))
        p("- 正确地出现「%s」：%s" % (must_have, "是 ✓" if got else "否 ❌"))
        p("```")
        p(first_lines(out, 4))
        p("```")
        p()
    p("**C 小结：%d/3 通过**" % c_ok)
finally:
    A._lit_get = real_lit_get
p()

# ═══════════════════ D. 429 验收 ═══════════════════
p("## D. 429 验收（连打 12 次 academic_search）")
p()
codes = []
lat = []
n_429 = 0
n_fail = 0
src_dist = {}
t_all = time.time()
for i in range(12):
    t0 = time.time()
    out = A.academic_search("deep learning transformer %d" % i, num_results=3)
    lat.append(time.time() - t0)
    if any(t in out for t in ("过于频繁", "Too Many Requests", "限流",
                              "Semantic Scholar 限制", "HTTP 429")):
        n_429 += 1
    if "学术搜索失败" in out or "学术搜索错误" in out:
        n_fail += 1
    m = re.search(r"数据源: ([^）\n]+)", out)
    src = (m.group(1).strip() if m else "未知")
    src_dist[src] = src_dist.get(src, 0) + 1
    time.sleep(0.15)

import statistics
p("| 指标 | 实测 |")
p("|---|---|")
p("| 请求次数 | 12 |")
p("| **用户可见的 429/限流提示** | **%d** |" % n_429)
p("| 完全失败次数 | %d |" % n_fail)
p("| 实际使用的数据源 | %s |"
  % "，".join("%s×%d" % kv for kv in sorted(src_dist.items())))
p("| 延迟 p50 | %.2fs |" % statistics.median(lat))
p("| 延迟 max | %.2fs |" % max(lat))
p("| 总耗时 | %.1fs |" % (time.time() - t_all))
p()
p("**对照（改前实测）**：Semantic Scholar 连打 5 次 = 5/5 次 429，且**无任何兜底源**，")
p("用户拿到的直接就是失败。")
p()
p("> 注意：本组的 429 计数是**用户可见的症状**（输出里是否出现限流提示），")
p("> 不是源端状态。本轮执行期间 OpenAlex 正处于日配额耗尽状态")
p("> （`Retry-After: 49335`，见 openalex_429_character.txt），")
p("> 所以数据源分布会明显偏向 Crossref —— 这正是兜底设计要覆盖的场景。")
p()

# ═══════════════════ E. literature_review ═══════════════════
p("## E. literature_review")
p()
t0 = time.time()
out = A.literature_review("transformer attention mechanism", num_papers=5)
dt = time.time() - t0
ok = "文献综述分析报告" in out and "未找到相关文献" not in out
p("### literature_review -> %s (%.1fs)" % ("PASS" if ok else "FAIL", dt))
p("```")
p(first_lines(out, 16))
p("```")
p()

# ═══════════════════ 总结 ═══════════════════
p("## 总结")
p()
p("| 组 | 结果 |")
p("|---|---|")
p("| A 检索 | %d/%d |" % (search_ok, len(cases) if False else 4))
p("| B 引用校验 | %d/7 |" % cit_ok)
p("| C 故障不误判 | %d/3 |" % c_ok)
p("| D 429 | 用户可见限流 %d 次（目标 0），数据源 %s |"
  % (n_429, "，".join("%s×%d" % kv for kv in sorted(src_dist.items()))))
p("| E 文献综述 | %s |" % ("PASS" if ok else "FAIL"))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK ->", OUT)
