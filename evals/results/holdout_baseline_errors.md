# 错判明细 — evals\routing_holdout.jsonl

- n=278  准确率=**0.5468**  错判=126

## 混淆对

| gold | pred | n |
|---|---|---|
| `knowledge` | `pm` | 11 |
| `knowledge` | `reasoner` | 11 |
| `pm` | `knowledge` | 10 |
| `chinese` | `knowledge` | 7 |
| `data` | `knowledge` | 7 |
| `security` | `coder` | 6 |
| `reasoner` | `knowledge` | 5 |
| `reasoner` | `pm` | 5 |
| `chinese` | `pm` | 4 |
| `coder` | `pm` | 3 |
| `academic` | `chinese` | 3 |
| `chinese` | `coder` | 3 |
| `vision` | `knowledge` | 3 |
| `data` | `pm` | 3 |
| `devops` | `pm` | 3 |
| `devops` | `coder` | 3 |
| `reasoner` | `academic` | 3 |
| `reasoner` | `coder` | 3 |
| `reasoner` | `data` | 3 |
| `pm` | `chinese` | 3 |
| `coder` | `reasoner` | 2 |
| `coder` | `knowledge` | 2 |
| `coder` | `data` | 2 |
| `security` | `pm` | 2 |
| `security` | `devops` | 2 |
| `coder` | `chinese` | 1 |
| `coder` | `devops` | 1 |
| `academic` | `pm` | 1 |
| `academic` | `reasoner` | 1 |
| `academic` | `data` | 1 |
| `vision` | `pm` | 1 |
| `devops` | `data` | 1 |
| `devops` | `security` | 1 |
| `chinese` | `data` | 1 |
| `data` | `coder` | 1 |
| `chinese` | `security` | 1 |
| `reasoner` | `devops` | 1 |
| `data` | `devops` | 1 |
| `chinese` | `academic` | 1 |
| `reasoner` | `security` | 1 |
| `reasoner` | `vision` | 1 |
| `chinese` | `devops` | 1 |

## 逐条


### `academic`→`chinese` ×3

- [-] 摘要怎么写才能突出贡献
- [-] 摘要里的英文翻译怎么润色
- [short] 写个引言

### `academic`→`data` ×1

- [-] 这个回归方程的系数怎么解释

### `academic`→`pm` ×1

- [-] 同行评审意见要求补实验怎么回

### `academic`→`reasoner` ×1

- [-] 这个定理的证明思路是什么

### `chinese`→`academic` ×1

- [conflict] 翻译并润色这段学术论文的摘要

### `chinese`→`coder` ×3

- [-] 写一个短视频的口播文案
- [conflict] 写一个关于安全漏洞的科普文章
- [conflict] 写一个介绍机器学习的科普文章

### `chinese`→`data` ×1

- [conflict] 帮我写一份项目进展的数据分析报告

### `chinese`→`devops` ×1

- [conflict] 帮我写一份服务器巡检的总结

### `chinese`→`knowledge` ×7

- [-] 帮我把这段话改得更有文采
- [-] 帮我想一个班级晚会的主持串词
- [-] 这个通知的措辞是不是太生硬了
- [-] 帮我起草一份请假条
- [-] 这段话的病句帮我改一下
- [-] 把会议纪要改得通顺一些
- [short] 改下病句

### `chinese`→`pm` ×4

- [-] 这个产品的宣传口号怎么起
- [-] 公众号推文的标题怎么取
- [short] 翻译这句
- [conflict] 把这份会议纪要整理成总结文档

### `chinese`→`security` ×1

- [conflict] 帮我写一份渗透测试的报告模板

### `coder`→`chinese` ×1

- [-] 写个正则表达式匹配手机号

### `coder`→`data` ×2

- [-] PHP 连数据库总是超时是什么原因
- [-] 帮我把 CSV 读进来处理成字典列表

### `coder`→`devops` ×1

- [-] 把这段 shell 命令改成可以传参数的

### `coder`→`knowledge` ×2

- [-] C++ 里 vector 和 list 该用哪个
- [short] 报错 KeyError

### `coder`→`pm` ×3

- [-] Golang 的 goroutine 和 channel 怎么用
- [-] Swift 闭包捕获变量会有什么问题
- [-] 这个 Kotlin 报空指针怎么处理

### `coder`→`reasoner` ×2

- [-] 为什么我的 for 循环跑出来结果不对
- [-] 这个 React 组件为什么重复渲染

### `data`→`coder` ×1

- [conflict] 用 SQL 查询做销售数据的分类统计

### `data`→`devops` ×1

- [conflict] 帮我优化一下数据库查询的性能

### `data`→`knowledge` ×7

- [-] 帮我画一张销售额的折线图
- [-] 特征归一化用哪种方法合适
- [-] 这组样本的分布是不是正态的
- [-] 用 seaborn 画个热力图
- [short] 画个图
- [short] 算下方差
- [short] 画个柱状图

### `data`→`pm` ×3

- [-] 散点图的横纵坐标该怎么选
- [-] 箱线图里离群点怎么判断
- [-] 抽样调查的结果怎么加权

### `devops`→`coder` ×3

- [-] Ansible 批量部署脚本怎么写
- [-] 日志文件太大怎么滚动切割
- [-] Terraform 状态文件被锁住了怎么解锁

### `devops`→`data` ×1

- [-] 写个定时任务每天备份数据库

### `devops`→`pm` ×3

- [-] 怎么给这台机器加一块 swap
- [-] Redis 内存占用持续上涨怎么定位
- [-] 这台云主机的带宽跑满了怎么办

### `devops`→`security` ×1

- [-] 防火墙规则加了但流量还是不通

### `knowledge`→`pm` ×11

- [-] 光合作用的产物是什么
- [-] 恐龙是怎么灭绝的
- [-] 碳酸钠和碳酸氢钠有什么区别
- [-] 蜜蜂是怎么找到花的
- [-] 蛋白质的基本组成单位是什么
- [-] 蝴蝶是怎么从毛毛虫变来的
- [-] 铜生锈生成的物质叫什么
- [-] 罗马帝国是怎么分裂的
- [-] 血液里的红细胞有什么作用
- [-] 洋流对气候有什么影响
- [short] 什么时候开学

### `knowledge`→`reasoner` ×11

- [-] 为什么四季会有交替
- [-] 为什么打雷的时候先看到闪电
- [-] 为什么海水是咸的
- [-] 为什么一天是 24 小时
- [-] 碘酒为什么能消毒
- [-] 为什么人在高处会头晕
- [-] 为什么植物有向光性
- [-] 为什么冬天哈气会变白
- [-] 蜜蜂蛰人之后为什么会死
- [-] 为什么月球总是同一面朝着地球
- [-] 为什么铁在潮湿环境里会生锈

### `pm`→`chinese` ×3

- [-] 这个需求的验收标准怎么写
- [-] 帮我写一份项目周报的要点
- [short] 写个计划

### `pm`→`knowledge` ×10

- [-] 帮我把这个需求拆成几个任务
- [-] 帮我评估一下这个方案的风险
- [-] 帮我整理一下会议要对齐的事项
- [-] 这个任务的依赖关系理不清
- [-] 这个方案的备选路径有哪些
- [-] 帮我梳理一下当前的阻塞项
- [-] 帮我规划一下从零起步的路径
- [-] 复盘会上该问哪几个问题
- [short] 先做哪个
- [short] 帮我规划

### `reasoner`→`academic` ×3

- [-] 帮我算一下这个积分的结果
- [-] 推导一下贝叶斯公式的过程
- [-] 这个方程组的解是什么

### `reasoner`→`coder` ×3

- [-] 求这个函数的导数和极值
- [-] 二分查找的时间复杂度怎么推
- [conflict] 为什么这个函数的复杂度是 O(n²)

### `reasoner`→`data` ×3

- [-] 这道物理题的受力分析怎么做
- [conflict] 为什么这批数据的方差异常偏大
- [conflict] 为什么这次的构建会失败请分析原因

### `reasoner`→`devops` ×1

- [conflict] 这个接口的性能瓶颈怎么定位分析

### `reasoner`→`knowledge` ×5

- [-] 这个数列求和等于多少
- [-] 帮我算算这个概率是多少
- [short] 这题咋算
- [short] 求个极限
- [short] 算这个极限

### `reasoner`→`pm` ×5

- [-] 这道排列组合题怎么做
- [-] 解释一下博弈论里的纳什均衡
- [-] 这道题的辅助线应该怎么作
- [-] 矩阵的特征值怎么求
- [-] 这个递推关系怎么解

### `reasoner`→`security` ×1

- [conflict] 这段推理的逻辑有什么漏洞

### `reasoner`→`vision` ×1

- [conflict] 为什么图片上传接口会超时请分析

### `security`→`coder` ×6

- [-] 帮我做一次代码安全审计
- [-] SQL 注入的参数化查询怎么写
- [-] 帮我检查一下这段代码有没有命令注入
- [-] 代码审计发现硬编码密码怎么改
- [short] 代码安全吗
- [conflict] 这个部署脚本哪里有安全风险

### `security`→`devops` ×2

- [-] 越权访问怎么在服务端拦截
- [-] 日志里的敏感信息要不要脱敏

### `security`→`pm` ×2

- [-] 这个接口的鉴权设计有什么风险
- [-] 暴力破解登录该怎么限流

### `vision`→`knowledge` ×3

- [-] 帮我描述一下这张风景照的内容
- [vision-priority] 图里是谁
- [short] 帮我看看这图

### `vision`→`pm` ×1

- [-] 这张宣传海报的配色怎么样
