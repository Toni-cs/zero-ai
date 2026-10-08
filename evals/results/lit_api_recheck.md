# 文献 API 复测（针对首轮探针的 3 个异常）

## Q1  OpenAlex 排序参数是否让 search 失效

### 无 sort（默认）   HTTP 200  1438ms  n=5
  - [153] Cross-Attention is All You Need: Adapting Pretrained Transformers for 
  - [0] Attention Is All You Need: The Rise of the Transformer
  - [42] Attention is all you need: An interpretable transformer-based asset al
  - [31] Cross attention is all you need: relational remote sensing change dete
  - [3] Attention Is All You Need to Tell: Transformer-Based Image Captioning

### sort=cited_by_count:desc   HTTP 200  1522ms  n=5
  - [78456] Exploiting Generative AI to Scale up Intelligent Tutoring Systems
  - [47981] Highly accurate protein structure prediction with AlphaFold
  - [45623] AI-Assisted Pipeline for Dynamic Generation of Trustworthy Health Supp
  - [26832] Attention Is All You Need
  - [17301] HISTORIAE, History of Socio-Cultural Transformation as Linguistic Data

### sort=relevance_score:desc   HTTP 200  1404ms  n=5
  - [153] Cross-Attention is All You Need: Adapting Pretrained Transformers for 
  - [0] Attention Is All You Need: The Rise of the Transformer
  - [42] Attention is all you need: An interpretable transformer-based asset al
  - [31] Cross attention is all you need: relational remote sensing change dete
  - [3] Attention Is All You Need to Tell: Transformer-Based Image Captioning

### filter 换成 default.search   HTTP 200  1365ms  n=5
  - [153] Cross-Attention is All You Need: Adapting Pretrained Transformers for 
  - [0] Attention Is All You Need: The Rise of the Transformer
  - [42] Attention is all you need: An interpretable transformer-based asset al
  - [31] Cross attention is all you need: relational remote sensing change dete
  - [3] Attention Is All You Need to Tell: Transformer-Based Image Captioning

### Q1 判据
若「无 sort」和「relevance_score」都命中 transformer 相关论文，
而「cited_by_count」跑出香农/AlphaFold，则该 sort 会绕过搜索条件，
不能用它做 citations 排序，必须改用别的方式（如 filter 或客户端重排）。

## Q2  压测 20 次的真实延迟（逐次打印）

首轮报「20 次 72.4ms」，与单次 1324ms 矛盾，逐次核对。

### OpenAlex search
  #01 HTTP=200    1201.5ms
  #02 HTTP=200    1308.4ms
  #03 HTTP=200    1222.1ms
  #04 HTTP=200    1304.8ms
  #05 HTTP=200    1308.9ms
  #06 HTTP=200    1262.2ms
  #07 HTTP=-1    33386.3ms
  #08 HTTP=200    1422.0ms
  #09 HTTP=-1    31200.5ms
  #10 HTTP=200    1527.7ms
  #11 HTTP=200    1328.3ms
  #12 HTTP=200    1253.5ms
  #13 HTTP=200    1337.8ms
  #14 HTTP=200    1288.7ms
  #15 HTTP=200    1276.6ms
  #16 HTTP=200    1609.8ms
  #17 HTTP=200    1498.0ms
  #18 HTTP=200    1358.1ms
  #19 HTTP=-1    26156.2ms
  #20 HTTP=200    1763.0ms
  distinct codes = [-1, 200]
  n_429 = 0
  latency p50=1333ms  p95=31201ms  mean=5701ms  sum=114015ms

## Q3  Crossref 关键词搜索 500：偶发还是持续

### query.bibliographic
  first: total=1345833 n=3
    - [0] Attention is All You Need... Unless You Are a CISO: The Inherent Incom
    - [0] Attention via Synaptic Plasticity is All You Need A Biologically Inspi
    - [0] Failure Is All You Need — Attention as Reverse Diffusion: A Unified Ge
  5 次状态码分布 = {200: 4, 500: 1}

### query (通用)
  first: total=1492814 n=3
    - [0] Attention is All You Need... Unless You Are a CISO: The Inherent Incom
    - [0] Attention via Synaptic Plasticity is All You Need A Biologically Inspi
    - [0] Failure Is All You Need — Attention as Reverse Diffusion: A Unified Ge
  5 次状态码分布 = {200: 3, 500: 2}

### query.title
  first: total=933770 n=3
    - [24] Attention Is All You Need
    - [6] Attention Is All You Need
    - [22] Attention Is All You Need
  5 次状态码分布 = {200: 5}

### Q3-2  Crossref 未知名 DOI 应得 404 吗
  #1 HTTP=404  None
  #2 HTTP=404  None
  #3 HTTP=404  None
  #4 HTTP=404  None
  #5 HTTP=500  {'name': 'class java.util.concurrent.CancellationException', 'description': 'java.util.concurrent.CancellationException: Request execution cancelled', 'message'

### Q3-2b  对照：OpenAlex 未知名 DOI
  #1 HTTP=-1  None
  #2 HTTP=404  None
  #3 HTTP=404  None

## 附：Semantic Scholar 现状（连续 5 次）
  #1 HTTP=429 {"message": "Too Many Requests. Please wait and try again or apply for a key for higher rate limits. https://www.semanticscholar.org/product/api#api-key-form", "code": "429"}
  5 次状态码 = [429, 429, 429, 429, 429]

