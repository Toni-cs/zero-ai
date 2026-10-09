# -*- coding: utf-8 -*-
"""决定性测量：zero-ai 的工具 schema 占 CONTEXT_LIMIT 多少，以及 shell 能替掉哪些。

问题背景（用户原话）："我都有 shell 拼写了，真的还需要工具了吗"
—— 这个问题只有量出"工具 schema 的 token 成本"才能答，不能拍脑袋。
"""
from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from zeroai.tools.registry import TOOLS, TOOL_MAP

# ── 1. 工具规模 ──────────────────────────────────────────────────────────
print("=" * 78)
print("1. 工具规模")
print("=" * 78)
n_tools = len(TOOLS)
print(f"  TOOLS (schema) 条数    : {n_tools}")
print(f"  TOOL_MAP 条数          : {len(TOOL_MAP)}")

# ── 2. schema 的字符/token 成本 ──────────────────────────────────────────
print()
print("=" * 78)
print("2. 工具 schema 的上下文成本")
print("=" * 78)

schema_text = json.dumps(TOOLS, ensure_ascii=False)
chars = len(schema_text)
tokens = max(1, chars // 3)   # 与 context.CHARS_PER_TOKEN=3 同口径

CONTEXT_LIMIT = 8192          # skills.py:17 / agent.py 默认 or 8000
print(f"  schema JSON 字符数     : {chars:,}")
print(f"  估算 token (÷3)        : {tokens:,}")
print(f"  占 CONTEXT_LIMIT={CONTEXT_LIMIT}  : {tokens / CONTEXT_LIMIT * 100:.1f}%")

# 系统提示词 + 观察 + 历史的实际占用（同口径粗估）
from zeroai.core import prompts as _P
SYSTEM_PROMPT = next(
    (getattr(_P, n) for n in ("SYSTEM_PROMPT", "SYSTEM_PROMPT_TEXT",
                              "get_system_prompt")
     if hasattr(_P, n) and isinstance(getattr(_P, n), str)), "")
sys_chars = len(SYSTEM_PROMPT)
sys_tok = max(1, sys_chars // 3)
print()
print(f"  SYSTEM_PROMPT 字符     : {sys_chars:,}  (~{sys_tok:,} token, "
      f"{sys_tok / CONTEXT_LIMIT * 100:.1f}%)")
print(f"  合计（仅静态两项）     : ~{tokens + sys_tok:,} token "
      f"({(tokens + sys_tok) / CONTEXT_LIMIT * 100:.1f}%)")
print(f"  留给对话/观察的余额    : ~{CONTEXT_LIMIT - tokens - sys_tok:,} token")

# ── 3. 每个工具的 schema 成本排名 ────────────────────────────────────────
print()
print("=" * 78)
print("3. 单工具 schema 成本排名（前 15）")
print("=" * 78)
costs = []
for t in TOOLS:
    fn = t.get("function", {})
    name = fn.get("name", "?")
    c = len(json.dumps(t, ensure_ascii=False))
    costs.append((c, name, len(fn.get("parameters", {}).get("properties", {}) or {})))
costs.sort(reverse=True)
print(f"  {'工具':<34} {'字符':>7} {'~tok':>6} {'参数':>5}")
print("  " + "-" * 60)
for c, name, np in costs[:15]:
    print(f"  {name:<34} {c:>7,} {max(1, c // 3):>6,} {np:>5}")
print(f"  ... 共 {len(costs)} 个，合计 {sum(c for c, _, _ in costs):,} 字符")

# ── 4. shell 可直接替代性初判 ────────────────────────────────────────────
print()
print("=" * 78)
print("4. 按“shell 能否可靠替代”分类")
print("=" * 78)

# 有副作用/长连接/结构化产出 -> shell 难替代
NEED_SPECIAL = {
    "ssh_*": "SSH 长连接 + 连接池，shell 每次重连，语义不同",
    "read_file/write_file/edit_file": "精确行号编辑 + 原子写 + EOL 归一化，shell 易损坏文件",
    "search_files/list_dir/glob": "返回路径列表供模型继续操作，shell 输出需再解析",
    "web_search/web_fetch": "网络 IO + 超时/大小控制",
    "render_formula": "产出是图，不是文本",
    "read_image": "产出是多模态内容块",
    "skill_list/skill_load": "技能体系的按需取回",
    "memory_*": "跨会话持久化",
    "rag_search": "向量检索",
    "exec_python/code_execute": "沙箱 + AST 拦截，shell 跑 python 是裸跑",
    "plan_*": "编排状态",
}
# 本质是 shell 命令的薄包装 -> 可被 run_command 替代
THIN_WRAPPER_HINTS = ("_check", "_monitor", "system_info", "process_list",
                      "check_port", "local_", "pip_install")

thin, special, other = [], [], []
for c, name, np in costs:
    if any(name.startswith(p) or p in name for p in ("ssh_",)):
        special.append((name, c))
    elif any(h in name for h in THIN_WRAPPER_HINTS):
        thin.append((name, c))
    else:
        other.append((name, c))

print(f"\n  [A] shell 薄包装，可考虑收敛  {len(thin)} 个 / "
      f"{sum(c for _, c in thin):,} 字符")
for n, c in thin:
    print(f"      {n:<34} {c:>6,}")

print(f"\n  [B] shell 难以可靠替代  {len(special)} 个 / "
      f"{sum(c for _, c in special):,} 字符")
for n, c in special:
    print(f"      {n:<34} {c:>6,}")

print(f"\n  [C] 其余（需逐个判断）  {len(other)} 个 / "
      f"{sum(c for _, c in other):,} 字符")
for n, c in other:
    print(f"      {n:<34} {c:>6,}")
