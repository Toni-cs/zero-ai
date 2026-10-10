# -*- coding: utf-8 -*-
"""ZeroAI 语音闭环人工验证 —— 麦克风 -> 文字（listen_asr 完整链路）。

自动回环在本机不可行（立体声混音/虚拟设备/声学耦合均已实测失败），
最后一段必须由真人对着麦克风说。

用法：
    python verify_voice_manual.py

按回车 -> 开始听 -> 对着麦克风说一句话 -> 等结果。
按 Ctrl+C 结束。

判据（三档）：
    ✅ 相似度 >= 0.50   麦克风->文字 已确证
    ⚠️ 相似度 >= 0.20   识别到但有偏差，看原句/识别句是否同义
    ❌ 相似度 <  0.20   未通过
"""
from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\C\C")

from zeroai.core.paths import _ensure_vendored_path  # noqa: E402

_ensure_vendored_path()

# 固定测试句，便于客观比对
SENTENCE = "今天天气很好，我们一起去公园散步吧。"


def similarity(a: str, b: str) -> float:
    """字符级 Jaccard 相似度。"""
    sa, sb = set(a.replace(" ", "")), set(b.replace(" ", ""))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def main() -> int:
    print("=" * 78)
    print("ZeroAI 语音闭环验证：麦克风 -> listen_asr -> 文字")
    print("=" * 78)

    # 预热识别器，避免加载耗时挤占 VAD 窗口
    from zeroai.tools.voice import get_asr_model, listen_asr

    print("\n[1] 预热 SenseVoice 识别器 ...")
    t0 = time.time()
    try:
        get_asr_model()
        print(f"     OK {time.time() - t0:.2f}s")
    except Exception as e:
        print(f"     ❌ 失败: {type(e).__name__}: {e}")
        return 1

    print("\n[2] 请准备：相对安静的环境，麦克风已启用")
    print(f"\n    请照读这句：{SENTENCE}")

    trial = 0
    best = 0.0
    while True:
        trial += 1
        try:
            input(f"\n--- 第 {trial} 次 | 按回车开始听（说完自动停） --- ")
        except (EOFError, KeyboardInterrupt):
            print("\n结束。")
            break

        print("  正在听（最长 10s，3 秒内开口即可；句中可停顿至多 1 秒）...")
        t0 = time.time()
        try:
            hyp = listen_asr(max_seconds=10, silence_seconds=1.0)
        except KeyboardInterrupt:
            print("  已中断。")
            break
        except Exception as e:
            print(f"  ❌ 异常: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0

        sim = similarity(SENTENCE, hyp)
        best = max(best, sim)

        print(f"\n  耗时 {dt:.2f}s")
        print(f"  识别: {hyp}")
        print(f"  相似度: {sim:.3f}")

        if hyp == "（未录到声音）":
            print("  -> 前置静音超时（3 秒）。回车后 3 秒内开口即可，别等太久。")
        elif sim >= 0.5:
            print("\n  ✅ 通过：麦克风 -> 文字 已确证")
        elif sim >= 0.2:
            print("\n  ⚠️ 部分：识别到内容但有偏差，看是否同义")
        else:
            print("\n  ❌ 未达标")

        if sim >= 0.5:
            break

        again = input("\n  再来一次？(y/N) ").strip().lower()
        if again not in ("y", "yes"):
            break

    print("\n" + "=" * 78)
    print(f"最佳相似度: {best:.3f}")
    if best >= 0.5:
        print("✅ 语音闭环已确证 —— 数字人的耳朵可用")
        return 0
    if best >= 0.2:
        print("⚠️ 部分通过 —— 需人工判断识别句是否同义")
        return 2
    print("❌ 未通过 —— 需排查麦克风/VAD")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
