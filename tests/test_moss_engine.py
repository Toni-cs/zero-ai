# -*- coding: utf-8 -*-
"""MOSS 引擎调度与回退语义回归测试（不加载真实权重）。"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture()
def one_second_audio():
    rng = np.random.default_rng(0)
    return rng.standard_normal(16000).astype(np.float32) * 0.1


@pytest.fixture()
def patched_engines(monkeypatch):
    """给 voice 的两个底层实现打桩，回传引擎标识便于断言。"""
    import zeroai.tools.voice as V
    import zeroai.tools.asr_moss as M

    calls = {"moss": 0, "sv": 0}
    monkeypatch.setattr(V, "_recognize_audio_sensevoice",
                        lambda a: (calls.__setitem__("sv", calls["sv"] + 1),
                                   "SENSEVOICE")[1])
    monkeypatch.setattr(M, "recognize_audio_moss",
                        lambda a, sample_rate=16000: (
                            calls.__setitem__("moss", calls["moss"] + 1),
                            "MOSS")[1])
    return calls


def test_auto_prefers_moss(patched_engines, one_second_audio):
    import zeroai.tools.voice as V

    monkey_env = os.environ.get("ZEROAI_ASR_ENGINE", "auto")
    os.environ["ZEROAI_ASR_ENGINE"] = "auto"
    try:
        assert V.recognize_audio(one_second_audio) == "MOSS"
    finally:
        os.environ["ZEROAI_ASR_ENGINE"] = monkey_env


def test_engine_sensevoice_skips_moss(patched_engines, one_second_audio):
    import zeroai.tools.voice as V

    os.environ["ZEROAI_ASR_ENGINE"] = "sensevoice"
    try:
        assert V.recognize_audio(one_second_audio) == "SENSEVOICE"
    finally:
        os.environ["ZEROAI_ASR_ENGINE"] = "auto"
    assert patched_engines == {"moss": 0, "sv": 1}


def test_auto_falls_back_on_moss_error(patched_engines, one_second_audio,
                                       monkeypatch):
    import zeroai.tools.voice as V
    import zeroai.tools.asr_moss as M

    os.environ["ZEROAI_ASR_ENGINE"] = "auto"
    try:
        def boom(a, sample_rate=16000):
            raise RuntimeError("CUDA out of memory")
        monkeypatch.setattr(M, "recognize_audio_moss", boom)
        assert V.recognize_audio(one_second_audio) == "SENSEVOICE"
    finally:
        os.environ["ZEROAI_ASR_ENGINE"] = "auto"


def test_moss_forced_raises_on_failure(patched_engines, one_second_audio,
                                       monkeypatch):
    import zeroai.tools.voice as V
    import zeroai.tools.asr_moss as M

    os.environ["ZEROAI_ASR_ENGINE"] = "moss"
    try:
        def boom(a, sample_rate=16000):
            raise RuntimeError("CUDA out of memory")
        monkeypatch.setattr(M, "recognize_audio_moss", boom)
        with pytest.raises(RuntimeError):
            V.recognize_audio(one_second_audio)
    finally:
        os.environ["ZEROAI_ASR_ENGINE"] = "auto"


def test_engine_choice_accepts_only_known_values(monkeypatch):
    import zeroai.tools.voice as V

    for raw, want in [("", "auto"), ("moss", "moss"),
                      ("SENSEVOICE", "sensevoice"),
                      ("junk!!", "auto"), ("auto ", "auto")]:
        os.environ["ZEROAI_ASR_ENGINE"] = raw
        try:
            assert V._engine_choice() == want
        finally:
            os.environ["ZEROAI_ASR_ENGINE"] = "auto"


def test_empty_audio_fast_returns(monkeypatch, patched_engines):
    import zeroai.tools.voice as V

    # 空 array 不应该触发引擎加载
    assert V.recognize_audio(np.zeros(0, np.float32)) == "（未识别到内容）"
    assert patched_engines == {"moss": 0, "sv": 0}
