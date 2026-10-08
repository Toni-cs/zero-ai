# -*- coding: utf-8 -*-
"""删除 config.yaml 的 experts 段（字节级拼接，不做 decode/encode）。

## 为什么删

代码侧已全部改读 constants.EXPERT_TEAM（config.get_expert_config 与
zeroai_tui/integration.init_core）。删掉配置段后，config.yaml 里不再有
一份「看起来权威、实际没人读」的专家数据 —— 而这正是本次漂移事故的
成因：两份数据并存、各自演化，最后 academic 的 config 版丢了整套
「禁止编造文献」规则却无人发现。

数据只要还在，就总有人会去读它。

## 怎么删

按行号定位段边界，用字节切片拼接。全程不 decode/encode，
原文件其余字节保证逐字节不变（见下方自检）。
"""
import io
import os
import sys

PATH = os.path.join("zeroai", "config.yaml")
START_LINE = 36      # `# ====== Expert Team Configuration ======`
END_LINE = 298       # 段末空行（299 是下一节注释，不删）
EXPECT_MARKER_START = "# ====== Expert Team Configuration ======"
EXPECT_MARKER_END = "# ====== Hybrid Mode Configuration ======"

raw = io.open(PATH, "rb").read()
lines = raw.split(b"\n")
print("原始: %d 字节 / %d 行" % (len(raw), len(lines)))

# 边界断言 —— 行号变了就拒绝执行，避免误删
s = lines[START_LINE - 1].decode("utf-8").strip()
e = lines[299 - 1].decode("utf-8").strip()   # 第 299 行
if s != EXPECT_MARKER_START:
    raise SystemExit("第 %d 行不是预期的段首注释，实际: %r" % (START_LINE, s))
if e != EXPECT_MARKER_END:
    raise SystemExit("第 299 行不是预期的下一节注释，实际: %r" % e)
if lines[START_LINE].decode("utf-8").rstrip("\r") != "experts:":
    raise SystemExit("第 %d 行不是 experts:，实际: %r"
                     % (START_LINE, lines[START_LINE]))

# 删除 [START_LINE-1, END_LINE-1] 这些行
kept = lines[:START_LINE - 1] + lines[END_LINE:]
new = b"\n".join(kept)
removed = len(raw) - len(new)
print("删除: %d 字节 / %d 行" % (removed, END_LINE - START_LINE + 1))
print("新文件: %d 字节 / %d 行" % (len(new), len(kept)))

# 自检 1：split/join 必须无损往返 —— 证明 kept 是 raw 的真子序列，
# 没有在拼接过程中改动任何其它字节
assert b"\n".join(lines) == raw, "split/join 往返不等价，字节已被改动"
head = b"\n".join(lines[:START_LINE - 1])
tail = b"\n".join(lines[END_LINE:])
assert new.startswith(head), "文件头部被改动"
assert new.endswith(tail), "文件尾部被改动"
assert new.count(b"\nexperts:\n") == 0, "experts 段未被完全移除"
print("自检: split/join 无损往返，头部/尾部字节保持不变，experts 已清除")

# 自检 2：YAML 仍可解析，且不再含 experts
import yaml  # noqa: E402
cfg = yaml.safe_load(new.decode("utf-8"))
assert cfg is not None, "YAML 解析失败"
assert "experts" not in cfg, "experts 段仍存在"
for k in ("models", "hybrid", "tools", "tui", "security"):
    assert k in cfg, "缺少顶层段 %s" % k
assert "api_key" in (cfg.get("models") or {}).get("glm", {}), \
    "models 段被破坏（api_key 丢失）"
print("自检: YAML 可解析、experts 已移除、其余 5 段齐全、models.api_key 在位")

# 自检 3：删除的内容确实只有 experts 段
removed_txt = b"\n".join(lines[START_LINE - 1:END_LINE]).decode("utf-8")
assert removed_txt.startswith(EXPECT_MARKER_START)
assert "\nexperts:\n" in removed_txt
print("自检: 删除块以预期注释开头且包含 experts:")

with io.open(PATH, "wb") as f:
    f.write(new)
print("OK ->", PATH)
