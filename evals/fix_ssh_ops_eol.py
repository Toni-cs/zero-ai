# -*- coding: utf-8 -*-
"""修复：把 ssh_ops.py 的 CRLF 还原为 LF，并定位变异锚点。

根因：evals/mutation_ssh_ops.py 用 Path.write_text 还原源码，
Windows 下 write_text 默认 newline=None 会把 \n 翻译成 \r\n，
导致整份文件由 LF 变成 CRLF。改用 write_bytes 可彻底避免。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(r"D:\C\C\zeroai\tools\ssh_ops.py")


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── 1. 取 git HEAD 里的权威形态 ─────────────────────────────────────────
r = subprocess.run(["git", "show", f"HEAD:{SRC.relative_to(r'D:\C\C').as_posix()}"],
                   capture_output=True, cwd=r"D:\C\C")
head = r.stdout
print("=" * 96)
print("1. git HEAD 权威形态")
print("=" * 96)
print(f"   HEAD sha      = {_sha_bytes(head)[:16]}")
print(f"   HEAD CRLF 数  = {head.count(b'\\r\\n')}")
print(f"   HEAD 裸 LF 数 = {head.count(b'\\n') - head.count(b'\\r\\n')}")

cur = SRC.read_bytes()
print(f"   工作区 sha    = {_sha_bytes(cur)[:16]}")
print(f"   工作区 CRLF   = {cur.count(b'\\r\\n')}")
print(f"   工作区 裸 LF  = {cur.count(b'\\n') - cur.count(b'\\r\\n')}")

# ── 2. 内容是否一致（忽略换行差异） ─────────────────────────────────────
head_norm = head.replace(b"\r\n", b"\n")
cur_norm = cur.replace(b"\r\n", b"\n")
print(f"\n   忽略换行后内容一致 = {head_norm == cur_norm}")

if head_norm != cur_norm:
    print("   !! 内容本身有差异，不能直接按换行归一，需人工检查")
    sys.exit(1)

# ── 3. 还原为 LF（写 bytes，杜绝翻译） ─────────────────────────────────
if cur != head_norm:
    SRC.write_bytes(head_norm)
    print(f"\n   已还原为 LF -> sha = {_sha_bytes(SRC.read_bytes())[:16]}")
else:
    print("\n   工作区已是 LF，无需改动")

print(f"   与 HEAD 是否完全一致 = {SRC.read_bytes() == head}")

# ── 4. 定位变异锚点 ─────────────────────────────────────────────────────
print()
print("=" * 96)
print("2. 变异锚点核对")
print("=" * 96)
text = SRC.read_text(encoding="utf-8")
ANCHORS = {
    "M1": '        if exit_code != 0:\n            parts.append(f"[退出码: {exit_code}]")',
    "M2": "        if len(stdout) > 8000:",
    "M3": '    if is_dangerous and PERMISSION_LEVEL != "full" and not confirm_dangerous:',
    "M4": '            safe_host = f"{parts[0]}.{parts[1]}.***.***"',
    "M5": "    if not result:\n        return False",
    "M6": '        return "错误：protocol 必须是 tcp 或 udp"',
}
for tag, a in ANCHORS.items():
    hit = a in text
    print(f"   {tag}: {'命中' if hit else '!! 未命中'}")
    if not hit:
        # 打印真实行，便于改正缩进
        key = a.strip().split("\n")[0].strip()[:50]
        for i, line in enumerate(text.splitlines(), 1):
            if key[:30] and key[:30] in line:
                print(f"        源码 L{i}: {line!r}")
                break
