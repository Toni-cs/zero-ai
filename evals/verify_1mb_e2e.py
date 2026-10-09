# -*- coding: utf-8 -*-
"""端到端实测：1MB 的 run_command 输出，最终有多少真正进到模型。

链路（全部取自源码，非转述）：
  run_command 返回            -> 最多 MAX_CAPTURE_BYTES = 1MB
  agent.py:1429 存入 messages -> smart_truncate(result, 1500)
  agent.py:325  _build_observation -> token_budget = 2000（超出即 smart_truncate）
  agent.py:362  _build_history      -> smart_truncate(result, 300)
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from zeroai.core.agent import ReActPlanner, smart_truncate
from zeroai.core.context import estimate_tokens
from zeroai.tools.command_exec import MAX_CAPTURE_BYTES, run_command

planner = ReActPlanner.__new__(ReActPlanner)   # 不走 __init__，本方法不读实例状态

print("=" * 76)
print("端到端：1MB 工具输出 → 模型实际看到多少")
print("=" * 76)

# ── 1. run_command 真实产出 ────────────────────────────────────────────────
raw = run_command(f"python -c \"print('X' * {MAX_CAPTURE_BYTES + 9000})\"",
                  skip_translate=True)
print(f"  [1] run_command 返回        : {len(raw):>9,} 字符  "
      f"(采集上限 {MAX_CAPTURE_BYTES:,})")

# ── 2. 存入 messages 时（agent.py:1429）─────────────────────────────────
stored = f"[工具结果 run_command] {smart_truncate(raw, 1500)}"
print(f"  [2] smart_truncate(1500) 后 : {len(stored):>9,} 字符  ← 落进 messages")
assert len(stored) <= 1500 + 60, "第2层失效"

# ── 3. 构建观察时（agent.py:325 token_budget=2000）───────────────────────
messages = [
    {"role": "user", "content": "帮我看下系统状态"},
    {"role": "assistant", "content": "[调用工具 run_command] 查看系统"},
    {"role": "user", "content": stored},
]
obs = planner._build_observation(messages, None, "帮我看下系统状态")
print(f"  [3] _build_observation      : {len(obs):>9,} 字符 ≈ "
      f"{estimate_tokens([{'role': 'user', 'content': obs}]):>5} token "
      f"(预算 2000)")

# ── 4. 构建历史时（agent.py:362）─────────────────────────────────────────
hist = planner._build_history([
    {"thought": "查看系统", "action_type": "tool_call",
     "tool_name": "run_command", "result": raw},
])
print(f"  [4] _build_history          : {len(hist):>9,} 字符  "
      f"(result 截到 300)")

# ── 5. 拼成发给 planner 的 user_prompt 总量 ──────────────────────────────
from zeroai.core.agent import PLANNER_USER_TEMPLATE  # noqa: E402

prompt = PLANNER_USER_TEMPLATE.format(
    user_input="帮我看下系统状态"[:500],
    observation=obs,
    tools_summary="(工具列表)",
    history=hist,
)
prompt_tokens = estimate_tokens([{"role": "user", "content": prompt}])
print()
print(f"  [5] 最终 user_prompt        : {len(prompt):>9,} 字符 ≈ "
      f"{prompt_tokens:>5} token")
print()

# ── 6. 换成带真实错误/代码的长输出，确认 smart_truncate 的取材效果 ──────
print("=" * 76)
print("补充：真实型长输出（含 traceback/代码块）")
print("=" * 76)
lines = ["正常行 %d" % i for i in range(60000)]
lines[30000] = "Traceback (most recent call last):"
lines[30001] = '  File "app.py", line 12, in <module>'
lines[30002] = "KeyError: 'missing_field'"
realistic = "\n".join(lines)
print(f"  [1] 原始                    : {len(realistic):>9,} 字符")

stored2 = f"[工具结果 run_command] {smart_truncate(realistic, 1500)}"
print(f"  [2] smart_truncate(1500) 后 : {len(stored2):>9,} 字符")
print(f"      是否保住 KeyError       : {'KeyError' in stored2}")
print(f"      是否保住 Traceback      : {'Traceback' in stored2}")

obs2 = planner._build_observation(
    [{"role": "user", "content": "查"},
     {"role": "user", "content": stored2}], None, "查")
print(f"  [3] _build_observation      : {len(obs2):>9,} 字符 ≈ "
      f"{estimate_tokens([{'role': 'user', 'content': obs2}]):>5} token")

print()
print("=" * 76)
print("结论")
print("=" * 76)
print(f"  1MB 输入经过 3 层后，最终进模型约 {prompt_tokens} token，")
print(f"  占 8192 上限的 {prompt_tokens / 8192 * 100:.1f}%。")
print("  => 1MB 采集上限【不会】撑爆上下文——此前'零防护'的判断不成立。")
print()
print("  额外事实：把采集上限从 8000 提到 1MB，对【模型看到的内容】几乎无影响，")
print("  因为第 2 层永远只放行 1500 字符。1MB 真正的影响是：")
print("    smart_truncate 可从更大的素材里挑 error/traceback/代码块等关键行。")
