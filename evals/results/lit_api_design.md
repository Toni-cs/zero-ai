# academic.py 迁移设计验证

为从 Semantic Scholar（实测 5/5 次 429）迁到 OpenAlex + Crossref
提供逐条实测依据。所有数字均为本轮实跑。

## D1  相关性 + 引用排序能否同时成立

### 组合 A：filter=default.search + sort=cited_by_count:desc
HTTP 200  1427ms  n=5
  - [78456] Exploiting Generative AI to Scale up Intelligent Tutoring System
  - [47981] Highly accurate protein structure prediction with AlphaFold
  - [45623] AI-Assisted Pipeline for Dynamic Generation of Trustworthy Healt
  - [26832] Attention Is All You Need
  - [17301] HISTORIAE, History of Socio-Cultural Transformation as Linguisti

### 组合 B：search=... + sort=cited_by_count:desc（首轮已测，仅记结果）
  首条 = Exploiting Generative AI... [78456]  —— 相关性丢失

### 组合 C：search=... 默认排序（基准）
HTTP 200  1261ms  n=5
  - [153] Cross-Attention is All You Need: Adapting Pretrained Transformer
  - [0] Attention Is All You Need: The Rise of the Transformer
  - [42] Attention is all you need: An interpretable transformer-based as
  - [31] Cross attention is all you need: relational remote sensing chang
  - [3] Attention Is All You Need to Tell: Transformer-Based Image Capti

**判定**：看组合 A 首条是否为 attention/transformer 相关论文。

## D2  per_page 上限与 select 裁字段

  per_page=20   HTTP=200 n_returned=20   per_page_meta=20 1135ms
  per_page=50   HTTP=200 n_returned=50   per_page_meta=50 952ms
  per_page=100  HTTP=200 n_returned=100  per_page_meta=100 1059ms
  per_page=200  HTTP=200 n_returned=200  per_page_meta=200 1138ms

### select 是否被接受（不带 select 的字段数 vs 带 select 的字段数）
  不带 select 字段数 = -1   带 select 字段数 = 4
  （select 生效 => False）

## D3  abstract_inverted_index 还原摘要

title = Attention Is All You Need
inverted_index 是 dict，token 数 = 118
还原后长度 = 1136
还原结果前 300 字：
  The dominant sequence transduction models are based on complex recurrent or convolutional neural networks in an encoder-decoder configuration. The best performing models also connect the encoder and decoder through an attention mechanism. We propose a new simple network architecture, the Transformer

词序检查（应为连贯英文，不应出现同词重复跳序）：
  前 15 词 = ['The', 'dominant', 'sequence', 'transduction', 'models', 'are', 'based', 'on', 'complex', 'recurrent', 'or', 'convolutional', 'neural', 'networks', 'in']

## D4  年份过滤与 search 共存

### search + 年份 filter  HTTP 200  n=5
  - year=2018 [105] Captioning Transformer with Stacked Attention Modules
  - year=2018 [22] You May Not Need Attention
  - year=2018 [111] Accelerating Neural Transformer via an Average Attention
  - year=2018 [227] Image Transformer
  - year=2018 [79] How Much Attention Do You Need? A Granular Analysis of N

### search + 年份 + 引用排序  HTTP 200  n=5
  - year=2018 [45623] AI-Assisted Pipeline for Dynamic Generation of Trustwort
  - year=2018 [2520] Self-Attention with Relative Position Representations
  - year=2017 [2225] Squeeze-and-Excitation Networks
  - year=2017 [2112] Step-Up DC–DC Converters: A Comprehensive Review of Volt
  - year=2018 [1866] Conceptual Captions: A Cleaned, Hypernymed, Image Alt-te

## D5  兜底成功率（各 6 次，带 15s 超时）

### OpenAlex search
  状态码 = [200, 200, 200, -1, 200, 200]
  成功(200) = 5/6   429 = 0   失败(-1) = 1
  延迟 p50=1543ms  max=21561ms

### Crossref query.title
  状态码 = [500, 200, 200, 500, 500, 500]
  成功(200) = 2/6   429 = 0   失败(-1) = 0
  延迟 p50=865ms  max=1050ms

### Crossref /works/{doi} 已知
  状态码 = [200, 200, 500, 200, 200, 200]
  成功(200) = 5/6   429 = 0   失败(-1) = 0
  延迟 p50=1657ms  max=1706ms

## D6  标题匹配能否支撑 citation_check

### 查询: Attention Is All You Need
  HTTP 200  n=3
  首条: Attention Is All You Need
  SequenceMatcher 相似度 = 1.0000
  -> 验证通过

### 查询: Deep learning
  HTTP 200  n=3
  首条: Deep Learning
  SequenceMatcher 相似度 = 1.0000
  -> 验证通过

### 查询: Highly accurate protein structure prediction with AlphaFold
  HTTP 200  n=3
  首条: Highly accurate protein structure prediction with AlphaFold
  SequenceMatcher 相似度 = 1.0000
  -> 验证通过

### 查询: A Complete Theory of Zero-Shot Quantum Consciousness Routing
  HTTP 200  n=3
  首条: Single-shot online sequence classification with unbounded quantum memory advanta
  SequenceMatcher 相似度 = 0.2676
  -> 匹配不足/查无此文

