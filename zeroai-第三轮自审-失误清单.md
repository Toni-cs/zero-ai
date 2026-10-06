# zeroai 第三轮对抗式自审 —— "还有什么失误"

**日期**：2026-09-16
**对象**：`zero-ai-cli` 1.1.6（`D:\C\C`）
**方法**：对**上一轮自己宣称的"已修复"结论**做对抗式复核；每条结论先写"能失败的最小复现"
**结果**：**推翻上一轮 1 项核心断言**，新发现 3 项缺陷，同时**撤回我自己 2 项误报**

---

## 一、最重要的结论：我上一轮说错了

上一轮我宣称"已修复语音对话框气泡渲染"。

**这个结论不可信。** 我的验证方式是**直接调用方法**：

```python
screen._append_user_bubble("hi")     # 断言气泡节点出现 → PASS
```

这绕过了真正坏掉的那层管线。实测：

```python
hasattr(VoiceDialogScreen_instance, "call_from_thread")   # → False
```

`call_from_thread` **只存在于 `App`**。`Screen` / `Widget` / `MessagePump` 的 MRO 里都没有。
而 `VoiceDialogScreen` 类内 **52 处**都在调用 `self.call_from_thread(...)`，
且这些调用全部位于工作线程（`_handle_text` / `_dialog_loop` 跑在裸 `threading.Thread` 上）
→ 必然 `AttributeError` → **语音对话框的生产路径整体不可达**。

**也就是说：我"修好"的东西，在真实路径上根本观察不到。**

> 教训：**凡修复涉及 UI / 线程 / 回调，必须用 `App.run_test()` 端到端驱动**，
> 在工作线程里发起、在事件循环里断言。直接调方法只证明"函数本身对"。

---

## 二、本轮修掉的缺陷

| 编号 | 缺陷 | 判据 / 修法 |
|---|---|---|
| **F4** | `self.call_from_thread` 在 `Screen` 上不存在（**52 处**） | 改为 `self.app.call_from_thread`；MRO 实测无 `App` |
| **F1** | `_ai_text_buffer` 从不重置 → 第二轮回放第一轮回答 | 在 `_append_ai_placeholder` **同步**清空 |
| **F3** | `_update_ai_bubble` 的 `clear()` 抹掉 AI 标题（与用户气泡不对称） | `clear()` 后重写标题 |
| **F2** | `_current_ai_text` 跨轮累加 → 污染导出记录 | 两条回合入口（`_handle_text` / `_dialog_loop_single_turn`）都置空 |
| **F5** | `screens.py` 的函数级反向依赖 `from tui_agent import speak_tts` | 改指 `zeroai.tools.voice` |
| **F6** | `identity.py` 里**不可达**的 `except ImportError` 回退 | 删除；被本轮新增测试当场抓到 |
| **P0-新** | `screens.py:_async_generate_ai` 绕过 `_make_openai_sync_client` | **线上可达**（语音对话框 3 条路径）→ 代理模式失效 |
| **P0-新** | `app.py:get_current_client` 同样绕过工厂 | 实测抛 `OpenAIError: Missing credentials`；**0 调用点**，属潜在陷阱 |
| **新** | `MCPAuditLogger.record()` 同步上下文**静默丢审计记录** | `ensure_future` 抛 RuntimeError 被吞 → 协程从不 await → 永不落盘 |

### 关于"代理模式"的完整澄清

`zeroai/core/secrets.py` 的 `_make_openai_client` / `_make_openai_sync_client`
是"代理 or 直连"的**唯一决策点**。`llm.py` 与 `model_manager.py` 早已改用它
（其 docstring 明确写着"不要在此处直接 `OpenAI(...)`"）。

但审计发现 **TUI 层仍有 2 处直接构造**：

- `zeroai/tui/screens.py:1597` `_async_generate_ai()` —— **线上可达**（已修）
- `zeroai/tui/app.py:333` `get_current_client()` —— 0 调用点（已修，消除潜在陷阱）

**实测后果**（代理开启 + 本地无真实 Key，即代理模式的常态）：

```
[对照] secrets._make_openai_client('glm')
   base_url = http://127.0.0.1:8899/v1/      ← 正确走代理
   api_key  = 'proxy-token-abc'
[缺陷] app.get_current_client()
   构造失败: OpenAIError: Missing credentials. Please pass an `api_key`...
```

**合法例外（不是缺陷，共 5 处）**：`app.py` 4 处 + `vector_store.py` 1 处，
模式为「先调工厂，再 `if not _is_proxy_enabled():` 覆盖 timeout / max_retries」。

### 关于审计日志静默丢记录

```python
# 修复前
try:
    asyncio.ensure_future(self._write_to_disk(record))
except RuntimeError:
    pass          # "没有事件循环（同步上下文），跳过磁盘写入"
```

两个缺陷，均已实测复现：

1. **同步上下文静默丢记录**：线程内无 loop 时 `ensure_future` 抛 `RuntimeError`，
   被吞掉后协程对象**从未 await** → 记录只进内存、**永不落盘**。
   这正是 pytest 里那条 `RuntimeWarning: coroutine '_write_to_disk' was never awaited` 的来源。
2. **Task 无强引用**：asyncio 只持弱引用，任务可能在完成前被 GC 回收
   （`Task was destroyed but it is pending!`），同样丢记录。

审计日志是安全相关产物，静默丢记录不可接受。修复后三条路径全部落盘，警告消失。

---

## 三、我自己造成的 2 个误报（必须记住）

> **误报和漏报一样有害** —— 它们会让人去"修"本来正确的东西。

### 误报 1：「wheel 标签与平台二进制矛盾」

我的判据是"wheel 标 `py3-none-any` 却含 `.c` 源文件 → 标签说谎"。**这是错的。**
`.c` 只是**文本**。平台专属的唯一证据是**编译产物**（`.pyd` / `.so` / `.dll` / `.dylib`）。

实测：

```
wheel 内编译产物 (.pyd/.dll/.so): 0 个
磁盘上的 win_amd64 .pyd: _renderer.cp310/312-win_amd64.pyd 等 4 个 + zig_render.dll
泄漏进 wheel 的: 无 —— 已正确排除
隔离安装后 HAS_ZIG_RENDERER = False, integration 可导入 = True   ← 优雅回退
```

→ **标签诚实，原判定是误报。已重写判据并撤回结论。**

### 误报 2：「`tui_agent.get_client` 绕过客户端工厂」

我读到 `tui_agent.py:2038` 的 `def get_client(): return OpenAI(...)` 就下了结论。
但那行是**死代码**。运行时它是：

```python
f = tui_agent.get_client
f.__code__.co_filename, f.__code__.co_firstlineno
# → ('D:\\C\\C\\zeroai\\core\\model_manager.py', 121)   ← 是已修好的实现
```

原因见下节。

---

## 四、重大发现：`tui_agent.py` 有 60% 是死代码

`tui_agent.py` 有 **7 个模块级 `try:` 迁移块**：

| 位置 | 导入的模块数 |
|---|---|
| L2138–2165 | 1（`zeroai.tui.colors`） |
| **L9222–9343** | **19（主迁移块）** |
| L9865–9876 / L9914–9922 / L9928–9936 / L9940–9948 / L9956–9964 | 各 1 |

模块级 `from zeroai... import (...)` 会**覆盖同名全局绑定** → 文件更早处的同名 `def`
**全部不可达**。实测数据：

```
文件总行数            : 10,017
死代码定义占据行数    : 6,086  (60%)
被覆盖的顶层定义      : 133 个
```

**判定必须按"名字"，不能按"行号"** —— 迁移块分散在 7 处，按"行号 < 第一个迁移块"
必然误判（我第一次就误判了）。正确做法：收集**所有**迁移块的导入名，再找更早的同名顶层 `def`。

### 衍生风险（需单独警告）

迁移块是 `try / except ImportError` 结构 —— 设计意图是"zeroai 包不可用时回退到本地实现"。
但**本地实现就是旧版本**，所以：

> **一旦 zeroai 包导入失败，所有已修好的缺陷会集体复活。**
> 这是"回滚机制"变成了"缺陷复活通道"。

同时，`pyproject.toml` 的 `py-modules = ["tui_agent"]` 仍把这份 10,017 行的文件
（其中 60% 是死代码）打进 wheel，每个用户都要下载。

---

## 五、验证矩阵（全部通过）

| 层 | 结果 |
|---|---|
| 全量 pytest | **290 passed, 0 warnings**（275 → 282 → 290） |
| GHOST 扫描（**整个 zeroai 包**，60+ 模块） | **GHOST = 0** |
| 代理绕过（AST 三层判据） | **真绕过 0 处** |
| wheel 保真 | 9 个修复模块**逐字节一致** |
| 隔离安装（中立 cwd） | **8/8 PASS** |
| 审计落盘 | 8 个测试通过 |
| 代理回归 | 7 个测试通过 |
| F1–F4 复核 | 4 项 0 失败 |
| 工作线程 E2E | PASS（用户气泡 1 / AI 气泡 1） |

### 新增测试

- `tests/test_proxy_mode_regression.py`（7 个）——
  含"扫描器对**已知坏样本**必须报警"与"必须接受合法守卫模式"两条**自校验**，
  防止判据空转（只断言"扫描结果为空"是**假测试**，判据写错时照样绿）。
- `tests/test_audit_persistence.py`（8 个）—— 同步 / 异步 / 轮转 / 失败不阻断。

---

## 六、方法学教训（本轮新增，已写入 skill）

1. **直接调方法验证 UI 修复 = 假验证。** 必须 `run_test()` 端到端。
2. **`call_from_thread` 只在 `App` 上。** `Screen`/`Widget` 没有；用 `hasattr` + `__mro__` 确认。
3. **巨型单体的前半部分可能是死代码。** 用 `f.__code__.co_filename` 验证真实来源。
4. **死代码判定按"名字"不按"行号"。**
5. **静态扫描必须配"已知坏样本"自校验**，否则判据可能空转。
6. **`ensure_future` + `except RuntimeError: pass` 是静默丢数据模式。**
   看到 `coroutine ... was never awaited` 就当缺陷查，别当噪音。
7. **隔离安装验证必须用中立 cwd**，否则 `import` 命中的是源码树 —— 假通过。
8. **"含 `.c` 源" ≠ "平台专属 wheel"。** 唯一证据是编译产物。
9. **下"X 是缺陷"结论前，先问"这个证据真的蕴含这个结论吗"。**
   本轮产生 2 个误报，都是被自己的运行时复核推翻的。

---

## 七、遗留问题（未处理）

| 项 | 状态 |
|---|---|
| `client` / `_LazyClientProxy` 搬迁（原清单最后一项） | 未做 |
| `py-modules = ["tui_agent"]` 把 60% 死代码打进 wheel | 未做 |
| 迁移块的 `except ImportError` 会复活旧缺陷 | 未做（需评估是否该改成 fail-fast） |
| `ssh_ops.py` 回滚承诺与实现不符 | 未复核 |
| 1.1.4 / 1.1.5 未从 PyPI yank | 需人工浏览器操作 |
| 两个 PyPI token 未吊销 | 未做 |
| 所有改动**未提交、未发布**（HEAD = `51c5cfa release: 1.1.6`） | — |

---

## 附：本轮归档脚本（`.local_archive/2026-09-16/`）

| 脚本 | 用途 |
|---|---|
| `wheel_final_check.py` | wheel 保真 + 隔离安装（中立 cwd + 来源断言） |
| `verify_c4_truth.py` | 核实 C4 是缺陷还是误报 → **判定为我的误报** |
| `proxy_bypass_repro.py` | 代理模式断裂的实证 repro |
| `proxy_bypass_ast.py` | AST 三层判据找真绕过 |
| `deadcode_by_name.py` | 按名字统计死代码（60%） |
| `audit_disk_repro.py` | 审计日志落盘缺陷复现 |
| `ghost_detect.py` | symtable 权威 GHOST 扫描 |
