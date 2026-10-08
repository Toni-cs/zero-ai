# -*- coding: utf-8 -*-
"""探测 OpenAlex / Crossref / arXiv 三个公开文献 API 的真实响应形状与延迟。

目的：为 zeroai/tools/academic.py 从 Semantic Scholar（12/13 次 429）
迁移到 OpenAlex + Crossref 提供**实测依据**，不靠文档假设。

测项：
  1. OpenAlex 关键词搜索（含年份过滤、按引用排序）
  2. OpenAlex DOI 精确查询
  3. Crossref 关键词搜索
  4. Crossref DOI 精确查询（DOI 注册机构，权威）
  5. arXiv 官方 API 按 ID 查询
  6. 未知名 DOI 的 404 行为（引用校验依赖此判定"文献不存在"）
  7. 连续 20 次请求是否触发 429

输出：
    evals/results/lit_api_probe.json
    evals/results/lit_api_probe.md
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evals", "results")

UA = "ZeroAI/1.0 (Academic Research; mailto:zeroai@example.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# 已知真实文献，用于验证字段是否取得到
KNOWN_DOI = "10.1038/nature14539"      # Vaswani et al., Attention Is All You Need（被引极高）
FAKE_DOI = "10.9999/zeroai.not-a-real-doi-2026"
ARXIV_ID = "1706.03762"


def get(url, timeout=20):
    """返回 (status, bytes, elapsed_ms)。HTTPError 也归一化返回，不抛。"""
    req = urllib.request.Request(url, headers=HEADERS)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        code = e.code
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode(), (time.time() - t0) * 1000
    return code, body, (time.time() - t0) * 1000


def jget(url, timeout=20):
    code, body, ms = get(url, timeout)
    try:
        return code, json.loads(body.decode("utf-8", errors="ignore")), ms
    except Exception:  # noqa: BLE001
        return code, None, ms


def oa_search(q, n=3, year_from=0, year_to=0, sort=None):
    p = {"search": q, "per_page": str(n), "mailto": "zeroai@example.com"}
    if year_from or year_to:
        yf = year_from or 1900
        yt = year_to or 2099
        p["filter"] = f"from_publication_date:{yf}-01-01,to_publication_date:{yt}-12-31"
    if sort:
        p["sort"] = sort
    return jget("https://api.openalex.org/works?" + urllib.parse.urlencode(p))


def oa_doi(doi):
    return jget("https://api.openalex.org/works/https://doi.org/"
                + urllib.parse.quote(doi))


def cr_search(q, n=3):
    p = {"query.bibliographic": q, "rows": str(n),
         "mailto": "zeroai@example.com"}
    return jget("https://api.crossref.org/works?" + urllib.parse.urlencode(p))


def cr_doi(doi):
    return jget("https://api.crossref.org/works/" + urllib.parse.quote(doi))


def arxiv_by_id(aid):
    url = ("https://export.arxiv.org/api/query?id_list="
           + urllib.parse.quote(aid))
    return jget(url)


def summarize_openalex(d):
    """列出 OpenAlex work 的关键字段实测值"""
    if not isinstance(d, dict):
        return {"error": "non-json"}
    out = {}
    for k in ["id", "doi", "title", "publication_year", "cited_by_count",
              "type", "language"]:
        out[k] = d.get(k)
    auth = d.get("authorships") or []
    out["n_authorships"] = len(auth)
    out["first_author"] = ((auth[0].get("author") or {}).get("display_name")
                           if auth else None)
    inv = d.get("abstract_inverted_index")
    out["has_abstract_inverted_index"] = inv is not None
    out["n_abstract_tokens"] = len(inv) if isinstance(inv, dict) else 0
    loc = d.get("primary_location") or {}
    out["landing_page_url"] = loc.get("landing_page_url")
    out["primary_location_source"] = ((loc.get("source") or {}).get("display_name"))
    out["ids"] = d.get("ids")
    return out


def summarize_crossref(d):
    if not isinstance(d, dict):
        return {"error": "non-json"}
    msg = d.get("message") or {}
    out = {}
    out["DOI"] = msg.get("DOI")
    out["title"] = (msg.get("title") or [None])[0]
    out["container"] = (msg.get("container-title") or [None])[0]
    out["is_referenced_by_count"] = msg.get("is-referenced-by-count")
    out["type"] = msg.get("type")
    out["URL"] = msg.get("URL")
    out["issued"] = msg.get("issued", {}).get("date-parts")
    auth = msg.get("author") or []
    out["n_authors"] = len(auth)
    out["first_author"] = (f"{auth[0].get('given','')} {auth[0].get('family','')}".strip()
                           if auth else None)
    out["has_abstract"] = bool(msg.get("abstract"))
    out["abstract_len"] = len(msg.get("abstract") or "")
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    res = {}

    print("[1] OpenAlex 关键词搜索 ...")
    code, d, ms = oa_search("attention is all you need transformer", 3,
                            2017, 2020)
    res["oa_search"] = {"status": code, "ms": round(ms, 1)}
    if d:
        res["oa_search"]["meta"] = d.get("meta")
        rs = d.get("results") or []
        res["oa_search"]["n_results"] = len(rs)
        res["oa_search"]["first"] = summarize_openalex(rs[0]) if rs else None
        res["oa_search"]["rank_fields"] = sorted(rs[0].keys()) if rs else []

    print("[2] OpenAlex 按引用数排序 ...")
    code, d, ms = oa_search("attention is all you need", 3,
                            sort="cited_by_count:desc")
    res["oa_search_sorted"] = {"status": code, "ms": round(ms, 1)}
    if d:
        res["oa_search_sorted"]["n_results"] = len(d.get("results") or [])
        res["oa_search_sorted"]["cited_by"] = [
            (r.get("cited_by_count"), (r.get("title") or "")[:50])
            for r in (d.get("results") or [])]

    print("[3] OpenAlex DOI 查询 ...")
    code, d, ms = oa_doi(KNOWN_DOI)
    res["oa_doi"] = {"status": code, "ms": round(ms, 1),
                     "summary": summarize_openalex(d) if d else None}

    print("[4] OpenAlex 未知名 DOI（应 404）...")
    code, d, ms = oa_doi(FAKE_DOI)
    res["oa_doi_fake"] = {"status": code, "ms": round(ms, 1),
                          "error": (d.get("error") if isinstance(d, dict) else None),
                          "message": (d.get("message") if isinstance(d, dict) else None)}

    print("[5] Crossref 关键词搜索 ...")
    code, d, ms = cr_search("attention is all you need transformer", 3)
    res["cr_search"] = {"status": code, "ms": round(ms, 1)}
    if d:
        msg = d.get("message") or {}
        res["cr_search"]["total_results"] = msg.get("total-results")
        it = msg.get("items") or []
        res["cr_search"]["n_results"] = len(it)
        res["cr_search"]["first"] = summarize_crossref(it[0] if it else {})
        res["cr_search"]["rank_fields"] = sorted(it[0].keys()) if it else []

    print("[6] Crossref DOI 查询 ...")
    code, d, ms = cr_doi(KNOWN_DOI)
    res["cr_doi"] = {"status": code, "ms": round(ms, 1),
                     "summary": summarize_crossref(d) if d else None}

    print("[7] Crossref 未知名 DOI（应 404）...")
    code, d, ms = cr_doi(FAKE_DOI)
    res["cr_doi_fake"] = {"status": code, "ms": round(ms, 1),
                          "message": (d.get("message") if isinstance(d, dict) else None)}

    print("[8] arXiv 按 ID 查询 ...")
    code, body, ms = get("https://export.arxiv.org/api/query?id_list=" + ARXIV_ID)
    txt = body.decode("utf-8", errors="ignore")
    res["arxiv_by_id"] = {"status": code, "ms": round(ms, 1), "bytes": len(body),
                          "has_entry": "<entry>" in txt,
                          "title_m": (txt.split("<title>")[1].split("</title>")[-1][:80]
                                      if "<title>" in txt else None)}

    print("[9] 连压 20 次 OpenAlex 看是否 429 ...")
    codes = []
    t0 = time.time()
    for i in range(20):
        c, _, m = oa_search(f"deep learning paper {i}", 1)
        codes.append(c)
    res["oa_burst20"] = {"codes": codes, "ms_total": round(time.time() - t0, 1),
                         "n_429": sum(1 for c in codes if c == 429),
                         "distinct": sorted(set(codes))}

    print("[10] 连压 20 次 Crossref 看是否 429 ...")
    codes = []
    t0 = time.time()
    for i in range(20):
        c, _, m = cr_search(f"deep learning paper {i}", 1)
        codes.append(c)
    res["cr_burst20"] = {"codes": codes, "ms_total": round(time.time() - t0, 1),
                         "n_429": sum(1 for c in codes if c == 429),
                         "distinct": sorted(set(codes))}

    # 对照组：Semantic Scholar 当前是否还在 429
    print("[11] 对照：Semantic Scholar 现状 ...")
    s2 = ("https://api.semanticscholar.org/graph/v1/paper/search?"
          "query=attention&limit=1&fields=title,year")
    code, d, ms = jget(s2)
    res["s2_control"] = {"status": code, "ms": round(ms, 1),
                         "message": (d.get("message") if isinstance(d, dict) else None)}

    with io.open(os.path.join(OUT, "lit_api_probe.json"), "w",
                 encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    # ---- 报告 ----
    L = ["# 文献 API 实测探针", "",
         "为 academic.py 从 Semantic Scholar 迁出提供实测依据。", "",
         "## 总览", "",
         "| API | 用途 | HTTP | 延迟 | 关键实测 |",
         "|---|---|---|---|---|"]

    oas = res["oa_search"]
    first = oas.get("first") or {}
    L.append("| OpenAlex `/works?search` | 关键词搜索 | %s | %sms | meta=%s, n=%s, "
             "cited_by_count=%s, 年份=%s, 摘要倒排=%s |" % (
                 oas.get("status"), oas.get("ms"),
                 (oas.get("meta") or {}).get("count"),
                 oas.get("n_results"), first.get("cited_by_count"),
                 first.get("publication_year"),
                 first.get("has_abstract_inverted_index")))
    L.append("| OpenAlex `/works/{doi}` | DOI 精确 | %s | %sms | title=%s |" % (
        res["oa_doi"]["status"], res["oa_doi"]["ms"],
        (res["oa_doi"]["summary"] or {}).get("title")))
    L.append("| OpenAlex 未知 DOI | 不存在判定 | **%s** | %sms | %s |" % (
        res["oa_doi_fake"]["status"], res["oa_doi_fake"]["ms"],
        res["oa_doi_fake"].get("message")))
    L.append("| Crossref `/works?query.bibliographic` | 关键词搜索 | %s | %sms | "
             "total-results=%s, n=%s, cited=%s |" % (
                 res["cr_search"]["status"], res["cr_search"]["ms"],
                 res["cr_search"].get("total_results"),
                 res["cr_search"].get("n_results"),
                 (res["cr_search"].get("first") or {}).get("is_referenced_by_count")))
    L.append("| Crossref `/works/{doi}` | DOI 权威查询 | %s | %sms | title=%s |" % (
        res["cr_doi"]["status"], res["cr_doi"]["ms"],
        (res["cr_doi"]["summary"] or {}).get("title")))
    L.append("| Crossref 未知 DOI | 不存在判定 | **%s** | %sms | %s |" % (
        res["cr_doi_fake"]["status"], res["cr_doi_fake"]["ms"],
        res["cr_doi_fake"].get("message")))
    L.append("| arXiv `id_list` | arXiv ID 查询 | %s | %sms | has_entry=%s |" % (
        res["arxiv_by_id"]["status"], res["arxiv_by_id"]["ms"],
        res["arxiv_by_id"]["has_entry"]))
    L.append("| **Semantic Scholar（现状对照）** | 关键词搜索 | **%s** | %sms | %s |" % (
        res["s2_control"]["status"], res["s2_control"]["ms"],
        res["s2_control"].get("message")))

    L += ["", "## 压测（各连打 20 次，看是否 429）", "",
          "| API | 20 次状态码分布 | 429 次数 | 总耗时 |",
          "|---|---|---|---|"]
    L.append("| OpenAlex | %s | **%d** | %sms |" % (
        res["oa_burst20"]["distinct"], res["oa_burst20"]["n_429"],
        res["oa_burst20"]["ms_total"]))
    L.append("| Crossref | %s | **%d** | %sms |" % (
        res["cr_burst20"]["distinct"], res["cr_burst20"]["n_429"],
        res["cr_burst20"]["ms_total"]))

    L += ["", "## 字段映射实测", "",
          "### OpenAlex → academic.py 期望的 S2 形状", "",
          "| S2 字段 | OpenAlex 字段 | 实测 |",
          "|---|---|---|"]
    for s2f, oaf, val in [
        ("title", "title", first.get("title")),
        ("year", "publication_year", first.get("publication_year")),
        ("citationCount", "cited_by_count", first.get("cited_by_count")),
        ("authors[].name", "authorships[].author.display_name",
         first.get("first_author")),
        ("abstract", "abstract_inverted_index（需重建）",
         "有" if first.get("has_abstract_inverted_index") else "无"),
        ("externalIds.DOI", "doi", first.get("doi")),
        ("url", "primary_location.landing_page_url",
         first.get("landing_page_url")),
        ("influentialCitationCount", "**无对应**", "OpenAlex 不提供"),
    ]:
        L.append("| `%s` | `%s` | %s |" % (s2f, oaf, val))

    L += ["", "### OpenAlex 排序字段实测", ""]
    for c, t in (res["oa_search_sorted"].get("cited_by") or []):
        L.append("- %s 被引 %s" % (t, c))

    L += ["", "## OpenAlex 首条结果完整字段名", "",
          "```", json.dumps(res["oa_search"].get("rank_fields") or [],
                            ensure_ascii=False, indent=2), "```",
          "", "## Crossref 首条结果完整字段名", "",
          "```", json.dumps(res["cr_search"].get("rank_fields") or [],
                            ensure_ascii=False, indent=2), "```", ""]

    with io.open(os.path.join(OUT, "lit_api_probe.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("OK -> evals/results/lit_api_probe.md")


if __name__ == "__main__":
    main()
