# academic.py 迁移验收（实调 OpenAlex / Crossref / arXiv）

## A. academic_search

### 默认相关性  -> PASS  (2.5s, 5 条, 数据源=Crossref)
```
=== 学术搜索: 「attention is all you need transformer」 ===
关键词命中 1033389 篇（匹配数），显示前 5 篇（数据源: Crossref）

[1] Attention Is All You Need to Tell: Transformer-Based Image Captioning
```

### 按引用数排序  -> PASS  (6.8s, 5 条, 数据源=Crossref)
```
=== 学术搜索: 「attention is all you need transformer」 ===
关键词命中 1033389 篇（匹配数），显示前 5 篇（按引用数排序（在相关性结果内重排），数据源: Crossref）

[1] Attention Is All You Need In Speech Separation
```

### 年份筛选 2017-2018  -> PASS  (3.6s, 5 条, 数据源=Crossref)
```
=== 学术搜索: 「attention is all you need」 ===
关键词命中 65412 篇（匹配数），显示前 5 篇（年份: 2017-2018，数据源: Crossref）

[1] All You Need Is Love
```

### 中文查询  -> PASS  (1.9s, 3 条, 数据源=Crossref)
```
=== 学术搜索: 「Transformer 注意力机制 综述」 ===
关键词命中 204571 篇（匹配数），显示前 3 篇（数据源: Crossref）

[1] 融合空间注意力机制的图像语义描述算法
```

**A 小结：4/4 通过**

## B. citation_check

### 真 DOI（Nature 深度学习）  -> PASS  (2.1s)
- 期望命中其一：「验证通过」
- 实际命中：「验证通过」
- 禁止出现：「文献不存在」 -> 未命中 ✓
依据：应命中
```
=== 引用校验结果 ===
校验方式：DOI
查询条件：10.1038/nature14539
状态：✓ 验证通过：文献真实存在（标题精确匹配）

文献信息：
  标题：Deep learning
```

### 假 DOI  -> PASS  (1.5s)
- 期望命中其一：「文献不存在」 / 「无法确认」
- 实际命中：「无法确认」
- 禁止出现：「验证通过」 -> 未命中 ✓
依据：双源都明确 not_found 才能说「不存在」；若主源正在限流，只能如实说「无法确认」——两种都是正确行为，但都绝不能说「验证通过」
```
⚠ 引用校验未完成：无法确认该 DOI 是否真实存在
  查询条件：DOI:10.9999/zeroai.not-a-real-doi-2026
  OpenAlex: rate_limited    Crossref: not_found
  原因：OpenAlex 日配额已用尽（服务端给出的恢复时间约十几小时），与该引用本身无关。
  注意：这**不代表该引用是假的**，只是本次两个源没有全部查询成功。
  请稍后重试，或用 academic_search 搜索确认。
```

### 真标题  -> PASS  (15.3s)
- 期望命中其一：「验证通过」
- 实际命中：「验证通过」
- 禁止出现：「虚构」 -> 未命中 ✓
依据：相似度应 >= 95%
```
=== 引用校验结果 ===
校验方式：标题匹配
查询条件：Attention Is All You Need
状态：✓ 验证通过：文献真实存在（标题精确匹配）

文献信息：
  标题：Attention Is All You Need
```

### 假标题（虚构文献）  -> PASS  (2.7s)
- 期望命中其一：「虚构」
- 实际命中：「虚构」
依据：相似度过低 => 明确说很可能虚构
```
⚠ 引用校验结果：未找到标题足够接近的文献
  查询条件：标题「A Complete Theory of Zero-Shot Quantum Consciousness Routing」
  检索到的候选文献中没有标题足够接近的
  最佳候选相似度：57%（阈值 60%，低于此判定为不匹配）
  该引用很可能是 AI 编造的虚构文献，请勿在学术写作中使用
  建议：使用 academic_search 搜索真实存在的文献替代
```

### 真 arXiv ID  -> PASS  (1.1s)
- 期望命中其一：「验证通过」
- 实际命中：「验证通过」
- 禁止出现：「文献不存在」 -> 未命中 ✓
依据：arXiv 官方 API
```
=== 引用校验结果 ===
校验方式：arXiv
查询条件：1706.03762
状态：✓ 验证通过：文献真实存在（标题精确匹配）

文献信息：
  标题：Attention Is All You Need
```

### 假 arXiv ID  -> PASS  (0.7s)
- 期望命中其一：「文献不存在」
- 实际命中：「文献不存在」
依据：arXiv 无此条目
```
✗ 引用校验结果：文献不存在
  查询条件：arXiv:9999.99999
  校验源：arXiv 官方 API
  该引用很可能是 AI 编造的虚构文献，请勿在学术写作中使用
```

### 无参数  -> PASS  (0.0s)
- 期望命中其一：「请提供文献标题」
- 实际命中：「请提供文献标题」
依据：参数校验
```
请提供文献标题、DOI 或 arXiv ID 中的至少一个参数
```

**B 小结：7/7 通过**

## C. 关键正确性：网络故障不得被输出成「文献不存在」

这是本次迁移最不能出错的地方 —— 把工具故障说成「用户编造引用」，
代价远高于多报一次网络错误。用 monkeypatch 让两个源都返回网络失败：

### DOI + 双源网络故障 -> PASS
- 错误地出现「文献不存在」：否 ✓
- 正确地出现「无法确认」：是 ✓
```
⚠ 引用校验未完成：无法确认该 DOI 是否真实存在
  查询条件：DOI:10.1038/nature14539
  OpenAlex: error    Crossref: error
  注意：这**不代表该引用是假的**，只是本次两个源没有全部查询成功。
```

### 标题 + 双源网络故障 -> PASS
- 错误地出现「虚构」：否 ✓
- 正确地出现「未完成」：是 ✓
```
⚠ 引用校验未完成：OpenAlex 与 Crossref 均未响应
  查询条件：标题「Attention Is All You Need」
  注意：这**不代表该引用是假的**，只是本次请求失败，请稍后重试。
```

### arXiv + 网络故障 -> PASS
- 错误地出现「文献不存在」：否 ✓
- 正确地出现「未完成」：是 ✓
```
⚠ 引用校验未完成：arXiv API 未响应
  查询条件：arXiv:1706.03762
  注意：这**不代表该引用是假的**，请稍后重试。
```

**C 小结：3/3 通过**

## D. 429 验收（连打 12 次 academic_search）

| 指标 | 实测 |
|---|---|
| 请求次数 | 12 |
| **用户可见的 429/限流提示** | **0** |
| 完全失败次数 | 0 |
| 实际使用的数据源 | Crossref×12 |
| 延迟 p50 | 1.89s |
| 延迟 max | 29.92s |
| 总耗时 | 72.1s |

**对照（改前实测）**：Semantic Scholar 连打 5 次 = 5/5 次 429，且**无任何兜底源**，
用户拿到的直接就是失败。

> 注意：本组的 429 计数是**用户可见的症状**（输出里是否出现限流提示），
> 不是源端状态。本轮执行期间 OpenAlex 正处于日配额耗尽状态
> （`Retry-After: 49335`，见 openalex_429_character.txt），
> 所以数据源分布会明显偏向 Crossref —— 这正是兜底设计要覆盖的场景。

## E. literature_review

### literature_review -> PASS (18.4s)
```
=== 文献综述分析报告 ===
研究主题：transformer attention mechanism
检索范围：不限 - 至今
分析文献数：5 篇（去重后共 31 篇）
数据来源：Crossref + arXiv

── 一、文献概览 ──

#    年份     引用       标题                                                 来源          
-------------------------------------------------------------------------------------
1    2020   24       Transformer with sparse self‐attention mechanism   Crossref    
2    2024   19       Multiscale Transformer and Attention Mechanism f   Crossref    
3    2025   13       Micro-Expression Recognition Using Convolutional   Crossref    
4    2022   11       LAS-Transformer: An Enhanced Transformer Based o   Crossref    
5    2022   10       Improving Transformer-based Conversational ASR b   Crossref    

```

## 总结

| 组 | 结果 |
|---|---|
| A 检索 | 4/4 |
| B 引用校验 | 7/7 |
| C 故障不误判 | 3/3 |
| D 429 | 用户可见限流 0 次（目标 0），数据源 Crossref×12 |
| E 文献综述 | PASS |
