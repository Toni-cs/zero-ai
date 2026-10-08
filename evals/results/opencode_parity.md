# OpenCode V2 ↔ zero-ai 功能对齐差距分析（修正版）

> 首版报告给出「25/31 有证据」，**经实现级核验后修正为 22/31**。
> 首版的误判过程与逐条核验保留在
> `parity_spotcheck.txt` / `parity_verify.txt`，不只留修正后的结论。

- 基准清单：https://opencode.ai/v2/llms.txt（OpenCode V2 官方文档索引）
- 扫描范围：`zeroai/` + `zeroai-tui/`，115 个文件
- **两阶段判定**：先用正则找线索，再对可疑项读代码确认

## 方法上的教训（先说这个）

首版只用关键词正则，**只在 docstring 里声明了"可能漏判"，没考虑误判**。
实际抓到三类假阳性：

| 误判项 | 正则命中了什么 | 实质 |
|---|---|---|
| Formatters | `argparse.RawDescriptionHelpFormatter`、`get_blackboard`（含 `black`） | 与代码格式化毫无关系 |
| Commands | LaTeX 的 `r"\backslash"` —— **含子串 `slash`** | 子串匹配的经典陷阱 |
| References | doc_gen 的「参考文献」标签、依赖图的 `references:` 字段 | 不是 @file 引用注入 |
| Sharing | 中文注释里的「分享」、无关的 `share_` 函数（27 个文件！） | 正则过宽 |

**命中数越多越可疑** —— Sharing 命中 27 个文件时就该起疑。
这与路由那边「Laya +0.9pp 被交叉验证打回」是同一类问题：
工具本身也会制造假信号，必须反查。

## 结论：31 项中 **22 项有确凿证据，9 项确认缺失**

### ❌ 确认缺失（9 项，已逐个读代码确认）

| 功能 | 属于 | 说明 |
|---|---|---|
| **Skills** | Configure | 可加载的技能定义（全仓 0 命中，仅测试文件与文档出现该词） |
| **Plugins** | Configure | 插件系统（全仓 0 命中） |
| **Plugin API** | Build | 插件开发接口（随 Plugins 一起缺失） |
| **Formatters** | Configure | 代码格式化器 —— 未发现任何 prettier/black/clang-format/ruff 调用 |
| **References** | Configure | @file 式引用注入 —— 无引用解析实现 |
| **Sharing** | Configure | 会话导出为可分享链接 —— 无分享实现 |
| **Warming** | Configure | 预热/预取（全仓 0 命中） |
| **ACP** | CLI | Agent Client Protocol（全仓 0 命中） |
| **JS/TS Client** | Build | 类型化客户端 SDK（全仓无 .ts/.tsx 文件） |

### ⚠️ 部分成立（1 项）

| 功能 | 实况 |
|---|---|
| **Web UI** | 命中的是 `zeroai/mcp/server.py` 的 FastAPI/SSE —— 这是 **MCP 服务端**，不是给用户用的浏览器界面。OpenCode 的 Web 是 TUI 之外的第二个操作界面 |

### ✅ 有确凿证据（22 项）

| 功能 | 关键证据 |
|---|---|
| Agents | `EXPERT_TEAM` 10 专家 + 三种工作模式 |
| Models / Providers | `MODEL_CONFIGS` 5 提供方（glm/glm-4/glm-v/ollama/openrouter） |
| Themes | TUI 主题配置 |
| Commands | `zeroai/tui/app_commands.py`（25878 字节）有分发实现 |
| Websearch | `tools/network.py` Bing → DuckDuckGo → 百度 |
| Network | 代理配置、`_load_proxy_config` 等 |
| **Snapshots** | **`zeroai/core/checkpoint.py` 存在** + `enable_checkpoint` 开关 |
| Compaction | `compress_if_needed` / `COMPRESS_THRESHOLD` 上下文压缩 |
| Attachments | `screens.py` 的 `_add_pending_image` 图片附件队列 |
| Tools | `tools/registry.py` 工具注册表 |
| MCP servers | `zeroai/mcp/`（protocol/client/server/health） |
| Permissions | `allowed_commands` / `blocked_commands` 白黑名单 |
| **Policies** | `sandbox.py` 安全策略 + `cli.py` **fail-closed diff 审批** |
| Instructions | 10 专家各自的 `system_prompt` |
| TUI | textual 终端界面 |
| Settings | `config.yaml` + `get_config` |
| CLI commands | `argparse` 子命令 |
| Keybinds | TUI `BINDINGS` |
| HTTP API | `zeroai/mcp/server.py` SSE 端点 |
| SDK (embed) | `zeroai/core/` 可作为库引入（`expert.py`/`llm.py`） |
| References | ⚠️ 见上，此项实为误判，已从 ✅ 移入 ❌ |

### 不计入差距（商业 SaaS 范畴）

Console / Workspaces / Members / SSO / SCIM / Budgets / Billing /
Cloud inference API / Go console —— 面向团队云服务，与本地 CLI 工具
不是同一类能力，纳入只会稀释目标。

## 补齐优先级（按「评审价值 ÷ 实现成本」）

| 优先级 | 项 | 理由 |
|---|---|---|
| **P0** | **Skills** | 成本最低（本质是可加载的 Markdown 指令 + 发现机制），且直接支撑"可扩展架构"叙事；对后续数字人教学的课程/教案注入也是同一套机制 |
| **P0** | **Formatters** | 成本低（生成代码后调一次 ruff/black），但堵住"生成的代码没人管格式"这个一试就露的短板 |
| **P1** | **References（@file）** | 成本中，日常使用频次高，体验提升明显 |
| **P1** | **Plugins + Plugin API** | 成本高，但**是"可扩展架构"最硬的证据**；评审问"怎么加新能力"时，插件机制是标准答案 |
| **P2** | Sharing | 成本中低，但对本地 CLI 价值有限 |
| **P2** | JS/TS Client | 成本高；已有 HTTP API，收益边际 |
| **P3** | Warming / ACP | 收益低，ACP 是与其他编辑器互操作的协议，与你的目标无关 |

**不建议追的**：Console 系列（SSO/SCIM/billing）——那是商业 SaaS，
不是本地工具该有的东西，补了反而显得目标不清。
