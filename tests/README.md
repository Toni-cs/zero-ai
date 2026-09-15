# tests/ —— 测试目录约定

## 为什么所有测试都在这里

2026-09-15 之前，测试文件散落在三处，且根目录一度有 16 个 `test_*.py`
与 `pyproject.toml` / `README.md` 平级。根因是 `pyproject.toml` 里
`testpaths = ["."]`（从仓库根递归收集），于是"新建测试就丢在根目录"成了
默认行为；更糟的是从根收集会连带扫进 `build/`、`zero-ai-repo/` 等目录里的
**同名旧副本**，同一份测试被重复执行。

现已收敛为 `testpaths = ["tests"]`。**新增测试请放在本目录。**

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
| `test_regression_tui_agent_wiring.py` | 回归：`tui_agent.py` 内部调用已切换到 `zeroai` 包 |
| `test_release_readiness.py` | 发布前全面体检（版本号 / 入口 / 模块导入 / 工具注册一致性） |
| `test_render_benchmark.py` | Zig / C / Python 三层渲染性能对比 |
| `test_render_benchmark_streaming.py` | 流式文本拼接的性能对比 |
| `test_sandbox_multiagent_stream.py` | 代码沙箱 + 多 Agent 协作 + 流式思维链 + 代码知识图谱 |
| `test_session.py` | 会话管理 |
| `test_task_manager.py` | 任务管理 |
| `test_tool_call_xml.py` | `<tool_call>` 伪 XML 解析 |
| `test_tools_registry_contract.py` | 工具可调用性 + schema 与函数签名的一致性 |
| `test_tui_markdown.py` | `zeroai.tui.markdown` 聚合模块 |
| `test_zig_parallel_memory.py` | Zig 加速层 / 工具调用并行化 / 内存与性能优化 |

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
pytest                      # 走 pyproject.toml 的 testpaths，只收集本目录
pytest tests/test_mcp_e2e.py -v
python tests/test_release_readiness.py   # 部分文件保留了 __main__ 直跑入口
```

注意：部分测试依赖可选组件（`zeroai-tui` 的 C/Zig 扩展、FAISS、MCP 预设
依赖等）。这类测试在组件缺失时应**降级跳过**而不是失败；若你看到
`SKIP` 而非 `FAILED`，通常不是回归。

## 已知未纳入的测试

`zeroai-tui/tests/`（5 个文件）测的是 C/Zig 加速层，依赖 `build.zig` 产物，
未纳入 `testpaths`。需要时显式指定：

```bash
pytest zeroai-tui/tests/
```
