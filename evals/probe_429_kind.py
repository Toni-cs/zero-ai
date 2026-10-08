# -*- coding: utf-8 -*-
"""区分 OpenAlex 429 是「突发限流」还是「配额耗尽」。

方法：用 5 秒间隔打 6 次（远低于任何合理的突发阈值）。
- 若全部恢复 200 -> 之前是突发限流，慢速可用，说明客户端必须退避
- 若仍 429      -> 配额已耗尽，短期内该源不可用，兜底源成为主力
- 若 429 且响应带 Retry-After -> 按服务端指示等待
"""
import time
import urllib.error
import urllib.request

URL = ("https://api.openalex.org/works?"
       "search=hello&per_page=1&mailto=zeroai@example.com")
N = 6
GAP = 5.0

lines = ["# OpenAlex 429 性质判定", "",
         "打法：%d 次，间隔 %.0f 秒" % (N, GAP), "",
         "| # | HTTP | 延迟 | Retry-After | body 片段 |", "|---|---|---|---|---|"]
codes = []

for i in range(N):
    req = urllib.request.Request(URL, headers={
        "User-Agent": "ZeroAI/1.1 (rate-limit probe)",
        "Accept": "application/json"})
    t0 = time.time()
    retry_after = ""
    body = ""
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            code = r.status
            retry_after = r.headers.get("Retry-After") or ""
            body = r.read()[:160].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = e.code
        retry_after = e.headers.get("Retry-After") or ""
        body = (e.read()[:200] if e.fp else b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        code, body = -1, "%s: %s" % (type(e).__name__, e)
    ms = (time.time() - t0) * 1000
    codes.append(code)
    lines.append("| %d | %s | %.0fms | %s | %s |"
                 % (i + 1, code, ms, retry_after or "-",
                    body.replace("\n", " ")[:70]))
    time.sleep(GAP)

lines += ["",
          "状态码分布: `%s`" % sorted(set(codes)),
          "成功(200): **%d/%d**" % (sum(1 for c in codes if c == 200), N),
          "429: %d" % sum(1 for c in codes if c == 429),
          "",
          "## 结论判读",
          "- 全 200 => 突发限流，5 秒退避即可恢复，客户端必须做退避重试",
          "- 仍 429 => 配额维度限制（很可能按日/按小时），短时无法恢复，",
          "  必须依靠 Crossref 兜底，且不能对用户声称「不限流」",
          "- 有 Retry-After => 服务端已给出明确等待时长，应遵从"]

open("evals/results/openalex_429_character.txt", "w",
     encoding="utf-8").write("\n".join(lines) + "\n")
print("codes=%s" % sorted(set(codes)))
