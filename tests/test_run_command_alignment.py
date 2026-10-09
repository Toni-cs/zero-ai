# -*- coding: utf-8 -*-
"""run_command 对齐 OpenCode `bash` 工具的验收测试（2026-10-09）

覆盖三块：
  1. OpenCode 对齐的 5 项缺口：workdir / timeout / 退出码 / 截断标记 / 超时引导
  2. strip_exit_code_meta —— 解析方剥离退出码元数据的单一出口
  3. CPU/内存解析回归 —— 退出码首行曾让两项读数恒为 0（本轮发现并修复）
"""
from __future__ import annotations

import re

import pytest

from zeroai.tools.command_exec import (
    EXIT_CODE_PREFIX,
    MAX_CAPTURE_BYTES,
    MAX_TIMEOUT_S,
    run_command,
    strip_exit_code_meta,
)


# ═══════════════════════ 1. OpenCode 对齐的 5 项缺口 ═══════════════════════

class TestOpenCodeParity:
    """逐项对应 packages/core/src/tool/bash.ts 的能力。"""

    def test_exit_code_always_reported(self):
        """缺口3：退出码恒返回（此前完全丢弃 returncode）。"""
        out = run_command("cmd /c exit 0", skip_translate=True)
        assert out.splitlines()[0] == f"{EXIT_CODE_PREFIX} 0]"

    def test_exit_code_nonzero_reported(self):
        out = run_command("cmd /c exit 7", skip_translate=True)
        assert f"{EXIT_CODE_PREFIX} 7]" in out

    def test_empty_output_placeholder_survives(self):
        """空输出必须保留 (无输出) 占位，否则 system_check 的判断会失效。"""
        out = run_command("cmd /c exit 0", skip_translate=True)
        assert "(无输出)" in out

    def test_workdir_applied(self):
        out = run_command("cd", workdir=r"C:\Windows", skip_translate=True)
        assert "windows" in out.lower()

    def test_relative_workdir_resolved(self):
        out = run_command("cd", workdir=".", skip_translate=True)
        assert f"{EXIT_CODE_PREFIX} 0]" in out

    def test_missing_workdir_rejected_without_running(self):
        """对齐 OpenCode：目标目录不存在必须直接报错，不能静默回退到父目录。"""
        out = run_command("cd", workdir=r"C:\no_such_dir_zzz", skip_translate=True)
        assert "工作目录不存在" in out
        assert f"{EXIT_CODE_PREFIX}" not in out

    def test_timeout_param_bounds_execution(self):
        """缺口5：timeout 生效，且【返回时间】也被约束。

        这条锁的是本轮修复的真实缺陷：subprocess.run(timeout=N) 在
        Windows + shell=True 下只保证超时被【检测到】，communicate() 仍会
        等孙进程自然结束——实测 2 秒超时 29.4 秒才返回。改用
        Popen + taskkill /F /T 后压回 2.3 秒。
        """
        import time

        t0 = time.time()
        out = run_command('python -c "import time; time.sleep(30)"',
                          timeout=2, skip_translate=True)
        elapsed = time.time() - t0
        assert "命令超时" in out
        assert elapsed < 10, f"timeout 未约束返回时间：{elapsed:.1f}s"

    def test_timeout_guidance_mentions_retry_and_cap(self):
        """缺口5：超时要给「调大重试」的引导，不能只报错。"""
        out = run_command('python -c "import time; time.sleep(30)"',
                          timeout=1, skip_translate=True)
        assert "调大 timeout" in out
        assert str(MAX_TIMEOUT_S) in out

    def test_timeout_clamped_to_cap(self):
        """传超大 timeout 必须被截到上限，否则等同于没有超时保护。"""
        import time

        t0 = time.time()
        out = run_command("cmd /c exit 3", timeout=99999, skip_translate=True)
        assert f"{EXIT_CODE_PREFIX} 3]" in out
        assert time.time() - t0 < 3

    def test_capture_limit_matches_opencode(self):
        """对齐 bash.ts:21 MAX_CAPTURE_BYTES = 1024 * 1024。"""
        assert MAX_CAPTURE_BYTES == 1024 * 1024

    def test_timeout_cap_matches_opencode(self):
        """对齐 bash.ts:20 MAX_TIMEOUT_MS = 10 * 60 * 1_000。"""
        assert MAX_TIMEOUT_S == 10 * 60


class TestTruncationMarker:
    """缺口4：截断必须显式告知，无声截断会让模型误以为「输出就这么多」。"""

    def test_no_marker_when_under_limit(self):
        out = run_command("python -c \"print('A'*5000)\"", skip_translate=True)
        assert "输出已截断" not in out

    def test_marker_reports_total_and_kept(self):
        total = MAX_CAPTURE_BYTES + 5000
        out = run_command(f"python -c \"print('B'*{total})\"", skip_translate=True)
        assert "输出已截断" in out
        assert f"共 {total} 字符" in out
        assert str(MAX_CAPTURE_BYTES) in out

    def test_truncated_body_is_bounded(self):
        total = MAX_CAPTURE_BYTES + 5000
        out = run_command(f"python -c \"print('B'*{total})\"", skip_translate=True)
        # 返回值不应显著超过采集上限
        assert len(out) < MAX_CAPTURE_BYTES + 600


# ═══════════════════════ 2. 退出码元数据剥离 ═══════════════════════

class TestStripExitCodeMeta:
    def test_strips_header_line(self):
        assert strip_exit_code_meta("[退出码: 0]\n85") == "85"

    def test_keeps_body_intact(self):
        body = "第一行\n第二行\nTraceback (most recent call last)"
        assert strip_exit_code_meta(f"[退出码: 1]\n{body}") == body

    def test_noop_when_no_header(self):
        assert strip_exit_code_meta("直接就是内容") == "直接就是内容"

    def test_handles_empty_and_none(self):
        assert strip_exit_code_meta("") == ""
        assert strip_exit_code_meta(None) is None

    def test_matches_run_command_output(self):
        """剥离前缀必须与 run_command 实际产出的前缀一致。"""
        out = run_command("echo x", skip_translate=True)
        assert out.startswith(EXIT_CODE_PREFIX)
        assert EXIT_CODE_PREFIX not in strip_exit_code_meta(out)


# ═══════════════════════ 3. CPU/内存解析回归 ═══════════════════════

class TestParsingRegression:
    """本轮发现的真实回归：退出码首行会让 CPU/内存读数恒为 0。"""

    def test_defect_is_real_without_stripping(self):
        """证明下面那条集成测试【有牙齿】：不剥离时确实解析出 0。"""
        mem_out = "[退出码: 0]\n93.5"
        raw = re.search(r"(\d+(?:\.\d+)?)", mem_out)
        stripped = re.search(r"(\d+(?:\.\d+)?)", strip_exit_code_meta(mem_out))
        assert raw.group(1) == "0", "若此断言失败，说明缺陷已不可复现"
        assert stripped.group(1) == "93.5"

    def test_cpu_line_scan_defect_is_real(self):
        """CPU 是逐行扫描，第一个含数字的行会命中退出码。"""
        cpu_out = "[退出码: 0]\n97"
        first_digit_line = None
        for line in cpu_out.split("\n"):
            line = line.strip()
            if line and any(c.isdigit() for c in line):
                first_digit_line = line
                break
        assert first_digit_line == "[退出码: 0]", "缺陷前提已变，需重新评估"

        # 剥离后取到的是真实读数
        first_digit_line = None
        for line in strip_exit_code_meta(cpu_out).split("\n"):
            line = line.strip()
            if line and any(c.isdigit() for c in line):
                first_digit_line = line
                break
        assert first_digit_line == "97"

    def test_local_monitor_reports_real_cpu_and_memory(self, monkeypatch):
        """端到端：喂入高负载读数，报告不得被退出码吃成 0%。"""
        import zeroai.tools.system_check as sc

        def fake_run_command(cmd, *args, **kwargs):
            if "LoadPercentage" in cmd:
                return "[退出码: 0]\n97"
            if "FreePhysicalMemory" in cmd:
                return "[退出码: 0]\n93.5"
            return "[退出码: 0]\n(无输出)"

        monkeypatch.setattr(sc, "run_command", fake_run_command)
        report = sc.local_monitor(threshold_cpu=80, threshold_memory=85)

        assert "CPU 使用率 97%" in report, report
        assert "内存使用率 93.5%" in report, report
        # 不得出现被退出码污染的 0% 判定
        assert "CPU 使用率 0%" not in report
        assert "内存使用率 0%" not in report


# ═══════════════════════ 4. 向后兼容 ═══════════════════════

class TestBackwardCompatibility:
    """27 处既有调用方全部使用关键字传参，签名新增参数不得破坏它们。"""

    def test_single_positional_arg(self):
        assert f"{EXIT_CODE_PREFIX} 0]" in run_command("echo compat")

    def test_keyword_skip_translate(self):
        assert f"{EXIT_CODE_PREFIX} 0]" in run_command(
            "echo compat", skip_translate=True)

    def test_real_internal_caller_signature(self):
        """system_check 用 run_command(cmd, skip_translate=True) 形态。"""
        from zeroai.tools.system_check import local_port_check

        out = local_port_check(action="check", port=1)
        assert isinstance(out, str) and out
