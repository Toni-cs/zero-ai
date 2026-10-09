# -*- coding: utf-8 -*-
"""代码格式化器 —— 检测、调用、优雅降级

对标 OpenCode 的 Formatters 能力（`anomalyco/opencode`，**MIT 许可**）。
设计对标，非代码复制。

## 三条不可违反的约束

1. **格式化失败绝不能影响写入结果**。`write_file`/`edit_file` 先落盘，
   格式化是**事后可选步骤**；任何异常都吞掉并保留原内容。
   写文件是主路径，格式化是增值路径，主路径不能被增值路径拖垮。

2. **没有格式化器时必须是无操作**。本项目开发环境当前就**没有装**
   ruff/black（已实测 `python -m ruff`、`python -m black` 均 No module），
   所以"检测不到就跳过"不是理论分支，是**当前的真实运行路径**。

3. **只格式化真能判断的语言**。按扩展名白名单匹配，未知扩展名一律跳过 ——
   对一个 .md 或 .bin 调 ruff 是没意义且危险的。

## 调用方式

统一走 **stdin -> stdout**（而非原地改文件），因为：

  - 可以先拿到结果、比对内容、确认没变坏，再决定要不要落盘；
  - 不需要临时文件，失败时零副作用。

各格式化器的 stdin 用法：`ruff format -` / `black -` / `prettier
--stdin-filepath` / `clang-format --assume-filename` / `gofmt`。

## 开关

环境变量 `ZEROAI_FORMAT_ON_WRITE`，默认开启（"0"/"false"/"no" 关闭）。
不放进 config.yaml：这是个纯本地的开发期行为，不该污染配置模式。
"""
import io
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Formatter",
    "BUILTIN_FORMATTERS",
    "detect",
    "clear_probe_cache",
    "formatter_for",
    "format_code",
    "format_file",
    "formatting_enabled",
    "format_on_write",
]

_TIMEOUT = 10  # 秒。格式化器卡死不能拖住写文件


@dataclass(frozen=True)
class Formatter:
    """一个格式化器：命令模板 + 适用扩展名 + 适用语言

    ``probe`` 非空时，仅 ``which(argv[0])`` **不足以**判定可用 —— 必须跑一次
    probe 确认目标工具真的在。这只在"包装器存在 ≠ 目标工具存在"时需要：
    ``npx`` 在 PATH 上，但 ``npx --no-install prettier`` 在没装 prettier 的
    机器上会失败。实测本机即为这种情况（见 detect() 下方说明）。
    """

    name: str
    argv: Tuple[str, ...]          # 含 {file} 占位符，展开为实际文件名
    exts: Tuple[str, ...]          # 小写，含点，如 ".py"
    langs: Tuple[str, ...] = ()
    probe: Tuple[str, ...] = ()    # 非空则以此验证"目标工具确实可用"

    def build(self, filename: str) -> List[str]:
        return [a.replace("{file}", filename) for a in self.argv]


# 扩展名 -> 优先级排序的格式化器（同扩展名时排在前面的优先）
BUILTIN_FORMATTERS: Tuple[Formatter, ...] = (
    # Python：ruff 最快且是现代首选，black/autopep8 兜底
    Formatter("ruff", ("ruff", "format", "--stdin-filename", "{file}", "-"),
              (".py",), ("python",)),
    Formatter("black", ("black", "-q", "-"), (".py",), ("python",)),
    Formatter("autopep8", ("autopep8", "-"), (".py",), ("python",)),
    # 前端 / JSON
    Formatter("prettier", ("prettier", "--stdin-filepath", "{file}"),
              (".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".scss"),
              ("javascript", "typescript", "json", "css")),
    # npx 是包装器：npx 在 PATH 上**不代表** prettier 装了。
    # 实测本机 `npx --no-install prettier` 报 "missing packages"，
    # 即 which 会给出假阳性 —— 故必须 probe 复核（见 Formatter.probe）。
    Formatter("prettier-npx",
              ("npx", "--no-install", "prettier", "--stdin-filepath", "{file}"),
              (".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".scss"),
              ("javascript", "typescript", "json", "css"),
              probe=("npx", "--no-install", "prettier", "--version")),
    # C/C++
    Formatter("clang-format", ("clang-format", "--assume-filename={file}"),
              (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"), ("c", "cpp")),
    # Go 内置 gofmt
    Formatter("gofmt", ("gofmt",), (".go",), ("go",)),
)

_ENV_FLAG = "ZEROAI_FORMAT_ON_WRITE"
# 空串**不算关闭** —— 环境变量被设成空值等同于没设，走默认（开启）。
_FALSE = {"0", "false", "no", "off"}

# probe 结果缓存（进程级）。npx 启动要秒级，探测一次就够 ——
# 但**不能缓存 which 的结果**：装了新工具应当立即生效。
_PROBE_CACHE: Dict[str, bool] = {}


def clear_probe_cache() -> None:
    _PROBE_CACHE.clear()


def _run_probe(fmt: Formatter) -> bool:
    """跑一次 probe，判断目标工具是否真的可用（结果缓存）"""
    if fmt.name in _PROBE_CACHE:
        return _PROBE_CACHE[fmt.name]
    ok = False
    if fmt.probe:
        try:
            p = subprocess.run(list(fmt.probe), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=8)
            ok = p.returncode == 0
        except Exception:            # noqa: BLE001 — 探测失败一律视为不可用
            ok = False
    _PROBE_CACHE[fmt.name] = ok
    return ok


def formatting_enabled() -> bool:
    """写文件后是否自动格式化（环境变量可关）"""
    return os.environ.get(_ENV_FLAG, "1").strip().lower() not in _FALSE


def detect(formatters: Optional[Sequence[Formatter]] = None) -> Dict[str, Formatter]:
    """返回本机可用的格式化器 {name: Formatter}

    两级判定：

    1. ``which(argv[0])`` —— 每次重查、**不缓存**。缓存 PATH 查询会让
       "装了新工具却不生效"变成一个极难复现的 bug。
    2. ``probe`` —— 只对**包装器型**条目执行（见 Formatter.probe）。
       本机实测：``npx`` 在 PATH 上，但 ``npx --no-install prettier``
       报 missing packages，即纯 which 会给出假阳性。
       probe 要起子进程、耗时秒级，故按名字缓存结果。
    """
    src = list(formatters) if formatters is not None else list(BUILTIN_FORMATTERS)
    out: Dict[str, Formatter] = {}
    for fmt in src:
        if not shutil.which(fmt.argv[0]):
            continue
        if fmt.probe and not _run_probe(fmt):
            continue
        if fmt.name not in out:
            out[fmt.name] = fmt
    return out


def formatter_for(path: str,
                  available: Optional[Dict[str, Formatter]] = None,
                  formatters: Optional[Sequence[Formatter]] = None
                  ) -> Optional[Formatter]:
    """按扩展名挑出该文件的格式化器；没有匹配的返回 None"""
    ext = os.path.splitext(str(path))[1].lower()
    if not ext:
        return None
    avail = available if available is not None else detect(formatters)
    # 保持 BUILTIN_FORMATTERS 的既定优先级（ruff 先于 black）
    pool = formatters if formatters is not None else BUILTIN_FORMATTERS
    for fmt in pool:
        if fmt.name in avail and ext in fmt.exts:
            return fmt
    return None


def format_code(code: str, filename: str = "snippet.py",
                formatters: Optional[Sequence[Formatter]] = None
                ) -> Tuple[str, Optional[str], Optional[str]]:
    """格式化一段代码

    返回 (结果代码, 格式化器名, 错误信息)。三态约定：

        (code,      None, None)  没有可用格式化器 -> 原样返回
        (code,      None, "…")   调用失败/输出可疑 -> 原样返回并给出原因
        (new,       "ruff", None)格式化成功且有变化
        (code,      "ruff", None)格式化成功但无变化（也是成功）

    **任何失败路径都返回原始 code**，绝不返回半成品。
    """
    if not code:
        return code, None, None
    fmt = formatter_for(filename, formatters=formatters)
    if fmt is None:
        return code, None, None

    try:
        proc = subprocess.run(
            fmt.build(os.path.basename(filename)),
            input=code.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_TIMEOUT,
        )
    except FileNotFoundError:
        return code, None, None          # which 与实际执行之间被卸载了
    except subprocess.TimeoutExpired:
        return code, None, f"{fmt.name} 超时（>{_TIMEOUT}s），保留原内容"
    except OSError as e:
        return code, None, f"{fmt.name} 无法执行：{e}"

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return code, None, (f"{fmt.name} 报错（退出码 {proc.returncode}），"
                            f"保留原内容：{err[-1] if err else '无输出'}")

    out = proc.stdout.decode("utf-8", "replace")
    # 输出为空而输入非空 —— 多半是格式化器把内容吞了，宁可不格式化
    if not out.strip() and code.strip():
        return code, None, f"{fmt.name} 输出为空，保留原内容"
    if not out.strip():
        return code, None, None
    # Windows 上子进程的 stdout 走文本模式，会把 \n 翻译成 \r\n。
    # 若**差异仅在行尾**，按无改动处理 —— 否则一次格式化会把整个文件的
    # 换行符静默改掉，git diff 全红、跨平台协作直接炸。这是实测本机
    # （Windows + 无 BOM）发现的行为，不是理论分支。
    if _normalize_eol(out) == _normalize_eol(code):
        return code, fmt.name, None
    return out, fmt.name, None


def _normalize_eol(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def format_file(path: str,
                formatters: Optional[Sequence[Formatter]] = None
                ) -> Tuple[bool, Optional[str], Optional[str]]:
    """格式化磁盘上的文件（读 -> 格式化 -> 有变化才写回）

    返回 (是否改动, 格式化器名, 错误信息)。文件不存在或不可读时返回
    (False, None, 原因)，不抛异常。
    """
    p = str(path)
    try:
        raw = io.open(p, "rb").read()
    except OSError as e:
        return False, None, f"读取失败：{e}"

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, None, "非 UTF-8 文件，跳过格式化"

    new, name, err = format_code(text, os.path.basename(p), formatters=formatters)
    if err or new == text or name is None:
        return False, (None if err else name), err
    try:
        io.open(p, "w", encoding="utf-8", newline="").write(new)
    except OSError as e:
        return False, name, f"写回失败：{e}"
    return True, name, None


# ─────────────────── 写文件后的钩子（file_manager 用） ───────────────────

def format_on_write(path: str) -> str:
    """写入成功后调用；**返回给人看的后缀，永远不抛异常**

    返回空串表示无可报告内容。约束 1 要求这里的一切异常都被吞掉 ——
    它跑在 write_file 的主路径末尾，一旦抛出就会让一次成功写入看起来像失败。
    """
    if not formatting_enabled():
        return ""
    try:
        changed, name, err = format_file(path)
    except Exception as e:                       # noqa: BLE001 — 见 docstring 约束 1
        return ""
    if err:
        return f"（格式化跳过：{err}）"
    if changed and name:
        return f"（已用 {name} 格式化）"
    if name:
        return f"（{name} 检查通过，无需改动）"
    return ""
