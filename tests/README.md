# tests/ —— 测试目录约定

## 为什么所有测试都在这里

2026-09-15 之前，测试文件散落在三处，且根目录一度有 16 个 `test_*.py`
与 `pyproject.toml` / `README.md` 平级。根因是 `pyproject.toml` 里
`testpaths = ["."]`（从仓库根递归收集），于是"新建测试就丢在根目录"成了
默认行为；更糟的是从根收集会连带扫进 `build/`、`zero-ai-repo/` 等目录里的
**同名旧副本**，同一份测试被重复执行。

现已收敛为 `testpaths = ["tests"]`，单一明确目录，不再从根递归。
**新增主包测试请放在本目录。**

## 关于第二个测试目录（已于 2026-10-10 删除）

本目录曾是"两个测试目录"之一，另一个是 `zeroai-tui/tests/`（`zeroai_tui`
C/Zig 加速层，28 项）。2026-10-10 该层整体删除，理由是同场基准实测
**加速比中位 0.9x**（24x80 改动30% = 0.66x、24x80 改动100% = 1.10x、
50x200 改动30% = 0.66x），即不加速反而更慢；且渲染单帧 0.08ms 对 LLM
调用秒级，占总时延 <1%。相应的两个专测文件也一并删除：

- `tests/test_render_benchmark.py`（405 行）
- `tests/test_zig_parallel_memory.py`（534 行）

原记录（保留备查）：2026-09-15 实测该目录单独 28 passed、与 `tests/` 合跑
247 passed（219 + 28）、无 C 扩展时 20 passed / 8 skipped。

`tests/test_expert_config_single_source.py::test_zeroai_tui_layer_is_removed`
现作为**结构锁**钉死这个决定：目录不得复活、包不得重新进入环境。

## 命名约定：按"被测试的能力"命名

此前大量文件用开发阶段代号（`test_agent_g_stage.py`、`test_nopq_stages.py`、
`test_rstu_stages.py`、`test_stage_m.py`、`test_phase3_regression.py`）。
这些名字对没参与当时开发的人（包括三个月后的你自己）完全不可读 ——
"G 阶段"、"RSTU 阶段"不携带任何关于"测什么"的信息。

现有命名规则：`test_<被测模块或能力>.py`，需要时用下划线继续细分。

| 文件名 | 测什么 |
| --- | --- |
| `test_agent_loop_persistence.py` | Agent Loop 优化 + `ThoughtSnapshot` 持久化 |
| `test_agent_loop_advanced.py` | 思维链可视化 / `AdvancedAgentLoop` / RAG 自动检索 |
| `test_app_runtime_smoke.py` | 运行时冒烟：真实挂载 ZeroAI，校验 BINDINGS 与 8 个 mixin 的 action/method 可解析 |
| `test_checkpoint.py` | 检查点保存与恢复 |
| `test_cli_headless.py` | `zeroai --task` 无头模式（本目录内路径推导的参考实现） |
| `test_cost_tracker.py` | token 成本统计 |
| `test_diff_edit.py` | diff 编辑 |
| `test_embedding_rag.py` | Embedding API + 向量存储升级 + RAG 管道 |
| `test_integration_smoke.py` | 核心模块集成冒烟 |
| `test_mcp_ecosystem.py` | MCP 健康监控 / 审计日志 / 生态管理 |
| `test_mcp_e2e.py` | MCP 端到端：起 Server 子进程 + Client 连接调用工具 |
| `test_mcp_protocol.py` | MCP 协议层：JSON-RPC 编解码 / 配置 / 客户端 / 注册 / 服务器 |
| `test_react_agent.py` | ReAct Agent 与向量记忆 |
| `test_regression_tui_agent_wiring.py` | 工具接线回归：`TOOLS`/`TOOL_MAP` 与 registry 单一真源一致、签名与真实调用（原为 `tui_agent.py` 校验，该文件已删除，改指 `zeroai`） |
| `test_release_readiness.py` | 发布前全面体检（版本号 / 入口 / 模块导入 / 工具注册一致性） |
| `test_render_benchmark.py` | Zig / C / Python 三层渲染性能对比 |
| `test_render_benchmark_streaming.py` | 流式文本拼接的性能对比 |
| `test_sandbox_multiagent_stream.py` | 代码沙箱 + 多 Agent 协作 + 流式思维链 + 代码知识图谱 |
| `test_session.py` | 会话管理 |
| `test_task_manager.py` | 任务管理 |
| `test_tool_call_xml.py` | `<tool_call>` 伪 XML 解析 |
| `test_tools_registry_contract.py` | 工具可调用性 + schema 与函数签名的一致性 |
| `test_tui_markdown.py` | `zeroai.tui.markdown` 聚合模块 |
| `test_tui_source_budget.py` | 源码清单完整性 + 单文件 800 行复杂度预算（防止 ZeroAI 巨类复发） |
| `test_zig_parallel_memory.py` | Zig 加速层 / 工具调用并行化 / 内存与性能优化 |

**共享机制：`tests/tui_sources.py`（不是测试文件）**

它维护「哪些文件承载 ZeroAI」的清单（zeroai/tui/app.py + app_*.py），凡是读源码做字符串/AST 断言的测试都引用它。
**不要硬编码单个 `app.py`**：2026-10-06 把 3,664 行的 `ZeroAI` 拆成 8 个 mixin 时，
6 条硬编码测试直接误报，2 条扫描类测试则静默失去覆盖（绿着，但新文件没人扫了）。
清单的权威判定是解析 `ZeroAI` 类头的继承表，完整性由 `test_tui_source_budget.py` 把守。

## 路径推导的坑（重要）

本目录下的测试**不能**用 `os.path.dirname(os.path.abspath(__file__))` 当项目根 ——
它指的是 `tests/`，不是仓库根。正确写法（见 `test_cli_headless.py:25`）：

```python
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
```

2026-09-15 移动这些文件时，有 12 个文件因为用了旧的单层 `dirname` 而失效，
必须逐个上溯一级。

## 运行

```bash
pytest                                   # 走 testpaths（即 tests/），全量
pytest tests/                            # 同上，显式指定
pytest tests/test_mcp_e2e.py -v
python tests/test_release_readiness.py   # 部分文件保留了 __main__ 直跑入口
```

注意：部分测试依赖可选组件（FAISS、MCP 预设依赖等）。这类测试在组件缺失时
应**降级跳过**而不是失败；若你看到 `SKIP` 而非 `FAILED`，通常不是回归。
