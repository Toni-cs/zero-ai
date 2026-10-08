# -*- coding: utf-8 -*-
"""复测探针里的三个异常，不搞清楚不许改 academic.py。

Q1  OpenAlex 带 sort=cited_by_count:desc 时，search 条件还生效吗？
    首轮返回了香农《A Mathematical Theory of Communication》，
    与 "attention is all you need" 无关，怀疑 sort 让搜索失效。

Q2  压测 20 次只花 72.4ms（3.6ms/次），而单次实测 1324ms，
    物理上不可能。逐次打印真实延迟与状态码定位。

Q3  Crossref 关键词搜索返回 500，是持续故障还是偶发？
    换 query / query.bibliographic / rows 三种参数各试 5 次。
    未知名 DOI 应为 404，首轮却拿到 500 + Java 栈。

输出 evals/results/lit_api_recheck.md
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
KNOWN_DOI = "10.1038/nature14539"
FAKE_DOI = "10.9999/zeroai.not-a-real-doi-2026"
OUT = "evals/results/lit_api_recheck.md"


def get(url, timeout=25):
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


def jget(url, timeout=25):
    code, body, ms = get(url, timeout)
    try:
        return code, json.loads(body.decode("utf-8", "ignore")), ms
    except Exception:  # noqa: BLE001
        return code, None, ms


def titles(results, n=5):
    out = []
    for w in (results or [])[:n]:
        t = w.get("title") or w.get("display_name") or ""
        out.append((w.get("cited_by_count"), t[:70]))
    return out


L = []


def p(s=""):
    L.append(str(s))


# ---------------- Q1 ----------------
p("# 文献 API 复测（针对首轮探针的 3 个异常）")
p()
p("## Q1  OpenAlex 排序参数是否让 search 失效")
p()
cases = [
    ("无 sort（默认）", ""),
    ("sort=cited_by_count:desc", "cited_by_count:desc"),
    ("sort=relevance_score:desc", "relevance_score:desc"),
    ("filter 换成 default.search", "__DFS__"),
]
for label, sort in cases:
    if sort == "__DFS__":
        url = ("https://api.openalex.org/works?"
               + urllib.parse.urlencode({
                   "filter": "default.search:" + Q,
                   "per_page": "5",
                   "mailto": "zeroai@example.com"}))
    else:
        p_ = {"search": Q, "per_page": "5", "mailto": "zeroai@example.com"}
        if sort:
            p_["sort"] = sort
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(p_)
    code, d, ms = jget(url)
    rs = (d or {}).get("results") if d else None
    p("### %s   HTTP %s  %.0fms  n=%s"
      % (label, code, ms, len(rs) if rs is not None else "err"))
    if rs is None and d:
        p("  body: %s" % json.dumps(d, ensure_ascii=False)[:300])
    else:
        for c, t in titles(rs):
            p("  - [%s] %s" % (c, t))
    p()

p("### Q1 判据")
p("若「无 sort」和「relevance_score」都命中 transformer 相关论文，")
p("而「cited_by_count」跑出香农/AlphaFold，则该 sort 会绕过搜索条件，")
p("不能用它做 citations 排序，必须改用别的方式（如 filter 或客户端重排）。")
p()

# ---------------- Q2 ----------------
p("## Q2  压测 20 次的真实延迟（逐次打印）")
p()
p("首轮报「20 次 72.4ms」，与单次 1324ms 矛盾，逐次核对。")
p()
for label, base in [
    ("OpenAlex search", "https://api.openalex.org/works?"),
]:
    lat, codes = [], []
    p("### " + label)
    for i in range(20):
        if label.startswith("OpenAlex"):
            u = base + urllib.parse.urlencode(
                {"search": "deep learning paper %d" % i,
                 "per_page": "1", "mailto": "zeroai@example.com"})
        code, d, ms = jget(u)
        lat.append(ms)
        codes.append(code)
        p("  #%02d HTTP=%-4s %8.1fms" % (i + 1, code, ms))
    p("  distinct codes = %s" % sorted(set(codes)))
    p("  n_429 = %d" % sum(1 for c in codes if c == 429))
    p("  latency p50=%.0fms  p95=%.0fms  mean=%.0fms  sum=%.0fms"
      % (statistics.median(lat),
         sorted(lat)[max(0, int(round(.95 * (len(lat) - 1))))],
         statistics.mean(lat), sum(lat)))
    p()

# ---------------- Q3 ----------------
p("## Q3  Crossref 关键词搜索 500：偶发还是持续")
p()
variants = [
    ("query.bibliographic", {"query.bibliographic": Q, "rows": "3",
                             "mailto": "zeroai@example.com"}),
    ("query (通用)", {"query": Q, "rows": "3",
                      "mailto": "zeroai@example.com"}),
    ("query.title", {"query.title": "Attention is all you need",
                     "rows": "3", "mailto": "zeroai@example.com"}),
]
for label, params in variants:
    p("### " + label)
    stats = {}
    for i in range(5):
        u = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        code, d, ms = jget(u)
        stats[code] = stats.get(code, 0) + 1
        if i == 0:
            if d and isinstance(d, dict) and "message" in d:
                m = d["message"]
                if isinstance(m, dict) and "items" in m:
                    p("  first: total=%s n=%s" % (
                        m.get("total-results"), len(m.get("items") or [])))
                    for it in (m.get("items") or [])[:3]:
                        p("    - [%s] %s" % (
                            it.get("is-referenced-by-count"),
                            ((it.get("title") or [""])[0])[:70]))
                else:
                    p("  first body: %s"
                      % json.dumps(m, ensure_ascii=False)[:200])
            else:
                p("  first: non-json / %s" % (d,))
        time.sleep(0.2)
    p("  5 次状态码分布 = %s" % stats)
    p()

p("### Q3-2  Crossref 未知名 DOI 应得 404 吗")
for i in range(5):
    u = "https://api.crossref.org/works/" + urllib.parse.quote(FAKE_DOI)
    code, d, ms = jget(u)
    msg = (d.get("message") if isinstance(d, dict) else None)
    if isinstance(msg, dict):
        msg = {k: msg[k] for k in list(msg)[:4]}
    p("  #%d HTTP=%s  %s" % (i + 1, code, str(msg)[:160]))
    time.sleep(0.2)
p()

p("### Q3-2b  对照：OpenAlex 未知名 DOI")
for i in range(3):
    u = ("https://api.openalex.org/works/https://doi.org/"
         + urllib.parse.quote(FAKE_DOI))
    code, d, ms = jget(u)
    p("  #%d HTTP=%s  %s" % (
        i + 1, code,
        json.dumps(d, ensure_ascii=False)[:160] if d else None))
    time.sleep(0.2)
p()

# ---------------- Q4 附带：S2 现状再确认 ----------------
p("## 附：Semantic Scholar 现状（连续 5 次）")
codes = []
for i in range(5):
    u = ("https://api.semanticscholar.org/graph/v1/paper/search?"
         "query=attention&limit=1&fields=title")
    code, d, ms = jget(u)
    codes.append(code)
    if i == 0:
        p("  #1 HTTP=%s %s" % (code, json.dumps(d, ensure_ascii=False)[:180]
                               if d else None))
    time.sleep(0.3)
p("  5 次状态码 = %s" % codes)
p()

io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("OK ->", OUT)
