# -*- coding: utf-8 -*-
"""守卫测试：受跟踪的文本文件不得出现 Unicode 替换符 U+FFFD。

背景（2026-10-08）：一次编辑把 .gitignore（混合编码）按 UTF-8 解码重写，
产生 406 个 U+FFFD，GBK 段中文注释被永久替换。该损坏靠人工比对
25/20 的异常 diff 行数才被发现，故固化为自动检查。

判定逻辑：
- U+FFFD (ef bf bd) 在源文件里没有合法用途，出现即为编码损坏。
- 检查范围：git ls-files 列出的受跟踪文件；无 git 时退回扫描
  zeroai/ 与仓库根下的 .py/.md/.toml/.yaml。
- 二进制文件按大小与空字节启发式跳过。
"""
import io
import os
import subprocess

# 单文件上限，避免扫到超大二进制
MAX_BYTES = 2 * 1024 * 1024
FFFD = b"\xef\xbf\xbd"

TEXT_EXTS = {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".txt",
             ".cfg", ".ini", ".zig", ".js", ".html", ".css", ".sh",
             ".spec"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 诊断证据目录：这些 .txt/.md 是脚本生成的实测记录，其中允许**原样引用**
# 含 U+FFFD 的坏字节（例如 gitignore_encoding.txt 记录的就是被损坏前后的
# 字节对比）。把它们排除在外，是因为守卫的目标是保护源码与文档不被编码
# 损坏，而不是禁止证据里出现被损坏的样本。
# 注意：evals/*.py 仍在扫描范围内 —— 若编辑工具损坏了脚本本身，照样报警。
EVIDENCE_PREFIX = os.path.join("evals", "results") + os.sep


def tracked_files():
    """列出受跟踪文件；git 不可用时退回本地扫描。"""
    try:
        out = subprocess.run(["git", "ls-files"],
                             capture_output=True, cwd=ROOT, timeout=30)
        if out.returncode == 0 and out.stdout:
            return [os.path.join(ROOT, p) for p in
                    out.stdout.decode("utf-8", "replace").splitlines() if p]
    except Exception:  # noqa: BLE001
        pass
    found = []
    for base in (os.path.join(ROOT, "zeroai"), ROOT):
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns
                      if d not in ("__pycache__", ".git", "libs",
                                   "build", "dist", "node_modules")]
            for fn in fns:
                if os.path.splitext(fn)[1] in TEXT_EXTS:
                    found.append(os.path.join(dp, fn))
    return found


def looks_binary(buf):
    if b"\x00" in buf[:4096]:
        return True
    return False


def test_no_unicode_replacement_char_in_tracked_files():
    """任何受跟踪文本文件都不应含 U+FFFD —— 出现即编码损坏。"""
    paths = tracked_files()
    assert paths, "未扫描到任何文件，检查脚本是否失效"

    offenders = []
    scanned = 0
    for p in paths:
        rel = os.path.relpath(p, ROOT)
        # 跳过诊断证据目录（见 EVIDENCE_PREFIX 注释）
        if rel.replace("\\", "/").startswith("evals/results/"):
            continue
        try:
            if os.path.getsize(p) > MAX_BYTES:
                continue
            with open(p, "rb") as f:
                buf = f.read()
        except (OSError, IOError):
            continue
        scanned += 1
        if looks_binary(buf):
            continue
        if FFFD in buf:
            offenders.append("%s  x%d" % (rel, buf.count(FFFD)))

    assert scanned > 0, "没有扫到可读文件"
    assert not offenders, (
        "以下文件含 U+FFFD 替换符（编码被损坏，应从改动前的 commit 恢复"
        "原始字节）：\n  " + "\n  ".join(offenders))


def test_gitignore_decodes_cleanly():
    """.gitignore 必须能按其声明的编码解出内容，不能有替换符。"""
    p = os.path.join(ROOT, ".gitignore")
    assert os.path.exists(p), ".gitignore 不存在"
    with open(p, "rb") as f:
        buf = f.read()
    assert FFFD not in buf, ".gitignore 含 U+FFFD，中文注释已被破坏"
    # 现状：该文件为混合编码（多数行 GB18030，L17/23/27/33 为被截断的
    # UTF-8）。这里只要求至少 gb18030 或 utf-8 之一能完整解码。
    ok = False
    for enc in ("utf-8", "gb18030"):
        try:
            buf.decode(enc)
            ok = True
        except UnicodeDecodeError:
            continue
    assert ok, ".gitignore 无法用 utf-8 或 gb18030 完整解码"


if __name__ == "__main__":
    test_no_unicode_replacement_char_in_tracked_files()
    test_gitignore_decodes_cleanly()
    print("PASS: text encoding integrity")
