# -*- coding: utf-8 -*-
"""MCP stdio 传输的 UTF-8 编码回归测试。

背景（2026-10-10，接通 OpenCode 底座时实测发现）
==========================================================================
把 ZeroAI 挂成 OpenCode 的 MCP server 后，客户端能正常握手、能列出 65 个工具，
但 ``tools/call`` 的返回值全是乱码::

    实际:  ϵͳ��Windows 11
    应为:  系统：Windows 11

根因（已逐行定位，非猜测）：``server.py`` 的 ``run_stdio`` 用文本流写回::

    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\\n")

``sys.stdout`` 的编码由 ``locale.getpreferredencoding()`` 决定 —— Windows 中文
环境实测为 ``cp936``（本文件运行时已确认），于是中文被编成 GBK 字节；而 MCP
规范要求 stdio 传输使用 UTF-8，客户端按 UTF-8 解码即得 ``ϵͳ``（GBK 字节
``CF B5 CD B8`` 被解成希腊字母）。

原代码里的这段处理**只解决了一半**::

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

``O_BINARY`` 只禁用 CRLF 换行转换，**不改变文本流的字符编码**。工具名是 ASCII
所以一路绿灯，一旦工具返回中文立刻崩 —— 这也是它此前没被发现的原因。

修复：读写两侧一律走 ``.buffer`` 二进制流并显式 UTF-8 编解码。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zeroai.mcp.server import MCPServer, create_zeroai_server  # noqa: E402


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _mojibake(s: str) -> list[str]:
    """挑出 UTF-8/cp936 混淆的指纹字符。

    判据只认真正的失败特征：替换符 U+FFFD、希腊字母区、西里尔字母区。
    中文技术文本里合法出现的全角标点（U+FF00-U+FFEF）、箭头 ``→``（U+2192）、
    破折号等一律放行 —— 首版判据把 ``ssh_deploy`` 描述里的 ``→`` 误判为乱码，
    属于判据过宽，已收紧。
    """
    bad = []
    for c in s:
        o = ord(c)
        if c == "�":
            bad.append(c)
        elif 0x0370 <= o <= 0x03FF:  # 希腊字母
            bad.append(c)
        elif 0x0400 <= o <= 0x04FF:  # 西里尔
            bad.append(c)
    return bad


# ---------------------------------------------------------------------------
# 1. 单元层：_write_json_line 必须产出 UTF-8 字节
# ---------------------------------------------------------------------------
class TestWriteJsonLineEncoding:
    def test_writes_utf8_bytes_not_locale_encoding(self):
        """含中文的负载必须以 UTF-8 字节落到流上，而不是 cp936。"""
        buf = io.BytesIO()
        MCPServer._write_json_line(buf, {"text": "系统：Windows 11"})
        raw = buf.getvalue()

        assert raw.endswith(b"\n"), "MCP stdio 按行分隔，必须以换行结尾"
        body = raw[:-1]

        # 正确性：按 UTF-8 解码能还原中文
        decoded = json.loads(body.decode("utf-8"))
        assert decoded["text"] == "系统：Windows 11"

        # 排他性：同一串字节若按 cp936 解码必然失真，证明它不是 GBK
        assert body != "系统：Windows 11".encode("cp936"), (
            "输出仍是 cp936 编码 —— Windows 中文环境的默认行为，"
            "这正是本次回归要防的缺陷"
        )

    def test_pure_ascii_payload_unchanged(self):
        """纯 ASCII 负载不受影响（UTF-8 与 cp936 对 ASCII 等价）。"""
        buf = io.BytesIO()
        MCPServer._write_json_line(buf, {"ok": True, "n": 65})
        assert buf.getvalue() == b'{"ok": true, "n": 65}\n'

    def test_no_mojibake_fingerprint(self):
        """写出去的中文不得含 UTF-8/cp936 混淆的指纹字符。"""
        buf = io.BytesIO()
        MCPServer._write_json_line(
            buf, {"t": "架构：AMD64  内存：20.7%（13474MB / 64957MB）"}
        )
        text = buf.getvalue()[:-1].decode("utf-8")
        assert _mojibake(text) == []

    def test_falls_back_to_text_stream_when_buffer_is_none(self):
        """buffer 缺失（某些重定向场景）时退回文本流，不得抛异常。"""
        buf = None  # 模拟 getattr(sys.stdout, "buffer", None) 返回 None
        MCPServer._write_json_line(buf, {"x": 1})  # 不应抛出


# ---------------------------------------------------------------------------
# 2. 单元层：日志写入同样强制 UTF-8
# ---------------------------------------------------------------------------
class TestLogEncoding:
    def test_write_log_does_not_raise(self, capsys):
        """_write_log 在任何环境下都不得抛异常。"""
        MCPServer._write_log("[ZeroAI MCP Server] 启动 stdio 模式\n")


# ---------------------------------------------------------------------------
# 3. 集成层：真实子进程往返（与 OpenCode 的调用方式一致）
# ---------------------------------------------------------------------------
class TestStdioRoundTripUTF8:
    """从「非仓库」cwd 启动子进程，模拟 OpenCode 的 spawn 方式。

    OpenCode 以 workspace 为 cwd 拉起 ``python -m zeroai.mcp``；若换 cwd
    或换编码就出问题，配置等于白写。
    """

    @staticmethod
    def _spawn():
        return subprocess.Popen(
            [sys.executable, "-m", "zeroai.mcp"],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def _send(proc, obj):
        proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        proc.stdin.flush()

    @staticmethod
    def _recv(proc, timeout=60.0):
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = proc.stdout.readline()
            if not raw:
                return None
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def test_initialize_and_tools_list(self):
        """握手 + 列工具：65 个工具，且中文描述不得含乱码指纹。"""
        proc = self._spawn()
        try:
            self._send(proc, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            })
            init = self._recv(proc)
            assert init is not None, "initialize 无响应"
            info = init["result"]["serverInfo"]
            assert info["name"] == "zeroai"

            self._send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._send(proc, {"jsonrpc": "2.0", "id": 2,
                              "method": "tools/list", "params": {}})
            tools = self._recv(proc)["result"]["tools"]
            assert len(tools) == 65

            for t in tools:
                desc = t.get("description") or ""
                assert _mojibake(desc) == [], (
                    f"工具 {t['name']} 描述含乱码指纹: {_mojibake(desc)}"
                )
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=15)

    def test_tool_result_with_chinese_is_clean(self):
        """真实调用含中文返回的工具，返回值必须是可读中文。

        这是本次缺陷的原始现象：``system_info`` 返回 ``ϵͳ��Windows 11``。
        """
        proc = self._spawn()
        try:
            self._send(proc, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            })
            assert self._recv(proc) is not None
            self._send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

            self._send(proc, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "system_info", "arguments": {}},
            })
            res = self._recv(proc)
            assert res is not None, "tools/call 无响应"

            content = res.get("result", {}).get("content") or []
            text = "".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
            assert text, "system_info 返回为空"
            assert _mojibake(text) == [], (
                f"返回含乱码指纹 {_mojibake(text)}，原文: {text!r}"
            )
            for kw in ("系统", "架构"):
                assert kw in text, f"返回缺少预期中文词「{kw}」，原文: {text!r}"
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=15)


# ---------------------------------------------------------------------------
# 4. 结构锁：不得再退回文本流写法
# ---------------------------------------------------------------------------
class TestNoTextStreamRegression:
    def test_run_stdio_source_uses_binary_buffer(self):
        """源码层面锁死：run_stdio 必须走二进制流，不得直接用文本流读写。

        注意断言匹配的是真实写法 ``getattr(sys.stdin, "buffer", None)``；
        首版断言写成 ``".buffer" in body`` 属于判据错误（源码里是
        ``, "buffer"`` 而非 ``.buffer``），已按实际形态修正。
        """
        import zeroai.mcp.server as mod

        path = Path(mod.__file__)
        text = path.read_text(encoding="utf-8")

        fn_start = text.index("async def run_stdio")
        fn_end = text.index("async def run_sse")
        body = text[fn_start:fn_end]

        assert 'getattr(sys.stdin, "buffer"' in body, (
            "run_stdio 未从 stdin 取二进制流 —— 会退回 locale 编码（Windows 下为 cp936）"
        )
        assert 'getattr(sys.stdout, "buffer"' in body, (
            "run_stdio 未从 stdout 取二进制流 —— 中文返回会被编成 GBK"
        )
        assert 'decode("utf-8"' in body, "run_stdio 未显式按 UTF-8 解码输入"
        assert "sys.stdout.write(" not in body, (
            "run_stdio 不得直接用文本流 sys.stdout.write —— 这正是原缺陷"
        )

    def test_create_zeroai_server_registers_all_tools(self):
        """服务端注册的工具数应与 registry 一致（65）。"""
        server = create_zeroai_server()
        assert len(server._tools) == 65
