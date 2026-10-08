# 路由变体对比（dev 池 = dev + holdout，495 条）

> **routing_test.jsonl 未参与本表任何计算**（哈希已校验）。

## 指标含义

- `l1_acc`      L1 关键词路由单独的准确率，与历史 79.7% 可比
- `defer_rate`  L1 判为 knowledge、交给 L2 GLM 的比例（= 延迟与 API 成本）
- `unsafe_rate` **用户实际收到错误答案**的比例。长输入下 defer 到 knowledge 会被 L2 纠正，不算错误；短句 defer 与短路到错专家都算
- `wrong_shortcircuit` 短路到错误专家的条数（最严重的一类）

## 结果

| 变体 | l1_acc | defer_rate | **unsafe_rate** | 短路到错专家 | 短句defer无纠正 |
|---|---|---|---|---|---|
| V0 | 0.6566 | 0.1374 | **0.2909** | 130 | 14 |
| V1 | 0.6828 | 0.1374 | **0.2646** | 117 | 14 |
| V2 | 0.6848 | 0.1596 | **0.2525** | 110 | 15 |
| V3 | 0.6727 | 0.2667 | **0.2000** | 74 | 25 |
| V4 | 0.7596 | 0.1798 | **0.1818** | 74 | 16 |
| V5a | 0.7697 | 0.2384 | **0.1455** | 55 | 17 |
| V5b | 0.7697 | 0.2384 | **0.1455** | 55 | 17 |
| V5c | 0.7596 | 0.1798 | **0.1818** | 74 | 16 |
| V6 | 0.7838 | 0.2384 | **0.1313** | 48 | 17 |
| V6live | 0.7818 | 0.2444 | **0.1273** | 46 | 17 |

## 相对 V0（现状）的变化

| 变体 | Δl1_acc | Δunsafe_rate | Δ短路到错专家 |
|---|---|---|---|
| V0 | +0.0000 | +0.0000 | +0 |
| V1 | +0.0262 | -0.0263 | -13 |
| V2 | +0.0282 | -0.0384 | -20 |
| V3 | +0.0161 | -0.0909 | -56 |
| V4 | +0.1030 | -0.1091 | -56 |
| V5a | +0.1131 | -0.1454 | -75 |
| V5b | +0.1131 | -0.1454 | -75 |
| V5c | +0.1030 | -0.1091 | -56 |
| V6 | +0.1272 | -0.1596 | -82 |
| V6live | +0.1252 | -0.1636 | -84 |

## 分集合（检查是否只在某一个集合上过拟合）

| 变体 | dev(217) l1_acc | holdout(278) l1_acc | 两集合差 |
|---|---|---|---|
| V0 | 0.7972 | 0.5468 | +0.2504 |
| V1 | 0.8203 | 0.5755 | +0.2448 |
| V2 | 0.8157 | 0.5827 | +0.2330 |
| V3 | 0.7880 | 0.5827 | +0.2053 |
| V4 | 0.8157 | 0.7158 | +0.0999 |
| V5a | 0.8295 | 0.7230 | +0.1065 |
| V5b | 0.8295 | 0.7230 | +0.1065 |
| V5c | 0.8157 | 0.7158 | +0.0999 |
| V6 | 0.8433 | 0.7374 | +0.1059 |
| V6live | 0.8433 | 0.7338 | +0.1095 |

## unsafe_rate 最低的变体：V6live

### 混淆对

| gold | pred | n |
|---|---|---|
| `pm` | `knowledge` | 10 |
| `reasoner` | `knowledge` | 5 |
| `chinese` | `pm` | 5 |
| `security` | `coder` | 4 |
| `coder` | `data` | 3 |
| `academic` | `reasoner` | 3 |
| `reasoner` | `data` | 3 |
| `academic` | `data` | 2 |
| `chinese` | `academic` | 2 |
| `reasoner` | `academic` | 2 |
| `pm` | `data` | 2 |
| `chinese` | `data` | 2 |
| `data` | `devops` | 2 |
| `vision` | `knowledge` | 2 |
| `reasoner` | `coder` | 2 |
| `data` | `vision` | 1 |
| `reasoner` | `security` | 1 |
| `pm` | `devops` | 1 |
| `coder` | `chinese` | 1 |
| `coder` | `devops` | 1 |

### 仍短路到错专家的样本（前 40 条）

- [`coder`→`data`] 写一个 SQL 查询，按部门统计人数
- [`academic`→`data`] 回归分析的结果怎么解读
- [`academic`→`reasoner`] 怎么求这个积分
- [`academic`→`reasoner`] 方程组怎么解
- [`academic`→`reasoner`] 求这个定理的数学证明
- [`chinese`→`academic`] 这篇摘要太啰嗦了，帮我精简
- [`data`→`vision`] 这张图表怎么画得更清楚
- [`reasoner`→`data`] 复杂度分析怎么做
- [`reasoner`→`academic`] 线性代数这个方程怎么解
- [`reasoner`→`security`] 这个论证有没有逻辑漏洞
- [`pm`→`devops`] 解释一下什么是微服务
- [`pm`→`data`] 这个方案可行吗，分析一下
- [`pm`→`data`] 怎么入门机器学习
- [`chinese`→`data`] 写一篇关于数据可视化的文章
- [`coder`→`data`] 写一个数据清洗的脚本
- [`data`→`devops`] 怎么优化数据库查询的性能
- [`chinese`→`pm`] 翻译你好
- [`coder`→`chinese`] 写个正则表达式匹配手机号
- [`coder`→`devops`] 把这段 shell 命令改成可以传参数的
- [`coder`→`data`] 帮我把 CSV 读进来处理成字典列表
- [`academic`→`data`] 这个回归方程的系数怎么解释
- [`chinese`→`pm`] 年终总结的开头怎么写
- [`chinese`→`pm`] 翻译这句
- [`devops`→`data`] 写个定时任务每天备份数据库
- [`devops`→`security`] 防火墙规则加了但流量还是不通
- [`devops`→`coder`] 日志文件太大怎么滚动切割
- [`security`→`coder`] 帮我检查一下这段代码有没有命令注入
- [`security`→`coder`] 代码审计发现硬编码密码怎么改
- [`security`→`coder`] 代码安全吗
- [`reasoner`→`academic`] 推导一下贝叶斯公式的过程
- [`reasoner`→`coder`] 求这个函数的导数和极值
- [`reasoner`→`coder`] 二分查找的时间复杂度怎么推
- [`reasoner`→`data`] 这道物理题的受力分析怎么做
- [`pm`→`chinese`] 帮我写一份项目周报的要点
- [`chinese`→`data`] 帮我写一份项目进展的数据分析报告
- [`chinese`→`security`] 写一个关于安全漏洞的科普文章
- [`reasoner`→`data`] 为什么这批数据的方差异常偏大
- [`chinese`→`pm`] 把这份会议纪要整理成总结文档
- [`security`→`devops`] Docker 容器的安全隔离怎么做
- [`reasoner`→`devops`] 这个接口的性能瓶颈怎么定位分析
