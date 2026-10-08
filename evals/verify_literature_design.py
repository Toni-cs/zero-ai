# -*- coding: utf-8 -*-
"""实现前最后一轮验证：把 academic.py 的设计决策逐条钉死。

D1  filter=default.search + sort=cited_by_count:desc 组合是否既保相关性
    又按引用排序。若成立 => 服务端排序，一行搞定；否则 => 取相关性多条
    后客户端重排。
D2  per_page 上限与 select= 能否裁字段（影响 20 条结果的响应体积/延迟）。
D3  abstract_inverted_index 能否正确还原成摘要原文。
D4  年份过滤 filter=from_publication_date 是否与 search 共存。
D5  OpenAlex 失败率下的兜底：Crossref query.title 与 OpenAlex 混合重试
    的实际成功率（各 6 次）。
D6  citation_check 的标题匹配：OpenAlex search 的首条是否足以做
    SequenceMatcher 相似度判定（用 3 个已知标题 + 1 个伪造标题测）。

输出 evals/results/lit_api_design.md
"""
import io
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "ZeroAI/1.0 (Academic Research; mailto:zeroai@example.com)"
H = {"User-Agent": UA, "Accept": "application/json"}
Q = "attention is all you need transformer"
OUT = "evals/results/lit_api_design.md"

# 已知真实论文标题（含 arXiv 上的原版 "Attention Is All You Need"）
REAL_TITLES = [
    "Attention Is All You Need",
    "Deep learning",
    "Highly accurate protein structure prediction with AlphaFold",
]
FAKE_TITLE = "A Complete Theory of Zero-Shot Quantum Consciousness Routing"


def get(url, timeout=15):
    req = urllib.request.Request(url, headers=H)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, code = (e.read() if e.fp else b""), e.code
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode(), (time.time() - t0) * 1000
    return code, body, (time.time() - t0) * 1000


def jget(url, timeout=15):
    code, body, ms = get(url, timeout)
    try:
        return code, json.loads(body.decode("utf-8", "ignore")), ms
    except Exception:  # noqa: BLE001
        return code, None, ms


def oa(params):
    p = dict(params)
    p.setdefault("mailto", "zeroai@example.com")
    return jget("https://api.openalex.org/works?" + urllib.parse.urlencode(p))


def brief(rs, n=6):
    return [(w.get("cited_by_count"), (w.get("title") or "")[:64])
            for w in (rs or [])[:n]]


L = []


def p(s=""):
    L.append(str(s))


p("# academic.py 迁移设计验证")
p()
p("为从 Semantic Scholar（实测 5/5 次 429）迁到 OpenAlex + Crossref")
p("提供逐条实测依据。所有数字均为本轮实跑。")
p()

# ---- D1 ----
p("## D1  相关性 + 引用排序能否同时成立")
p()
p("### 组合 A：filter=default.search + sort=cited_by_count:desc")
u = {"filter": "default.search:" + Q, "sort": "cited_by_count:desc",
     "per_page": "5"}
code, d, ms = oa(u)
rs = (d or {}).get("results")
p("HTTP %s  %.0fms  n=%s" % (code, ms, len(rs) if rs is not None else "err"))
if rs is None and d:
    p("body: %s" % json.dumps(d, ensure_ascii=False)[:400])
else:
    for c, t in brief(rs):
        p("  - [%s] %s" % (c, t))
p()

p("### 组合 B：search=... + sort=cited_by_count:desc（首轮已测，仅记结果）")
p("  首条 = Exploiting Generative AI... [78456]  —— 相关性丢失")
p()

p("### 组合 C：search=... 默认排序（基准）")
code, d, ms = oa({"search": Q, "per_page": "5"})
rs = (d or {}).get("results")
p("HTTP %s  %.0fms  n=%s" % (code, ms, len(rs) if rs is not None else "err"))
for c, t in brief(rs):
    p("  - [%s] %s" % (c, t))
p()

p("**判定**：看组合 A 首条是否为 attention/transformer 相关论文。")
p()

# ---- D2 ----
p("## D2  per_page 上限与 select 裁字段")
p()
for n in (20, 50, 100, 200):
    code, d, ms = oa({"search": Q, "per_page": str(n),
                      "select": "id,title,cited_by_count"})
    rs = (d or {}).get("results")
    meta = (d or {}).get("meta")
    p("  per_page=%-4d HTTP=%s n_returned=%-4s per_page_meta=%s %.0fms"
      % (n, code, len(rs) if rs is not None else "err",
         (meta or {}).get("per_page") if meta else "?", ms))
    if rs is None and d:
        p("     body: %s" % json.dumps(d, ensure_ascii=False)[:300])
p()

p("### select 是否被接受（不带 select 的字段数 vs 带 select 的字段数）")
code, d, ms = oa({"search": Q, "per_page": "1"})
full_n = len(((d or {}).get("results") or [{}])[0]) if d else -1
code, d, ms = oa({"search": Q, "per_page": "1",
                  "select": "id,title,cited_by_count,doi"})
sel_n = len(((d or {}).get("results") or [{}])[0]) if d else -1
p("  不带 select 字段数 = %s   带 select 字段数 = %s" % (full_n, sel_n))
p("  （select 生效 => %s）" % (sel_n >= 0 and sel_n < full_n))
p()

# ---- D3 ----
p("## D3  abstract_inverted_index 还原摘要")
p()
code, d, ms = oa({"search": "attention is all you need", "per_page": "1"})
w = (((d or {}).get("results") or [None])[0]) or {}
inv = w.get("abstract_inverted_index")
p("title = %s" % (w.get("title") or "")[:80])
p("inverted_index 是 %s，token 数 = %s"
  % (type(inv).__name__, len(inv) if isinstance(inv, dict) else None))
if isinstance(inv, dict):
    # 还原
    pos = {}
    for word, ps in inv.items():
        for x in ps:
            pos[x] = word
    text = " ".join(pos[i] for i in sorted(pos))
    p("还原后长度 = %d" % len(text))
    p("还原结果前 300 字：")
    p("  " + text[:300])
    p()
    p("词序检查（应为连贯英文，不应出现同词重复跳序）：")
    words = text.split()
    p("  前 15 词 = %s" % words[:15])
else:
    p("无摘要（abstract_inverted_index 为 None）")
p()

# ---- D4 ----
p("## D4  年份过滤与 search 共存")
p()
combos = [
    ("search + 年份 filter", {"search": Q, "per_page": "5",
                              "filter": "from_publication_date:2017-01-01,"
                                        "to_publication_date:2018-12-31"}),
    ("search + 年份 + 引用排序", {"search": Q, "per_page": "5",
                                  "filter": "from_publication_date:2017-01-01,"
                                            "to_publication_date:2018-12-31",
                                  "sort": "cited_by_count:desc"}),
]
for label, params in combos:
    code, d, ms = oa(params)
    rs = (d or {}).get("results")
    p("### %s  HTTP %s  n=%s" % (label, code,
                                 len(rs) if rs is not None else "err"))
    if rs is None and d:
        p("  body: %s" % json.dumps(d, ensure_ascii=False)[:300])
    else:
        for w2 in (rs or [])[:5]:
            p("  - year=%s [%s] %s" % (w2.get("publication_year"),
                                       w2.get("cited_by_count"),
                                       (w2.get("title") or "")[:56]))
    p()

# ---- D5 ----
p("## D5  兜底成功率（各 6 次，带 15s 超时）")
p()
for label, fn in [
    ("OpenAlex search", lambda i: oa({"search": "deep learning %d" % i,
                                      "per_page": "1"})),
    ("Crossref query.title", lambda i: jget(
        "https://api.crossref.org/works?"
        + urllib.parse.urlencode({"query.title": "deep learning %d" % i,
                                  "rows": "1",
                                  "mailto": "zeroai@example.com"}))),
    ("Crossref /works/{doi} 已知", lambda i: jget(
        "https://api.crossref.org/works/10.1038/nature14539")),
]:
    codes, lats = [], []
    for i in range(6):
        c, d, ms = fn(i)
        codes.append(c)
        lats.append(ms)
        time.sleep(0.1)
    p("### %s" % label)
    p("  状态码 = %s" % codes)
    p("  成功(200) = %d/6   429 = %d   失败(-1) = %d"
      % (sum(1 for c in codes if c == 200),
         sum(1 for c in codes if c == 429),
         sum(1 for c in codes if c == -1)))
    p("  延迟 p50=%.0fms  max=%.0fms" % (statistics.median(lats), max(lats)))
    p()

# ---- D6 ----
p("## D6  标题匹配能否支撑 citation_check")
p()
for title in REAL_TITLES + [FAKE_TITLE]:
    code, d, ms = oa({"search": title, "per_page": "3"})
    rs = (d or {}).get("results") or []
    from difflib import SequenceMatcher
    p("### 查询: %s" % title)
    p("  HTTP %s  n=%d" % (code, len(rs)))
    if rs:
        best = rs[0]
        got = best.get("title") or ""
        ratio = SequenceMatcher(None, title.lower().strip(),
                                got.lower().strip()).ratio()
        p("  首条: %s" % got[:80])
        p("  SequenceMatcher 相似度 = %.4f" % ratio)
        p("  -> %s" % ("验证通过" if ratio >= 0.80 else
                       "匹配不足/查无此文"))
    else:
        p("  **无结果 => 判定为虚构文献**")
    p()

io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK ->", OUT)
