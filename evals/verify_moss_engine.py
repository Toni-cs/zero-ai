# -*- coding: utf-8 -*-
"""MOSS 引擎端到端验证（真实加载 0.9B，走 voice.recognize_audio 调度路径）。"""
from __future__ import annotations

import os
import pathlib
import sys
import time
import wave

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\C\C")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("ZEROAI_ASR_ENGINE", "auto")

from zeroai.core.paths import _ensure_vendored_path  # noqa: E402

_ensure_vendored_path()

ROOT = pathlib.Path(r"D:\C\C")
import av  # noqa: E402
import numpy as np  # noqa: E402

# ── 0. 素材：TTS 合成的已知句子（同 ASR-TTS 闭环测试素材） ──
SAMPLES = [
    ("tts_1.mp3", "今天天气很好，我们一起去公园散步吧。"),
    ("tts_2.mp3", "请帮我查一下北京到上海的高铁票。"),
    ("tts_4.mp3", "Hello, how are you today?"),
]
SRC = pathlib.Path(r"C:\Users\Administrator\AppData\Local\Temp\zeroai_asr_loop")


def load_wave(mp3: pathlib.Path):
    with av.open(str(mp3)) as c:
        st = c.streams.audio[0]
        rs = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        ch = []
        for fr in c.decode(st):
            for o in rs.resample(fr):
                ch.append(o.to_ndarray().reshape(-1))
        for o in rs.resample(None):
            ch.append(o.to_ndarray().reshape(-1))
    wav = np.concatenate(ch).astype(np.float32)
    pk = float(abs(wav).max())
    if pk > 0.99:
        wav = wav / pk
    return wav


def similarity(a: str, b: str) -> float:
    sa, sb = set(a.replace(" ", "")), set(b.replace(" ", ""))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def main() -> int:
    print("=" * 88)
    print("MOSS 端到端 via voice.recognize_audio")
    print("=" * 88)
    for name, sent in SAMPLES:
        p = SRC / name
        print(f"  {'✅' if p.exists() else '❌'} {p}")
        if not p.exists():
            return 1

    import zeroai.tools.voice as V
    import zeroai.tools.asr_moss as M

    assert M.is_available(), "MOSS 引擎路径缺席（vendor/model）"
    print(f"  is_available(): True   vendor={M._vendor_dir()}")
    print(f"                                         model ={M.model_dir()}")

    print()
    print("=" * 88)
    print("1. 首次引擎装入（懒加载 + 冷启动编译均计入）")
    print("=" * 88)
    t0 = time.time()
    st = M.get_model()
    load_s = st["load_s"]
    print(f"  加载 {load_s:.2f}s  供应器 {type(st['processor']).__name__}")
    del st

    # 逐句
    print()
    print("=" * 88)
    print("2. 逐句识别（engine=auto → moss）")
    print("=" * 88)
    rows = []
    for name, sent in SAMPLES:
        wav = load_wave(SRC / name)
        dur = len(wav) / 16000
        t0 = time.time()
        out = V.recognize_audio(wav)
        dt = time.time() - t0
        sim = similarity(sent, out)
        segs = M.last_segments()
        rows.append((sent, out, sim, dt, dur, len(segs)))
        print(f"\n  ({name}) 音频 {dur:.2f}s")
        print(f"     原文: {sent}")
        print(f"     MOSS: {out}")
        print(f"     相似度: {sim:.3f}   耗时: {dt:.2f}s   RTF: {dt / dur:.4f}   "
              f"分段时间戳: {'是' if segs else '无'}")
        for s in segs:
            print(f"       [{s[0]:.2f}-{s[1]:.2f}] {s[2]}  {s[3]!r}")

    print()
    print("=" * 88)
    print("3. 复测一次（确认多次调用之间无状态残留/Leak）")
    print("=" * 88)
    wav = load_wave(SRC / SAMPLES[0][0])
    t0 = time.time()
    out2 = V.recognize_audio(wav)
    dt2 = time.time() - t0
    print(f"  耗时 {dt2:.2f}s  输出 {out2!r}")

    print()
    print("=" * 88)
    print("4. SenseVoice 回退通路（auto 下 moss 抛错应静默替代）")
    print("=" * 88)
    calls = {}
    import zeroai.tools.asr_moss as M2

    def boom(a, sample_rate=16000):
        raise RuntimeError("CUDA outage")
    orig = M2.recognize_audio_moss
    M2.recognize_audio_moss = boom
    try:
        out3 = V.recognize_audio(wav)
        print(f"  回退输出: {out3[:70]}")
        fallback_ok = bool(out3) and "错误" not in out3
    finally:
        M2.recognize_audio_moss = orig
    print(f"  回退可用: {'✅' if fallback_ok else '❌'}")

    # ── 判定 ──
    sims = [r[2] for r in rows]
    avg_sim = sum(sims) / len(sims)
    avg_rt = sum(r[3] for r in rows) / len(rows)
    ok_sim = avg_sim >= 0.8
    ok_fallback = fallback_ok

    print()
    print("=" * 88)
    print("判定")
    print("=" * 88)
    print(f"  平均相似度: {avg_sim:.3f}   {'✅' if ok_sim else '⚠️'}")
    print(f"  平均单句耗时: {avg_rt:.2f}s (engine auto/moss)")
    print(f"  冷启动: {load_s:.2f}s + 首次编译开销")
    print(f"  SenseVoice 回退通路: {'✅' if ok_fallback else '❌'}")
    if ok_sim and ok_fallback:
        print("\n  ✅ MOSS 引擎端到端通过")
        return 0
    print("\n  ⚠️ 有项目未达标，查看上方原始数据")
    return 2


if __name__ == "__main__":
    sys.exit(main())
