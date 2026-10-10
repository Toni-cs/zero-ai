# -*- coding: utf-8 -*-
"""VAD 时间语义回归测试（不依赖麦克风/模型/网络）。

背景（2026-10-10）：voice.py 原先用
    max_silence_frames = int(silence_seconds * sample_rate / block_size)
算出"音频块数"（int(1.0*16000/1024)=15），却用 silence_count += 1
按"循环迭代"（每次 time.sleep(0.03)）累加，名义 1.0 秒的静音阈值
实际只有 15 × 0.03 = 0.45 秒。真人说话稍作停顿（如
"今天天气很好，<停>我们一起去公园散步吧"）就被中途掐断，
识别只剩前半句——用户实测"今天天气很好，我们一起去公"即此因。

与已修复的 pre-wait 缺陷（voice.py PRE_WAIT_SECONDS）属同一单位错配。

本测试打桩 sounddevice.InputStream，按真实块速率（1024/16000 = 64ms/块）
投放合成音频，不依赖麦克风、SenseVoice 模型或 faster-whisper：

  1. 静音段 < silence_seconds 时不得截断（两段话都保留）
  2. 静音段 >= silence_seconds 时按时截断
  3. 源码不再含旧的块数公式（非注释行）
"""
from __future__ import annotations

import ast
import pathlib
import sys
import threading
import time
import types
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from zeroai.core.paths import _ensure_vendored_path

    _ensure_vendored_path()
    import zeroai.tools.voice as voice
except Exception as _e:  # pragma: no cover - 环境缺失时给出可读信息
    pytest.skip(f"voice 模块不可用: {_e}", allow_module_level=True)

SR = 16000
BLOCK = 1024


class _FakeInputStream:
    """替代 sounddevice.InputStream，覆盖 listen_asr 的两种用法。

    用法 1（噪声采样）：with sd.InputStream(...) as stream:
                          for _: stream.read(block_size)
    用法 2（正式录音）：with sd.InputStream(..., callback=cb, ...):
                          循环读 frames
    进入用法 2 时按真实块时长（64ms）投放音频块，
    使 voice.py 里的 time.time() 计时语义得以真实检验。
    """

    def __init__(self, blocks, samplerate=SR, channels=1, blocksize=BLOCK,
                 callback=None, device=None, **_kwargs):
        self._blocks = list(blocks)
        self._cb = callback
        self._bs = int(blocksize or BLOCK)
        self._i = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t = None

    # ── 用法 1：噪声采样 ──
    def read(self, n):
        with self._lock:
            if self._i >= len(self._blocks):
                return np.zeros((n, 1), np.float32), True
            out = self._blocks[self._i][:n]
            self._i += 1
            return out.copy(), False

    # ── 用法 2：正式录音 ──
    def __enter__(self):
        if self._cb is not None:
            self._start_delivery()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=2.0)
        return False

    def _start_delivery(self):
        def run():
            i = 0
            pace = self._bs / SR  # 0.064s，真实块时长
            while not self._stop.is_set():
                with self._lock:
                    if i >= len(self._blocks):
                        blk = self._blocks[-1] if self._blocks else \
                            np.zeros((self._bs, 1), np.float32)
                    else:
                        blk = self._blocks[i]
                        i += 1
                try:
                    self._cb(blk, len(blk), None, None)
                except Exception:
                    pass
                time.sleep(pace)

        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()


def _make_blocks() -> list[np.ndarray]:
    """5 块噪声 + 8 块话音(0.512s) + 11 块静音(0.704s)
    + 8 块话音(0.512s) + 20 块静音(1.28s) = 52 块 ≈ 3.33s"""
    blocks = []
    for _ in range(5):  # 环境噪声（喂给 0.3s 噪声采样）
        blocks.append(np.full((BLOCK, 1), 0.005, dtype=np.float32))
    for _ in range(8):  # 话音段 1
        blocks.append(np.full((BLOCK, 1), 0.5, dtype=np.float32))
    for _ in range(11):  # 停顿 0.704s（介于 0.3 与 1.0 之间）
        blocks.append(np.zeros((BLOCK, 1), dtype=np.float32))
    for _ in range(8):  # 话音段 2
        blocks.append(np.full((BLOCK, 1), 0.5, dtype=np.float32))
    for _ in range(20):  # 结尾静音（让 silence_seconds=1.0 能触发）
        blocks.append(np.zeros((BLOCK, 1), dtype=np.float32))
    return blocks


@pytest.fixture()
def captured(monkeypatch):
    """打桩 sounddevice.InputStream 与 recognize_audio，返回收集容器。"""
    state = {"audio": None, "durations": []}
    blocks = _make_blocks()

    def fake_recognize(audio):
        audio = np.asarray(audio).reshape(-1)
        state["audio"] = audio
        state["durations"].append(len(audio) / SR)
        # 返回时长作文本，便于断言
        return f"{len(audio) / SR:.2f}"

    monkeypatch.setattr(voice, "recognize_audio", fake_recognize)
    import sounddevice as _sd

    monkeypatch.setattr(_sd, "InputStream",
                        lambda **kw: _FakeInputStream(blocks, **kw))
    return state


def test_gap_below_silence_seconds_is_not_truncated(captured):
    """0.704s 的停顿 < silence_seconds=1.0 → 两段话音都必须保留。"""
    text = voice.listen_asr(max_seconds=10, silence_seconds=1.0)
    dur = float(text)
    # 修复前：0.45s 实际阈值 → 在停顿里被截断，音频 ≈ 1.2s
    # 修复后：应至少保留两段话音 + 1.0s 结尾静音
    assert dur >= 2.0, (
        f"静音段 0.704s 在 silence_seconds=1.0 下被截断（仅录得 {dur:.2f}s）"
        "——时间语义未生效，silence_seconds 的'秒'语义回归失败")


def test_gap_above_silence_seconds_is_truncated(captured):
    """0.704s 的停顿 > silence_seconds=0.3 → 应在停顿内按时截断。"""
    text = voice.listen_asr(max_seconds=10, silence_seconds=0.3)
    dur = float(text)
    # 0.3s 静音阈值 → 只保留话音段 1（约 0.8s 处进入停顿，再 0.3s 截断）
    assert dur <= 1.5, (
        f"silence_seconds=0.3 未按时截断（录得 {dur:.2f}s，应 ~1.2s）"
        "——静音判定过松")


def test_source_has_no_block_count_formula():
    """源码结构锁：块数公式不得作为可执行代码出现（修复说明注释除外）。"""
    src = pathlib.Path(voice.__file__).read_text(encoding="utf-8")
    offenders = [ln for ln in src.splitlines()
                 if "max_silence_frames = int(" in ln
                 and not ln.lstrip().startswith("#")]
    assert not offenders, (
        f"检测到按'块'计数的静音公式仍在可执行代码中: {offenders}")


def test_source_uses_time_based_silence():
    """源码结构锁：静音判定必须基于时间比较。"""
    src = pathlib.Path(voice.__file__).read_text(encoding="utf-8")
    assert "time.time() - silence_start >= max_silence" in src, (
        "静音判定应基于 time.time() 与 silence_seconds 的比较")
