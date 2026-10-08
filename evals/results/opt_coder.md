# coder 泛词贪心后向剔除

- 评测集: `evals\routing_eval.jsonl`（217 条）
- 基线（不删任何词）: **75.1%**
- 最优剔除集合: **79.7%**（+4.6pp）
- 全删 18 个（对照）: **78.3%**（+3.2pp）

## 贪心过程

| 轮次 | 剔除的词 | 准确率 | 错判数 |
|---|---|---|---|
| 0 | （基线） | 75.1% | 54 |
| 1 | 配置 | 77.4% | 49 |
| 2 | 配置、看看 | 78.3% | 47 |
| 3 | 配置、看看、查看 | 78.8% | 46 |
| 4 | 配置、看看、查看、读取 | 79.3% | 45 |
| 5 | 配置、看看、查看、读取、项目 | 79.7% | 44 |

## 决策

| 方案 | 剔除词数 | 准确率 | Δ |
|---|---|---|---|
| 什么都不删（现状） | 0 | 75.1% | — |
| **贪心最优** | 5 | **79.7%** | **+4.6pp** |
| 全删 18 个 | 18 | 78.3% | +3.2pp |

**最终剔除的词**：配置、看看、查看、读取、项目

**保留的词**：文件、读文件、目录、打开、浏览、修改、编辑、写入、创建、删除、搜索、查找、仓库

## 最终方案下的剩余错判（Top 15）

- `chinese -> coder`  ×5
- `knowledge -> pm`  ×5
- `knowledge -> reasoner`  ×4
- `academic -> reasoner`  ×3
- `pm -> data`  ×3
- `academic -> data`  ×2
- `security -> coder`  ×2
- `reasoner -> data`  ×2
- `reasoner -> knowledge`  ×2
- `academic -> knowledge`  ×1
- `academic -> pm`  ×1
- `chinese -> knowledge`  ×1
- `vision -> knowledge`  ×1
- `vision -> pm`  ×1
- `data -> coder`  ×1

## 错判样本全量

- [academic→reasoner] 帮我推导这个公式的证明
- [academic→data] 回归分析的结果怎么解读
- [academic→knowledge] 帮我设计一个实验方案
- [academic→reasoner] 定理证明的步骤是什么
- [academic→data] 帮我分析这篇 paper 的方法论
- [academic→pm] peer review 的意见怎么回复
- [academic→reasoner] 求这个定理的数学证明
- [chinese→coder] 写一个产品介绍的文案
- [chinese→coder] 帮我写一个故事开头
- [chinese→coder] 写一个通知的公文
- [chinese→coder] 帮我写一个自我介绍
- [chinese→coder] 写一个活动策划的文案
- [chinese→knowledge] 帮我起草一份合作意向书
- [vision→knowledge] 帮我看看这个海报的设计
- [vision→pm] 图里这个人穿的什么颜色
- [data→coder] 怎么用 sql 查询做数据汇总
- [data→vision] 这张图表怎么画得更清楚
- [security→coder] sql注入怎么防御
- [security→coder] 帮我做代码安全审计
- [reasoner→data] 为什么会出现这个现象，分析原因
- [reasoner→data] 复杂度分析怎么做
- [reasoner→pm] 概率论这个题怎么算
- [reasoner→knowledge] 求这个数列的极限
- [reasoner→academic] 线性代数这个方程怎么解
- [reasoner→security] 这个论证有没有逻辑漏洞
- [pm→data] 帮我分析一下这个任务
- [pm→devops] 解释一下什么是微服务
- [pm→knowledge] 帮我规划一下时间安排
- [pm→data] 这个方案可行吗，分析一下
- [pm→data] 怎么入门机器学习
- [knowledge→pm] 什么时候开始下雪
- [knowledge→pm] 京剧是怎么来的
- [knowledge→reasoner] 海王星为什么是蓝色的
- [knowledge→reasoner] 为什么天空是蓝色的
- [knowledge→pm] 碳酸钙的化学式是什么
- [knowledge→reasoner] 为什么一年有 365 天
- [knowledge→reasoner] 蚂蚁为什么能搬动比自己重的东西
- [knowledge→pm] 古罗马用什么语言
- [chinese→data] 写一篇关于数据可视化的文章
- [data→devops] 怎么优化数据库查询的性能
- [reasoner→coder] 为什么这个函数运行很慢，帮我分析原因
- [reasoner→knowledge] 1+1等于几
- [knowledge→pm] 什么是量子
- [chinese→pm] 翻译你好
