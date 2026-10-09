# -*- coding: utf-8 -*-
"""从 zeroai/core/context.py 精确删除步内护栏代码块（lines 243-311）。

删除原因（2026-10-09 决策）：
  该函数的前提"1MB 工具输出会撑爆上下文"已被端到端实测证伪
  （见 evals/verify_1mb_e2e.py：1MB -> 最终 121 token，占 8192 的 1.5%），
  且它匹配 role=="tool"，而实际存储格式是 role:"user" + "[工具结果 X]"，
  永远匹配不到。留着会误导后来人与评审，故删除。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TARGET = Path(r"D:\C\C\zeroai\core\context.py")

START = "# 步内护栏：防止单条工具输出撑爆上下文"
# 结束标记：函数最后一行
END = "    return new_messages"


def main() -> int:
    raw = TARGET.read_bytes()

    # 编码体检
    if raw.startswith(b"\xef\xbb\xbf"):
        print("[阻断] 文件含 BOM")
        return 1
    if b"\r\n" in raw:
        print("[阻断] 文件含 CRLF")
        return 1
    before_sha = hashlib.sha256(raw).hexdigest()[:12]

    text = raw.decode("utf-8")
    lines = text.split("\n")   # 1-indexed 辅助

    # 定位起始：注释分隔栏的三行，从 '# ===...' + START 那组开始
    start_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == START:
            # 往上回退两行到 '# ===...' 与 '# ===...'
            start_idx = i - 1
            break
    if start_idx is None:
        print("[阻断] 未找到起始标记")
        return 1
    # 确认 start_idx 处是分隔栏
    if not lines[start_idx].startswith("# ="):
        print(f"[阻断] 起始行不是分隔栏: {lines[start_idx]!r}")
        return 1

    # 定位结束：END 之后的第一个非空行之前
    end_idx = None
    for i in range(start_idx, len(lines)):
        if lines[i] == END:
            end_idx = i
            break
    if end_idx is None:
        print("[阻断] 未找到结束标记")
        return 1

    # 吃掉 END 之后的空行，直到下一个非空行（应为 '# ====...' 第2层）
    tail = end_idx + 1
    while tail < len(lines) and lines[tail].strip() == "":
        tail += 1

    removed = tail - start_idx
    print(f"删除行 {start_idx + 1} - {tail}（共 {removed} 行）")
    print(f"  起: {lines[start_idx]!r}")
    print(f"  止: {lines[tail - 1]!r}")
    print(f"  后: {lines[tail]!r}")

    new_lines = lines[:start_idx] + lines[tail:]
    new_text = "\n".join(new_lines)

    # 校验删除后剩余结构完好
    for marker in ("def cleanup_context(", "# 第2层：GLM 深度压缩",
                   "def _split_messages_for_compress("):
        assert marker in new_text, f"删除后缺少 {marker}"
    assert "enforce_context_limit" not in new_text, "仍残留 enforce_context_limit"
    assert "_shrink_tool_output" not in new_text, "仍残留 _shrink_tool_output"

    TARGET.write_bytes(new_text.encode("utf-8"))

    after = TARGET.read_bytes()
    after_sha = hashlib.sha256(after).hexdigest()[:12]
    print()
    print(f"  sha {before_sha} -> {after_sha}")
    print(f"  行数 {len(lines)} -> {len(new_lines)}（应少 {removed}）")
    print("[完成] 结构校验通过：无残留、关键函数完好")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
