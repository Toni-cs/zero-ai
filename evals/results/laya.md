# Laya 决策模型评测

- 评测集: `evals\routing_eval.jsonl`（217 条）
- 变体: `kw` / checkpoint: `multilingual`
- criteria 来源: `EXPERT_TEAM`（未人工编写，避免选项偏向）

## 结果

| 路由方案 | 准确率 |
|---|---|
| 关键词 `route_expert`（现状） | **79.7%** |
| **Laya 零样本** | **45.6%** |
| 差值 | **-34.1pp** |
| 两者一致率 | 43.8% |

## 置信度可信度

- 判对时平均置信度: **0.7133**
- 判错时平均置信度: **0.4723**
- 判错且置信度 ≥0.9 的条数: **9 / 118**

## 置信度门控扫描（Laya 置信度 ≥ τ 用 Laya，否则回落关键词）

| τ | 用 Laya 条数 | 回落条数 | 准确率 |
|---|---|---|---|
| 0.0 | 217 | 0 | 45.6% |
| 0.5 | 127 | 90 | 64.1% |
| 0.6 | 108 | 109 | 68.2% |
| 0.7 | 78 | 139 | 73.7% |
| 0.8 | 59 | 158 | 77.0% |
| 0.9 | 45 | 172 | 77.4% |
| 0.95 | 31 | 186 | 79.7% |
| 0.99 | 11 | 206 | 80.7% |
| 1.01 | 0 | 217 | 79.7% |

**最优门控**: τ=0.99 → **80.7%**（vs 关键词 79.7%，+0.9pp）

## 分 tag 准确率

| tag | Laya | 关键词 |
|---|---|---|
| (常规) | 42% | 80% |
| conflict | 58% | 75% |
| short | 40% | 70% |
| vision-priority | 80% | 87% |

## 混淆对 Top 10（Laya）

- `coder` → `security`  ×9
- `knowledge` → `security`  ×8
- `pm` → `devops`  ×7
- `academic` → `chinese`  ×6
- `knowledge` → `pm`  ×6
- `academic` → `reasoner`  ×5
- `academic` → `coder`  ×5
- `devops` → `security`  ×5
- `chinese` → `coder`  ×4
- `reasoner` → `devops`  ×4

## Laya 判错而关键词判对（可回收的样本）

- [金标 `coder` / Laya `security`(0.30)] debug 一下这个 bug，运行时崩溃了
- [金标 `coder` / Laya `data`(0.15)] 写一个 SQL 查询，按部门统计人数
- [金标 `coder` / Laya `security`(0.30)] HTML 表单提交后页面空白是什么原因
- [金标 `coder` / Laya `security`(0.29)] CSS 怎么让元素垂直居中
- [金标 `coder` / Laya `reasoner`(0.14)] Java 的 HashMap 和 TreeMap 有什么区别
- [金标 `coder` / Laya `security`(0.68)] 读取 config.json 文件并解析
- [金标 `coder` / Laya `devops`(0.45)] 帮我修改这个脚本的配置项
- [金标 `coder` / Laya `security`(0.96)] 删除目录下所有 log 文件
- [金标 `coder` / Laya `security`(0.93)] 搜索项目里所有调用 login 的地方
- [金标 `coder` / Laya `security`(0.65)] 写一个爬虫脚本抓取网页标题
- [金标 `coder` / Laya `chinese`(0.78)] 这段 error 日志是什么意思
- [金标 `coder` / Laya `devops`(0.27)] 帮我实现一个二叉树的遍历
- [金标 `coder` / Laya `devops`(0.28)] 封装一个函数处理文件上传
- [金标 `coder` / Laya `security`(0.46)] js 的闭包是什么，举个例子
- [金标 `coder` / Laya `security`(0.52)] 怎么用 html 做一个登录页
- [金标 `coder` / Laya `reasoner`(0.44)] yaml 配置文件的语法是什么
- [金标 `academic` / Laya `chinese`(0.66)] 帮我写一篇论文的引言部分
- [金标 `academic` / Laya `coder`(0.56)] LaTeX 里怎么插入参考文献
- [金标 `academic` / Laya `devops`(0.13)] 什么是假设检验，怎么用
- [金标 `academic` / Laya `security`(0.91)] 期刊投稿的格式要求是什么
- [金标 `academic` / Laya `reasoner`(0.35)] 怎么求这个积分
- [金标 `academic` / Laya `chinese`(0.85)] 论文摘要怎么写才规范
- [金标 `academic` / Laya `coder`(0.35)] 公式推导到这里卡住了
- [金标 `academic` / Laya `reasoner`(0.51)] 方程组怎么解
- [金标 `academic` / Laya `reasoner`(0.52)] 微分方程的通解怎么求

## Laya 判对而关键词判错（Laya 独有价值）

- [金标 `academic` / 关键词 `pm`] peer review 的意见怎么回复
- [金标 `chinese` / 关键词 `coder`] 写一个产品介绍的文案
- [金标 `chinese` / 关键词 `coder`] 帮我写一个故事开头
- [金标 `chinese` / 关键词 `coder`] 写一个活动策划的文案
- [金标 `vision` / 关键词 `pm`] 图里这个人穿的什么颜色
- [金标 `data` / 关键词 `coder`] 怎么用 sql 查询做数据汇总
- [金标 `security` / 关键词 `coder`] sql注入怎么防御
- [金标 `reasoner` / 关键词 `pm`] 概率论这个题怎么算
- [金标 `reasoner` / 关键词 `knowledge`] 求这个数列的极限
- [金标 `reasoner` / 关键词 `academic`] 线性代数这个方程怎么解
- [金标 `pm` / 关键词 `knowledge`] 帮我规划一下时间安排
- [金标 `pm` / 关键词 `data`] 这个方案可行吗，分析一下
- [金标 `chinese` / 关键词 `data`] 写一篇关于数据可视化的文章

合计: 可回收 87 条，Laya 独有增益 13 条
