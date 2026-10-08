# 路由错判全量导出

- 评测集: `evals\routing_eval.jsonl`（217 条）
- 当前准确率: **79.7%**（对 173 条，错 44 条）

## 按混淆对分组

| gold | pred | 条数 |
|---|---|---|
| `chinese` | `coder` | 5 |
| `knowledge` | `pm` | 5 |
| `knowledge` | `reasoner` | 4 |
| `academic` | `reasoner` | 3 |
| `pm` | `data` | 3 |
| `academic` | `data` | 2 |
| `security` | `coder` | 2 |
| `reasoner` | `data` | 2 |
| `reasoner` | `knowledge` | 2 |
| `academic` | `knowledge` | 1 |
| `academic` | `pm` | 1 |
| `chinese` | `knowledge` | 1 |
| `vision` | `knowledge` | 1 |
| `vision` | `pm` | 1 |
| `data` | `coder` | 1 |
| `data` | `vision` | 1 |
| `reasoner` | `pm` | 1 |
| `reasoner` | `academic` | 1 |
| `reasoner` | `security` | 1 |
| `pm` | `devops` | 1 |
| `pm` | `knowledge` | 1 |
| `chinese` | `data` | 1 |
| `data` | `devops` | 1 |
| `reasoner` | `coder` | 1 |
| `chinese` | `pm` | 1 |

## 按 gold 分组（哪些专家最常被漏掉）

| gold | 错判条数 |
|---|---|
| `knowledge` | 9 / 23 |
| `chinese` | 8 / 22 |
| `reasoner` | 8 / 22 |
| `academic` | 7 / 29 |
| `pm` | 5 / 20 |
| `data` | 3 / 23 |
| `vision` | 2 / 17 |
| `security` | 2 / 17 |

## 逐条明细


### `academic` → `data`  ×2

- [-] (len=11) 回归分析的结果怎么解读
- [-] (len=17) 帮我分析这篇 paper 的方法论

### `academic` → `knowledge`  ×1

- [-] (len=10) 帮我设计一个实验方案

### `academic` → `pm`  ×1

- [-] (len=19) peer review 的意见怎么回复

### `academic` → `reasoner`  ×3

- [-] (len=11) 帮我推导这个公式的证明
- [-] (len=10) 定理证明的步骤是什么
- [-] (len=10) 求这个定理的数学证明

### `chinese` → `coder`  ×5

- [-] (len=10) 写一个产品介绍的文案
- [-] (len=9) 帮我写一个故事开头
- [-] (len=8) 写一个通知的公文
- [-] (len=9) 帮我写一个自我介绍
- [-] (len=10) 写一个活动策划的文案

### `chinese` → `data`  ×1

- [conflict] (len=13) 写一篇关于数据可视化的文章

### `chinese` → `knowledge`  ×1

- [-] (len=11) 帮我起草一份合作意向书

### `chinese` → `pm`  ×1

- [short] (len=4) 翻译你好

### `data` → `coder`  ×1

- [-] (len=15) 怎么用 sql 查询做数据汇总

### `data` → `devops`  ×1

- [conflict] (len=12) 怎么优化数据库查询的性能

### `data` → `vision`  ×1

- [-] (len=11) 这张图表怎么画得更清楚

### `knowledge` → `pm`  ×5

- [-] (len=8) 什么时候开始下雪
- [-] (len=7) 京剧是怎么来的
- [-] (len=10) 碳酸钙的化学式是什么
- [-] (len=8) 古罗马用什么语言
- [short] (len=5) 什么是量子

### `knowledge` → `reasoner`  ×4

- [-] (len=10) 海王星为什么是蓝色的
- [-] (len=9) 为什么天空是蓝色的
- [-] (len=12) 为什么一年有 365 天
- [-] (len=15) 蚂蚁为什么能搬动比自己重的东西

### `pm` → `data`  ×3

- [-] (len=10) 帮我分析一下这个任务
- [-] (len=12) 这个方案可行吗，分析一下
- [-] (len=8) 怎么入门机器学习

### `pm` → `devops`  ×1

- [-] (len=10) 解释一下什么是微服务

### `pm` → `knowledge`  ×1

- [-] (len=10) 帮我规划一下时间安排

### `reasoner` → `academic`  ×1

- [-] (len=11) 线性代数这个方程怎么解

### `reasoner` → `coder`  ×1

- [conflict] (len=18) 为什么这个函数运行很慢，帮我分析原因

### `reasoner` → `data`  ×2

- [-] (len=15) 为什么会出现这个现象，分析原因
- [-] (len=8) 复杂度分析怎么做

### `reasoner` → `knowledge`  ×2

- [-] (len=8) 求这个数列的极限
- [short] (len=6) 1+1等于几

### `reasoner` → `pm`  ×1

- [-] (len=9) 概率论这个题怎么算

### `reasoner` → `security`  ×1

- [-] (len=11) 这个论证有没有逻辑漏洞

### `security` → `coder`  ×2

- [-] (len=9) sql注入怎么防御
- [-] (len=9) 帮我做代码安全审计

### `vision` → `knowledge`  ×1

- [vision-priority] (len=11) 帮我看看这个海报的设计

### `vision` → `pm`  ×1

- [vision-priority] (len=11) 图里这个人穿的什么颜色
