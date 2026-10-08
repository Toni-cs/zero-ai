# EXPERT_TEAM 关键词（constants，TUI 实际生效的数据源）

## pm  (10)
帮助 分析 计划 总结 翻译 什么 怎么 如何 介绍 解释

## coder  (38)
代码 编程 函数 bug error python js java css html sql api 写一个 实现 debug code function class 脚本 文件 读文件 目录 打开 浏览 修改 编辑 写入 创建 删除 搜索 查找 仓库 json yaml xml toml ini env

## reasoner  (14)
推理 证明 数学 计算 逻辑 为什么 分析原因 算法 复杂 优化 证明 solve math reason

## knowledge  (0)


## chinese  (13)
写 作文 文章 报告 文案 论文 小说 故事 邮件 摘要 润色 写作 中文

## vision  (15)
图片 截图 看图 看这张 看这张图 识别图 图像 png jpg jpeg gif bmp 视觉 这张图 这张照片

## academic  (27)
论文 学术 文献 综述 公式 推导 定理 证明 latex 方程 积分 微分 引用 参考文献 期刊 投稿 研究方法 实验设计 假设检验 回归分析 paper research formula equation theorem proof citation

## devops  (30)
运维 部署 服务器 linux windows docker 容器 kubernetes k8s ssh shell bash powershell nginx apache systemd 服务 进程 端口 防火墙 监控 日志 性能 调优 devops ci/cd jenkins ansible terraform 负载均衡

## security  (31)
安全 漏洞 攻击 防护 加固 审计 渗透 xss sql注入 csrf ssrf rce 漏洞 cve 加密 证书 ssl tls 密钥 token 权限 认证 授权 owasp waf 防火墙规则 入侵检测 security vulnerability pentest hardening

## data  (29)
数据 分析 统计 可视化 图表 pandas numpy matplotlib 数据清洗 数据预处理 特征工程 机器学习 数据挖掘 报表 数据看板 excel csv json sql查询 数据分析 dataframe 数据分析 bi 数据分析 回归 聚类 分类 data analytics

# 路由固定优先级（expert_route.route_expert）
ision -> coder -> security -> devops -> data -> reasoner -> academic -> chinese -> pm -> knowledge(兜底)

# config.yaml experts 段（漂移对照）
`
{
  "pm": {
    "label": "项目经理·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "任务分析·调度·通用问答（GLM-4.7-Flash·128K上下文）",
    "keywords": [
      "帮助",
      "分析",
      "计划",
      "总结",
      "翻译",
      "什么",
      "怎么",
      "如何",
      "介绍",
      "解释"
    ],
    "system_prompt": "你是 ZeroAI 的项目经理，负责任务分析、计划制定、跨领域调度。用中文回答，简洁明了。如用户发送图片，请理解图片内容并纳入分析。"
  },
  "coder": {
    "label": "编程·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "代码生成·调试·重构（GLM-4.7-Flash·智谱直供·免费无限）",
    "keywords": [
      "代码",
      "编程",
      "函数",
      "bug",
      "error",
      "python",
      "js",
      "java",
      "css",
      "html",
      "sql",
      "api",
      "写一个",
      "实现",
      "debug",
      "code",
      "function",
      "class",
      "脚本"
    ],
    "system_prompt": "你是 ZeroAI 的编程专家，专精代码生成、调试、重构、架构设计。直接给出可运行的代码，必要时简短说明思路。你是 ZeroAI，不是其他模型。"
  },
  "reasoner": {
    "label": "推理·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "深度推理·数学·逻辑（GLM-4.7-Flash·智谱直供·免费无限）",
    "keywords": [
      "推理",
      "证明",
      "数学",
      "计算",
      "逻辑",
      "为什么",
      "分析原因",
      "算法",
      "复杂",
      "优化",
      "证明",
      "solve",
      "math",
      "reason"
    ],
    "system_prompt": "你是 ZeroAI 的推理专家，专精深度推理、数学证明、复杂逻辑分析。给出严谨的推理过程和结论。你是 ZeroAI，不是其他模型。"
  },
  "academic": {
    "label": "学术·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "学术论文·文献综述·研究方法·LaTeX",
    "keywords": [
      "论文",
      "文献",
      "学术",
      "研究",
      "综述",
      "引用",
      "期刊",
      "会议",
      "arxiv",
      "paper",
      "academic",
      "research",
      "review",
      "LaTeX",
      "公式",
      "定理",
      "证明"
    ],
    "system_prompt": "你是 ZeroAI 的学术专家，专精论文写作、文献综述、研究方法、LaTeX公式。遵循学术规范，给出严谨的学术内容。你是 ZeroAI，不是其他模型。"
  },
  "chinese": {
    "label": "中文·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "中文写作·文章·报告·文案·邮件",
    "keywords": [
      "中文",
      "写作",
      "文章",
      "报告",
      "文案",
      "邮件",
      "总结",
      "翻译",
      "润色",
      "修改",
      "作文",
      "写作"
    ],
    "system_prompt": "你是 ZeroAI 的中文写作专家，专精中文文章、报告、文案、邮件写作。给出流畅、地道的中文内容。你是 ZeroAI，不是其他模型。"
  },
  "vision": {
    "label": "视觉·GLM-4V",
    "model_key": "glm-v",
    "model": "glm-4v-flash",
    "desc": "图片理解·截图分析·视觉",
    "keywords": [
      "图片",
      "截图",
      "视觉",
      "看",
      "图像",
      "照片",
      "图像",
      "visual",
      "image",
      "screenshot"
    ],
    "system_prompt": "你是 ZeroAI 的视觉专家，专精图片理解、截图分析、视觉内容解读。仔细观察图片内容，给出准确的描述和分析。你是 ZeroAI，不是其他模型。"
  },
  "knowledge": {
    "label": "知识·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "百科知识·事实查询·翻译·其他",
    "keywords": [
      "知识",
      "百科",
      "事实",
      "历史",
      "地理",
      "科学",
      "常识",
      "是什么",
      "谁是",
      "什么时候",
      "哪里"
    ],
    "system_prompt": "你是 ZeroAI 的知识专家，专精百科知识、事实查询、常识解答。给出准确、简洁的知识性回答。你是 ZeroAI，不是其他模型。"
  },
  "devops": {
    "label": "运维·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "DevOps运维·系统管理·SSH·容器·部署·监控",
    "keywords": [
      "运维",
      "部署",
      "服务器",
      "linux",
      "windows",
      "docker",
      "容器",
      "kubernetes",
      "k8s",
      "ssh",
      "shell",
      "bash",
      "powershell",
      "nginx",
      "apache",
      "systemd",
      "服务",
      "进程",
      "端口",
      "防火墙",
      "监控",
      "日志",
      "性能",
      "调优",
      "devops",
      "ci/cd",
      "jenkins",
      "ansible",
      "terraform",
      "负载均衡"
    ],
    "system_prompt": "你是 ZeroAI 的运维专家，专精 DevOps、系统管理、容器编排、CI/CD、监控告警、性能调优。给出可执行的命令和配置，必要时说明原理。优先使用项目内置的运维工具（local_port_check/local_process_check/local_disk_check/local_service_check/local_firewall_check/ssh_*）而非直接给命令。你是 ZeroAI，不是其他模型。"
  },
  "security": {
    "label": "安全·GLM-4.7",
    "model_key": "glm",
    "model": "glm-4.7-flash",
    "desc": "安全分析·漏洞评估·加固方案·安全审计",
    "keywords": [
      "安全"
`
