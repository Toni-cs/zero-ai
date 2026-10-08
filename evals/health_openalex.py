# -*- coding: utf-8 -*-
"""OpenAlex 健康检查：分辨「服务端不可用」与「本环境网络抖动」。

验收里 A 组四条全部降级到 Crossref，需要判定 OpenAlex 当前是否可达。
直接打最小请求（per_page=1），逐次打印状态码与延迟。
"""
import statistics
import time
import urllib.error
import urllib.request

URL = ("https://api.openalex.org/works?"
       "search=test&per_page=1&mailto=zeroai@example.com")
N = 8

codes, lats = [], []
lines = ["# OpenAlex 健康检查（%d 次）" % N, "",
         "| # | HTTP | 延迟 | 说明 |", "|---|---|---|---|"]

for i in range(N):
    req = urllib.request.Request(URL, headers={
        "User-Agent": "ZeroAI/1.1 (health-check)",
        "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code, note = r.status, ""
    except urllib.error.HTTPError as e:
        code = e.code
        note = (e.read()[:120] if e.fp else b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        code, note = -1, "%s: %s" % (type(e).__name__, e)
    ms = (time.time() - t0) * 1000
    codes.append(code)
    lats.append(ms)
    lines.append("| %d | %s | %.0fms | %s |" % (i + 1, code, ms, note[:80]))
    time.sleep(0.3)

lines += ["",
          "- 状态码分布: `%s`" % sorted(set(codes)),
          "- 成功(200): **%d/%d**" % (sum(1 for c in codes if c == 200), N),
          "- 429: **%d**" % sum(1 for c in codes if c == 429),
          "- 网络失败(-1): %d" % sum(1 for c in codes if c == -1),
          "- 延迟 p50: %.0fms   max: %.0fms"
          % (statistics.median(lats), max(lats)),
          "",
          "## 对照：Crossref 同样打法",
          ""]

CURL = ("https://api.crossref.org/works?"
        "query.title=test&rows=1&mailto=zeroai@example.com")
ccodes, clats = [], []
for i in range(4):
    req = urllib.request.Request(CURL, headers={
        "User-Agent": "ZeroAI/1.1 (health-check)",
        "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:  # noqa: BLE001
        code = -1
    ccodes.append(code)
    clats.append((time.time() - t0) * 1000)
    time.sleep(0.3)

lines += ["- 状态码分布: `%s`" % sorted(set(ccodes)),
          "- 成功(200): %d/4" % sum(1 for c in ccodes if c == 200),
          "- 延迟 p50: %.0fms" % statistics.median(clats)]

open("evals/results/openalex_health.txt", "w", encoding="utf-8").write(
    "\n".join(lines) + "\n")
print("OpenAlex codes=%s  Crossref codes=%s"
      % (sorted(set(codes)), sorted(set(ccodes))))
