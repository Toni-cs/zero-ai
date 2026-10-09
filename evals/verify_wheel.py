# -*- coding: utf-8 -*-
"""wheel 解包验收：确认关键资源真的进了包

历史故障：assets/icons 的 glob 从未命中，图标在已发布版本里是缺失的，
直到有人解包才发现。此后"构建 wheel + 解包复测"成为本项目的强制验收方式。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# (说明, 必须命中的成员名前缀, 最少个数)
MUST_HAVE = [
    ("技能（Skills）正文", "zeroai/skills/", 2),
    ("主配置", "zeroai/config.yaml", 1),
    ("TUI 图标", "zeroai/assets/icons/", 1),
    ("入口 console script", "zero_ai_cli-", 1),
]

# 已知的历史故障形态：glob 写错导致整类资源缺失
MUST_NOT_BE_EMPTY = ["zeroai/skills/", "zeroai/assets/icons/"]


def main() -> int:
    wheels = sorted(DIST.glob("*.whl"))
    if not wheels:
        print("[失败] dist/ 下没有 wheel")
        return 1

    whl = wheels[-1]
    print(f"检查: {whl.name}  ({whl.stat().st_size / 1024:.1f} KB)\n")

    zf = zipfile.ZipFile(whl)
    names = zf.namelist()
    bad = 0

    print(f"{'验收项':<20} {'要求':<22} {'实际':>6}  结果")
    print("-" * 70)
    for label, prefix, minimum in MUST_HAVE:
        hits = [n for n in names if n.startswith(prefix)]
        ok = len(hits) >= minimum
        if not ok:
            bad += 1
        print(f"{label:<20} {prefix:<22} {len(hits):>6}  {'PASS' if ok else 'FAIL'}")
        if not ok:
            for n in hits[:5]:
                print(f"      命中: {n}")

    print("\n=== 技能正文抽样（确认不是空壳）===")
    skills = sorted(n for n in names if n.startswith("zeroai/skills/") and n.endswith(".md"))
    if not skills:
        print("  [FAIL] 包内无任何技能文件")
        bad += 1
    for n in skills:
        data = zf.read(n).decode("utf-8", errors="replace")
        has_fm = data.lstrip().startswith("---")
        lines = data.count("\n") + 1
        ok = has_fm and lines > 10
        if not ok:
            bad += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}  {lines} 行  frontmatter={'有' if has_fm else '无'}")

    print("\n=== 图标抽样 ===")
    icons = sorted(n for n in names if n.startswith("zeroai/assets/icons/"))
    print(f"  {'PASS' if icons else 'FAIL'} 图标 {len(icons)} 个" +
          ("，例如 " + ", ".join(Path(i).name for i in icons[:4]) if icons else ""))
    if not icons:
        bad += 1

    print("\n=== 包内顶层条目 ===")
    tops = sorted({n.split("/")[0] for n in names})
    print("  " + ", ".join(tops))

    print("\n" + "=" * 70)
    if bad:
        print(f"[失败] {bad} 项未通过")
        return 1
    print("[通过] 全部资源确认进包，可发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
