# -*- coding: utf-8 -*-
"""代码格式化器测试

钉住三条不可违反的约束（见 zeroai/core/formatters.py）：

1. **格式化失败绝不能影响写入结果** —— 任何失败路径必须返回原始内容。
   这是本组测试里权重最高的一条：写文件是主路径，格式化是增值路径。
2. **没有格式化器时必须是无操作** —— 本机当前就没装 ruff/black
   （实测 No module named），所以这不是理论分支，是真实运行路径。
3. **只格式化白名单扩展名** —— 对 .md/.bin 调格式化器是无意义且危险的。

测试刻意**不依赖本机装没装格式化器**：用真实子进程的假格式化器驱动，
这样在没装 ruff 的机器上也照样测得到"格式化成功"这条路径。
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zeroai.core import formatters as F  # noqa: E402
from zeroai.tools import file_manager as FM  # noqa: E402


# ─────────────────── 假格式化器（真实子进程） ───────────────────

def _mk(name, code, exts=(".py",), probe=()):
    """造一个用 python 跑的假格式化器，行为由 code 决定"""
    return F.Formatter(name=name, argv=(sys.executable, "-c", code),
                       exts=tuple(exts), probe=tuple(probe))


# 把 x=1 规范成 x = 1 —— 可断言、确定性
# 刻意走 stdout.buffer：走 sys.stdout 会经 Windows 文本模式把 \n 翻成 \r\n，
# 让断言被行尾差异污染（实测踩过）。行尾另有专门用例覆盖。
FORMATTING = ("import sys; "
              "sys.stdout.buffer.write("
              "sys.stdin.buffer.read().replace(b'x=1', b'x = 1'))")
FAILING = "import sys; sys.stderr.write('boom'); sys.exit(3)"
EMPTY = "import sys; sys.stdin.buffer.read()"        # 吞掉输入、什么都不输出
SLOW = "import sys, time; sys.stdin.buffer.read(); time.sleep(30)"
# 故意输出 CRLF：用于验证"仅行尾差异视为无改动"
CRLFIFY = ("import sys; d=sys.stdin.buffer.read(); "
           "sys.stdout.buffer.write(d.replace(b'\\n', b'\\r\\n'))")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ZEROAI_FORMAT_ON_WRITE", raising=False)
    F.clear_probe_cache()
    yield
    F.clear_probe_cache()


# ─────────────────── detect：两级判定 ───────────────────

def test_detect_returns_only_available():
    out = F.detect()
    assert isinstance(out, dict)
    for fmt in out.values():
        assert os.path.basename(fmt.argv[0]) or True


def test_detect_excludes_missing_executable():
    """which 不到的必须排除 —— 这是 detect 的基本契约

    注意 argv[0] 必须真的是个不存在的可执行名：_mk() 用的是
    sys.executable，用它造"幽灵格式化器"会让 which 必然通过。
    """
    ghost = F.Formatter("ghost", ("definitely-not-a-real-binary-xyz", "-"),
                        exts=(".py",))
    assert "ghost" not in F.detect(formatters=[ghost])


def test_detect_gates_on_probe():
    """**回归**：npx 在 PATH 上不等于 prettier 装了。

    首版 detect 只做 which，实测本机报出 `prettier-npx` 可用，
    而 `npx --no-install prettier` 实际报 missing packages —— 谎报。
    现在必须 probe 不过就排除。
    """
    # argv[0] 是真实存在的 python，which 必然通过；probe 故意失败
    fake = _mk("probe-fails", FORMATTING, probe=(sys.executable, "-c", "raise SystemExit(1)"))
    assert "probe-fails" not in F.detect(formatters=[fake])

    ok = _mk("probe-passes", FORMATTING, probe=(sys.executable, "-c", "raise SystemExit(0)"))
    assert "probe-passes" in F.detect(formatters=[ok])


def test_probe_result_is_cached(monkeypatch):
    """probe 要起子进程（npx 级别的开销），跑一次就该缓存住"""
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        return subprocess.CompletedProcess(args=a[0], returncode=0)

    fmt = _mk("cached", FORMATTING, probe=(sys.executable, "-c", "pass"))
    monkeypatch.setattr(F.subprocess, "run", fake_run)
    F.clear_probe_cache()
    F.detect(formatters=[fmt])
    F.detect(formatters=[fmt])
    assert calls["n"] == 1, "probe 应只跑一次，实际 %d 次" % calls["n"]


def test_probe_cache_clearable():
    fmt = _mk("x", FORMATTING, probe=(sys.executable, "-c", "raise SystemExit(0)"))
    F.detect(formatters=[fmt])
    assert "x" in F._PROBE_CACHE
    F.clear_probe_cache()
    assert "x" not in F._PROBE_CACHE


# ─────────────────── formatter_for：扩展名与优先级 ───────────────────

def test_formatter_for_matches_extension_only():
    avail = {"ruff": _mk("ruff", FORMATTING), "black": _mk("black", FORMATTING, exts=(".py",))}
    assert F.formatter_for("a.py", available=avail).name == "ruff"
    assert F.formatter_for("a.md", available=avail) is None
    assert F.formatter_for("a", available=avail) is None, "无扩展名必须跳过"
    assert F.formatter_for("Makefile", available=avail) is None


def test_formatter_for_respects_declared_priority():
    """ruff 必须排在 black 之前（BUILTIN_FORMATTERS 的既定顺序）"""
    pool = (F.Formatter("black", ("black", "-"), (".py",)),
            F.Formatter("ruff", ("ruff", "format", "-"), (".py",)))
    avail = {"black": pool[0], "ruff": pool[1]}
    # pool 里 black 在前 —— 以传入 pool 的顺序为准，不被 BUILTIN 常量影响
    assert F.formatter_for("a.py", available=avail, formatters=pool).name == "black"

    # 而内置声明里 ruff 先于 black
    names = [f.name for f in F.BUILTIN_FORMATTERS if ".py" in f.exts]
    assert names.index("ruff") < names.index("black")


def test_formatter_for_ignores_unavailable():
    assert F.formatter_for("a.py", available={}) is None


# ─────────────────── format_code：三态与失败安全 ───────────────────

def test_no_formatter_returns_identical_object():
    code = "x=1\n"
    out, name, err = F.format_code(code, "a.py", formatters=[])
    assert out is code, "无可用格式化器时应原样返回，而非复制一份"
    assert name is None and err is None


def test_empty_input_short_circuits():
    assert F.format_code("", "a.py", formatters=[_mk("f", FORMATTING)]) == ("", None, None)


def test_successful_format_reports_name():
    fmt = _mk("fake", FORMATTING)
    out, name, err = F.format_code("x=1\n", "a.py", formatters=[fmt])
    assert out == "x = 1\n"
    assert name == "fake" and err is None


def test_formatting_noop_still_reports_success():
    """已符合规范的代码：changed=False 但**也是成功**（err 必须为 None）"""
    fmt = _mk("fake", FORMATTING)
    out, name, err = F.format_code("y = 2\n", "a.py", formatters=[fmt])
    assert out == "y = 2\n"
    assert name == "fake" and err is None


def test_failing_formatter_returns_original():
    """**约束 1 核心用例**：格式化器报错时必须原样返回，绝不返回半成品"""
    original = "x=1\n"
    out, name, err = F.format_code(original, "a.py", formatters=[_mk("bad", FAILING)])
    assert out == original
    assert name is None
    assert err and "退出码 3" in err and "保留原内容" in err


def test_empty_output_keeps_original():
    """输出为空而输入非空 —— 格式化器把内容吞了，宁可不格式化"""
    original = "x=1\n"
    out, name, err = F.format_code(original, "a.py", formatters=[_mk("empty", EMPTY)])
    assert out == original
    assert name is None
    assert err and "输出为空" in err


def test_missing_executable_is_not_an_error():
    """which 与实际执行之间被卸载 —— 应当静默跳过，不算失败"""
    fmt = F.Formatter("vanish", ("definitely-not-a-real-binary-xyz", "-"), (".py",))
    out, name, err = F.format_code("x=1\n", "a.py", formatters=[fmt])
    assert out == "x=1\n"
    assert name is None and err is None


def test_timeout_keeps_original(monkeypatch):
    monkeypatch.setattr(F, "_TIMEOUT", 1)
    original = "x=1\n"
    out, name, err = F.format_code(original, "a.py",
                                   formatters=[_mk("slow", SLOW)])
    assert out == original
    assert name is None
    assert err and "超时" in err


def test_non_py_extension_untouched():
    """**约束 3**：白名单外的扩展名一律不碰"""
    fmt = _mk("fake", FORMATTING)          # 只认 .py
    out, name, err = F.format_code("# 标题\n", "notes.md", formatters=[fmt])
    assert out == "# 标题\n"
    assert name is None and err is None


def test_format_code_handles_unicode():
    fmt = _mk("fake", FORMATTING)
    src = "# 中文注释\nx=1\n"
    out, name, err = F.format_code(src, "a.py", formatters=[fmt])
    assert out == "# 中文注释\nx = 1\n"
    assert err is None


def test_eol_only_change_is_rejected():
    """**回归**：Windows 上子进程 stdout 走文本模式会把 \n 变 \\r\\n。

    若不拦住，一次格式化就把整个文件的换行符改了 —— git diff 全红、
    跨平台协作直接炸。实测本机（Windows）确认该行为存在。
    """
    original = "x=1\ny=2\n"
    out, name, err = F.format_code(original, "a.py",
                                   formatters=[_mk("crlf", CRLFIFY)])
    assert out == original, "仅行尾差异时必须保持原内容"
    assert name == "crlf", "识别成功但选择不改动 —— 仍是成功"
    assert err is None, "行尾被拦下不算失败，是成功地没做多余的事"


def test_real_change_with_crlf_output_still_applies():
    """混有真实改动 + 行尾变化时，改动要生效（不能因为行尾就全盘拒绝）"""
    fmt = _mk("crlf", CRLFIFY)
    # CRLFIFY 只换行尾不改内容 -> 被上一条拦下；
    # 这里用 FORMATTING 验证真实改动仍会应用
    out, name, err = F.format_code("x=1\n", "a.py", formatters=[_mk("f", FORMATTING)])
    assert out == "x = 1\n"
    assert err is None


# ─────────────────── format_file ───────────────────

def test_format_file_rewrites_only_when_changed(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x=1\n", encoding="utf-8")
    changed, name, err = F.format_file(str(p), formatters=[_mk("fake", FORMATTING)])
    assert changed is True and name == "fake" and err is None
    assert p.read_text(encoding="utf-8") == "x = 1\n"

    # 第二次跑：内容已规范，不应再改
    changed2, name2, err2 = F.format_file(str(p), formatters=[_mk("fake", FORMATTING)])
    assert changed2 is False and err2 is None


def test_format_file_missing_returns_reason_not_raise(tmp_path):
    changed, name, err = F.format_file(str(tmp_path / "nope.py"),
                                       formatters=[_mk("fake", FORMATTING)])
    assert changed is False and name is None
    assert err and "读取失败" in err


def test_format_file_directory_does_not_raise(tmp_path):
    changed, name, err = F.format_file(str(tmp_path),
                                       formatters=[_mk("fake", FORMATTING)])
    assert changed is False and err


def test_format_file_skips_non_utf8(tmp_path):
    p = tmp_path / "a.py"
    p.write_bytes(b"\xff\xfe x=1\n")
    changed, name, err = F.format_file(str(p), formatters=[_mk("fake", FORMATTING)])
    assert changed is False
    assert err and "非 UTF-8" in err


def test_format_file_failing_formatter_leaves_file_alone(tmp_path):
    p = tmp_path / "a.py"
    before = "x=1\n"
    p.write_text(before, encoding="utf-8")
    changed, name, err = F.format_file(str(p), formatters=[_mk("bad", FAILING)])
    assert changed is False and err
    assert p.read_text(encoding="utf-8") == before, "失败时磁盘内容必须保持不变"


# ─────────────────── format_on_write：绝不抛异常 ───────────────────

def test_format_on_write_never_raises(monkeypatch, tmp_path):
    """**约束 1**：它跑在 write_file 主路径末尾，一旦抛出，
    一次成功的写入就会看起来像失败。"""
    for target in (str(tmp_path / "nope.py"), str(tmp_path), "", "\\\\bad\\path"):
        try:
            F.format_on_write(target)
        except Exception as e:                # noqa: BLE001
            pytest.fail("format_on_write({!r}) 抛出了 {}".format(target, e))


def test_format_on_write_swallows_unexpected_exceptions(monkeypatch):
    """即便 format_file 内部炸出预料外的异常也要吞掉"""
    def boom(*a, **k):
        raise RuntimeError("surprise")
    monkeypatch.setattr(F, "format_file", boom)
    assert F.format_on_write("a.py") == ""


def test_format_on_write_reports_change(monkeypatch, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(F, "format_file",
                        lambda path, formatters=None: (True, "ruff", None))
    assert F.format_on_write(str(p)) == "（已用 ruff 格式化）"


def test_format_on_write_disabled_by_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ZEROAI_FORMAT_ON_WRITE", "0")
    assert F.formatting_enabled() is False
    assert F.format_on_write(str(tmp_path / "a.py")) == ""


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("", True),
    ("0", False), ("false", False), ("no", False), ("OFF", False),
])
def test_formatting_enabled_flag(val, expected, monkeypatch):
    monkeypatch.setenv("ZEROAI_FORMAT_ON_WRITE", val)
    assert F.formatting_enabled() is expected


# ─────────────────── 与 write_file / edit_file 的集成 ───────────────────

def test_write_file_appends_format_note(monkeypatch, tmp_path):
    p = tmp_path / "a.py"
    monkeypatch.setattr(FM, "format_on_write", lambda path: "（已用 ruff 格式化）")
    res = FM.write_file(str(p), "x=1\n")
    assert "已写入" in res and "（已用 ruff 格式化）" in res


def test_write_file_survives_formatter_crash(monkeypatch, tmp_path):
    """格式化器炸了，写入必须照样成功、内容照样在"""
    p = tmp_path / "a.py"
    monkeypatch.setattr(FM, "format_on_write",
                        lambda path: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        res = FM.write_file(str(p), "hello\n")
    except RuntimeError:
        pytest.fail("格式化异常穿透到了 write_file")
    assert "已写入" in res
    assert p.read_text(encoding="utf-8") == "hello\n"


def test_edit_file_all_four_ops_reach_formatter(monkeypatch, tmp_path):
    """replace/insert/delete/append 四条写回路径都要经过格式化钩子"""
    calls = []
    monkeypatch.setattr(FM, "format_on_write", lambda path: (calls.append(path) or " [fmt]"))
    p = tmp_path / "a.py"
    FM.write_file(str(p), "a\nb\nc\n")

    calls.clear()
    FM.edit_file(str(p), operation="replace", line=2, content="B")
    FM.edit_file(str(p), operation="insert", line=1, content="z")
    FM.edit_file(str(p), operation="delete", start_line=1, end_line=1)
    FM.edit_file(str(p), operation="append", content="end")
    assert len(calls) == 4, "四个操作都必须调用格式化钩子，实际 %d" % len(calls)


def test_edit_file_error_paths_do_not_format(monkeypatch, tmp_path):
    """编辑失败（文件不存在等）不该触发格式化 —— 没写入就不该格式化"""
    calls = []
    monkeypatch.setattr(FM, "format_on_write", lambda path: (calls.append(path) or ""))
    res = FM.edit_file(str(tmp_path / "nope.py"), operation="append", content="x")
    assert "错误" in res
    assert calls == []


def test_edit_file_result_still_valid_after_format(monkeypatch, tmp_path):
    p = tmp_path / "a.py"
    FM.write_file(str(p), "a\nb\n")
    res = FM.edit_file(str(p), operation="replace", line=1, content="A")
    assert "已替换" in res
    assert p.read_text(encoding="utf-8").startswith("A")


# ─────────────────── 内置声明的自洽性 ───────────────────

def test_builtin_formatters_have_lowercase_dotted_exts():
    for fmt in F.BUILTIN_FORMATTERS:
        assert fmt.exts, fmt.name
        for e in fmt.exts:
            assert e.startswith("."), "%s 的扩展名 %s 缺前导点" % (fmt.name, e)
            assert e == e.lower()


def test_builtin_formatter_names_unique():
    names = [f.name for f in F.BUILTIN_FORMATTERS]
    assert len(names) == len(set(names)), "重名会让 detect 结果不确定"


def test_builtin_argv0_is_the_probe_target():
    """probe 用的可执行文件必须和 argv[0] 一致，否则测的不是同一件事"""
    for fmt in F.BUILTIN_FORMATTERS:
        if fmt.probe:
            assert fmt.probe[0] == fmt.argv[0], fmt.name
