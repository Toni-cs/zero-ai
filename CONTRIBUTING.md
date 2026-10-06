# 贡献指南

感谢你对 ZeroAI 项目的关注！本文档说明参与贡献的流程。

## 开发环境

### 环境要求

- Python >= 3.10
- Git
- 可选：Zig 0.17+（用于构建 C/Zig 加速层）

### 初始化

```bash
git clone https://github.com/gt17641001169-design/zero-ai.git
cd zero-ai-cli
pip install -e .           # 安装主包（开发模式）
pip install -e ".[dev]"    # 安装开发依赖
pip install -e ".[voice]"  # 可选：安装语音依赖
```

### 构建 C/Zig 加速层（可选）

```bash
cd zeroai-tui
python setup.py build_ext --inplace
```

## 项目结构

```
zero-ai-cli/
├── zeroai/                # 模块化包（推荐入口）
│   ├── core/              # 核心层（8 个子模块）
│   ├── tools/             # 工具层（10 个子模块，56 个工具）
│   ├── tui/               # TUI 层（app.py 骨架 + 8 个 app_*.py mixin 等）
│   ├── main.py            # 统一入口
│   └── __main__.py        # 模块入口
├── zeroai-tui/            # C/Zig 加速 TUI 框架
├── zeroai-proxy/          # 代理服务器
├── pyproject.toml         # 包配置
└── README.md              # 项目说明
```

## 开发流程

### 1. 创建分支

```bash
git checkout -b feature/your-feature
```

### 2. 编写代码

- 遵循 PEP 8 风格
- 新增功能必须配套测试
- TUI 相关改动落在 `zeroai/tui/`（`app.py` 骨架 + 8 个 `app_*.py` mixin），
  **不要重新引入单文件巨类**：`tests/test_tui_source_budget.py` 会拦截
  单文件超 800 行或未登记进 `tests/tui_sources.py` 清单的改动
- `tui_agent.py` 已于 2026-10-06 彻底删除（原为 473KB 双实现），
  查看历史用 `git show <rev>:tui_agent.py`

### 3. 运行测试

```bash
# 全量测试（单元 + 回归 + 集成）
python -m pytest -q

# C/Zig 加速层测试
python -m pytest zeroai-tui/tests/ -v
```

### 4. 提交代码

```bash
git add .
git commit -m "feat: 简要描述本次变更"
```

提交信息规范（基于 Conventional Commits）：
- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 文档变更
- `refactor:` 重构（不影响功能）
- `test:` 测试相关
- `chore:` 构建/工具链相关

### 5. 推送与 Pull Request

```bash
git push origin feature/your-feature
```

在 GitHub 上发起 Pull Request，描述变更内容与测试结果。

## 重要约束

1. **不删除任何与项目功能/安全/性能/用户/数据相关的代码**
   （经确认冗余的实现、陈旧产物、无引用的备份可删，但须在提交信息中写明依据）
2. **修改或删除代码前必须备份**（临时备份进 `.local_archive/<日期>/`）
3. **修改前必须查看所有相关文件**
4. **终端命令重复或运行时间过长时自动跳过**
5. **能使用镜像源下载的都使用镜像源**
6. **每次任务结束后检查整个项目并给出下一步升级计划**
7. **未经用户允许不得升级版本号**

## 版本号规则

- 主版本号（X.0.0）：不兼容的 API 修改
- 次版本号（1.X.0）：向下兼容的功能新增
- 修订号（1.1.X）：向下兼容的 Bug 修复

**版本号升级必须经用户明确同意**，贡献者不得擅自升级。

## 测试要求

- 新增功能必须有单元测试
- 修改核心模块必须运行回归测试
- 发布前必须运行全部测试套件

## 问题反馈

- Bug 报告：[GitHub Issues](https://github.com/gt17641001169-design/zero-ai/issues)
- 安全漏洞：请勿公开报告，邮件联系作者
