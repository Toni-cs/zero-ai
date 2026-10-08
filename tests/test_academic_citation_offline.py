# -*- coding: utf-8 -*-
"""academic.py 文献检索迁移的离线回归测试（不发任何网络请求）。

覆盖两类必须稳定守住的正确性：

1. 「查无此记录」与「请求失败」必须可区分。
   把网络故障输出成「文献不存在」等于用工具的故障指控用户编造引用，
   是本次迁移最不能犯的错，因此用 monkeypatch 强制各种 status 来验证。

2. 数据出处必须反映**实际贡献结果**的源，不能硬编码。
   真实验收中出现过一次静默降级：主源全挂、结果全来自 arXiv，
   报告却仍写「数据来源：OpenAlex」。

实调网络的端到端验收在 evals/test_academic_migration.py，
其结果见 evals/results/academic_migration.md。
"""
import pytest

from zeroai.tools import academic as A


# ────────────────────────── 归一化 ──────────────────────────

def _oa_work(**over):
    """构造一条最小可用的 OpenAlex work。"""
    w = {
        "id": "https://openalex.org/W1",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "cited_by_count": 99999,
        "doi": "https://doi.org/10.48550/arXiv.1706.03762",
        "authorships": [
            {"author": {"display_name": "Ashish Vaswani"}},
            {"author": {"display_name": "Noam Shazeer"}},
        ],
        "primary_location": {
            "landing_page_url": "https://arxiv.org/abs/1706.03762"},
        "abstract_inverted_index": {"The": [0], "cat": [2], "sat": [1]},
    }
    w.update(over)
    return w


def test_oa_to_s2_maps_core_fields():
    d = A._oa_to_s2(_oa_work())
    assert d["title"] == "Attention Is All You Need"
    assert d["year"] == 2017
    assert d["citationCount"] == 99999
    # DOI 前缀必须剥掉，否则下游显示成完整 URL
    assert d["externalIds"]["DOI"] == "10.48550/arXiv.1706.03762"
    # 从 landing_page_url 里抽出 arXiv 编号
    assert d["externalIds"]["ArXiv"] == "1706.03762"
    assert [a["name"] for a in d["authors"]] == ["Ashish Vaswani", "Noam Shazeer"]
    assert d["_source"] == "OpenAlex"


def test_oa_to_s2_influential_is_none_not_zero():
    """OpenAlex 不提供影响力指标，必须是 None 而非 0。

    填 0 会被读成真实指标，等于凭空造数据。
    """
    assert A._oa_to_s2(_oa_work())["influentialCitationCount"] is None


def test_oa_to_s2_missing_year_is_not_none_string():
    d = A._oa_to_s2(_oa_work(publication_year=None))
    assert d["year"] not in (None, "None")
    assert d["year"] == "未知"


def test_oa_abstract_reconstructed_in_position_order():
    """倒排索引必须按位置还原，顺序错了摘要就是乱的。"""
    assert A._oa_abstract(_oa_work()) == "The sat cat"


def test_oa_abstract_missing_returns_empty():
    assert A._oa_abstract({"abstract_inverted_index": None}) == ""
    assert A._oa_abstract({}) == ""


def test_strip_jats_removes_xml_tags():
    assert A._strip_jats("<jats:p>Hello <b>world</b></jats:p>") == "Hello world"
    assert A._strip_jats("") == ""


def test_cr_to_s2_maps_fields():
    item = {
        "title": ["Attention Is All You Need"],
        "author": [{"given": "Ashish", "family": "Vaswani"}],
        "issued": {"date-parts": [[2017]]},
        "is-referenced-by-count": 123,
        "DOI": "10.1000/xyz",
        "URL": "https://doi.org/10.1000/xyz",
        "abstract": "<jats:p>Some abstract</jats:p>",
    }
    d = A._cr_to_s2(item)
    assert d["title"] == "Attention Is All You Need"
    assert d["authors"][0]["name"] == "Ashish Vaswani"
    assert d["year"] == 2017
    assert d["citationCount"] == 123
    assert d["abstract"] == "Some abstract"
    assert d["_source"] == "Crossref"


def test_sort_by_citations_is_descending():
    rows = [{"citationCount": 1}, {"citationCount": 50}, {"citationCount": 7}]
    out = A._sort_by_citations(rows)
    assert [r["citationCount"] for r in out] == [50, 7, 1]
    # 缺字段不得让排序崩
    A._sort_by_citations([{"citationCount": None}, {}])


# ─────────────────── 「查无」与「失败」必须可区分 ───────────────────

class _FakeHTTP:
    """按注入的 (status, body) 返回，替代 _lit_get。"""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, timeout=10):
        self.calls.append(url)
        for key, val in self.mapping.items():
            if key in url:
                return val
        return (-1, b"")


def test_doi_not_found_requires_both_sources(monkeypatch):
    """双源都明确 404 才能说「文献不存在」。"""
    fake = _FakeHTTP({"openalex.org": (404, b""),
                      "crossref.org": (404, b"")})
    monkeypatch.setattr(A, "_lit_get", fake)
    out = A.citation_check(doi="10.9999/fake")
    assert "文献不存在" in out
    assert "双源均明确返回" in out
    assert len(fake.calls) == 2, "两个源都必须查，不能只查一个就下结论"


def test_doi_network_failure_is_never_not_found(monkeypatch):
    """网络故障绝不能输出「文献不存在」。"""
    monkeypatch.setattr(A, "_lit_get", _FakeHTTP({}))
    out = A.citation_check(doi="10.1038/nature14539")
    assert "文献不存在" not in out
    assert "无法确认" in out
    assert "不代表该引用是假的" in out


def test_doi_partial_failure_is_never_not_found(monkeypatch):
    """一个源 404、另一个源报错 —— 信息不完整，同样不许下「不存在」结论。"""
    fake = _FakeHTTP({"openalex.org": (404, b""),
                      "crossref.org": (500, b"oops")})
    monkeypatch.setattr(A, "_lit_get", fake)
    out = A.citation_check(doi="10.1038/nature14539")
    assert "文献不存在" not in out
    assert "无法确认" in out


def test_oa_by_doi_maps_429_to_rate_limited(monkeypatch):
    """429 必须映射成 rate_limited，不能混进 error —— 两者对用户含义不同。"""
    monkeypatch.setattr(A, "_lit_get", _FakeHTTP({"openalex.org": (429, b"")}))
    state, paper = A._oa_by_doi("10.1/x")
    assert state == "rate_limited"
    assert paper is None


def test_doi_rate_limited_is_never_not_found(monkeypatch):
    """OpenAlex 429（日配额耗尽）时不得断言「文献不存在」，且要说明是限流。

    背景：实测 OpenAlex 的 429 是日预算耗尽（Retry-After 49335s），
    此时 Crossref 若 404 也不足以证明 DOI 不存在 —— Crossref 不收录
    DataCite 系 DOI（10.48550/arXiv.* 等）。
    """
    fake = _FakeHTTP({"openalex.org": (429, b'{"error":"Rate limit exceeded"}'),
                      "crossref.org": (404, b"")})
    monkeypatch.setattr(A, "_lit_get", fake)
    out = A.citation_check(doi="10.9999/fake")
    assert "文献不存在" not in out
    assert "无法确认" in out
    assert "rate_limited" in out
    assert "日配额已用尽" in out
    assert "不代表该引用是假的" in out


def test_doi_found_via_crossref_when_openalex_fails(monkeypatch):
    """OpenAlex 挂了但 Crossref 命中 —— 兜底必须生效。"""
    cr_work = (b'{"status":"ok","message":{"DOI":"10.1038/nature14539",'
               b'"title":["Deep learning"],'
               b'"author":[{"given":"Y","family":"LeCun"}],'
               b'"issued":{"date-parts":[[2015]]},'
               b'"is-referenced-by-count":500}}')
    fake = _FakeHTTP({"openalex.org": (-1, b""),
                      "crossref.org": (200, cr_work)})
    monkeypatch.setattr(A, "_lit_get", fake)
    out = A.citation_check(doi="10.1038/nature14539")
    assert "验证通过" in out
    assert "Deep learning" in out


def test_title_network_failure_is_never_fabricated(monkeypatch):
    """标题校验时网络全挂，绝不能说引用是虚构的。"""
    monkeypatch.setattr(A, "_lit_get", _FakeHTTP({}))
    out = A.citation_check(title="Attention Is All You Need")
    assert "虚构" not in out
    assert "未完成" in out


def test_arxiv_network_failure_is_never_not_found(monkeypatch):
    fake = _FakeHTTP({"arxiv.org": (-1, b"")})
    monkeypatch.setattr(A, "_lit_get", fake)
    out = A.citation_check(arxiv_id="1706.03762")
    assert "文献不存在" not in out
    assert "未完成" in out


# ─────────────────────── 数据出处不得谎报 ───────────────────────

def test_literature_review_reports_real_source_when_primary_down(monkeypatch):
    """主检索源全挂时，报告必须说「仅 arXiv」而不是继续写 OpenAlex。"""
    monkeypatch.setattr(A, "_lit_get", _FakeHTTP({}))          # OpenAlex/Crossref 全挂
    monkeypatch.setattr(A, "_lit_review_search_arxiv",
                        lambda topic, num: [{
                            "title": "Some arXiv paper",
                            "authors": [{"name": "A"}],
                            "year": 2023, "citations": 3,
                            "abstract": "", "doi": "", "arxiv_id": "",
                            "url": "", "source": "arXiv"}])
    out = A.literature_review("transformer attention", num_papers=5)
    assert "数据来源：仅 arXiv" in out
    assert "主检索源不可用" in out
    # 不能在主源失败时仍宣称自己用了 OpenAlex
    assert "数据来源：OpenAlex" not in out
    # 凑不满目标篇数也要说
    assert "目标 5 篇" in out


def test_literature_review_reports_source_when_primary_up(monkeypatch):
    oa_body = (b'{"meta":{"count":1},"results":[{"id":"https://openalex.org/W1",'
               b'"title":"OpenAlex paper","publication_year":2020,'
               b'"cited_by_count":42,"doi":null,"authorships":[],'
               b'"primary_location":null,"abstract_inverted_index":null}]}')
    monkeypatch.setattr(A, "_lit_get",
                        _FakeHTTP({"openalex.org": (200, oa_body)}))
    monkeypatch.setattr(A, "_lit_review_search_arxiv",
                        lambda topic, num: [])
    out = A.literature_review("transformer attention", num_papers=5)
    assert "数据来源：OpenAlex + arXiv" in out
    assert "主检索源不可用" not in out


def test_lit_review_search_returns_none_source_when_both_down(monkeypatch):
    monkeypatch.setattr(A, "_lit_get", _FakeHTTP({}))
    papers, source = A._lit_review_search_papers("topic", 5, 0, 0)
    assert papers == []
    assert source is None


# ─────────────────────── 输出不得再提 429 限流 ───────────────────────

def test_academic_search_failure_message_has_no_rate_limit_wording(monkeypatch):
    """两个源都失败时给出的文案不能是「过于频繁/429」——那是 S2 的旧病。"""
    monkeypatch.setattr(A, "_lit_get", _FakeHTTP({}))
    out = A.academic_search("attention")
    assert "学术搜索失败" in out
    assert "429" not in out
    assert "过于频繁" not in out
    assert "Semantic Scholar" not in out


def test_academic_search_hides_influence_when_source_lacks_it(monkeypatch):
    """数据源没有影响力指标时不得显示「影响力」列。"""
    oa_body = (b'{"meta":{"count":1},"results":[{"id":"https://openalex.org/W1",'
               b'"title":"T","publication_year":2020,"cited_by_count":7,'
               b'"doi":null,"authorships":[],"primary_location":null,'
               b'"abstract_inverted_index":null}]}')
    monkeypatch.setattr(A, "_lit_get",
                        _FakeHTTP({"openalex.org": (200, oa_body)}))
    out = A.academic_search("attention")
    assert "影响力" not in out
    assert "引用: 7" in out
    assert "数据源: OpenAlex" in out


def test_academic_search_citation_sort_notes_approximation(monkeypatch):
    """按引用排序必须注明是在相关性结果内重排（服务端做不到）。"""
    oa_body = (b'{"meta":{"count":1},"results":[{"id":"https://openalex.org/W1",'
               b'"title":"T","publication_year":2020,"cited_by_count":7,'
               b'"doi":null,"authorships":[],"primary_location":null,'
               b'"abstract_inverted_index":null}]}')
    monkeypatch.setattr(A, "_lit_get",
                        _FakeHTTP({"openalex.org": (200, oa_body)}))
    out = A.academic_search("attention", sort_by="citations")
    assert "在相关性结果内重排" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
