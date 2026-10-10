"""MOSS-Transcribe-Diarize 识别引擎（OpenMOSS 0.9B，Apache-2.0）。

2026-10-10 接入：用户决策"MOSS 完全替换 SenseVoice"作为对话识别引擎。
上游：https://github.com/OpenMOSS/MOSS-Transcribe-Diarize
      （同场模型在 INTERSPEECH 2026 MLC-SLM Challenge 获第一）
来源说明：
  - 模型权重：HuggingFace `OpenMOSS-Team/MOSS-Transcribe-Diarize`（0.9B，BF16，
    1.73GB），已缓存至 `<repo>/models/moss-transcribe-diarize/`
  - 推理辅助代码：仓库含 `moss_transcribe_diarize` Python 包（Apache-2.0），
    本地克隆于 `<repo>/.local_vendor/MOSS-Transcribe-Diarize/`，
    本模块在运行时把该目录注入 sys.path 后直接 import，不 pip 安装。
  - 模型卡自带远端代码（configuration/modeling/processing_moss_transcribe_diarize.py）
    融入 HF snapshot，AutoModel/AutoProcessor 均以 trust_remote_code=True 加载。

性能实测（RTX 5060 Laptop 8GB，Windows 11，torch 2.12.0-dev+cu128）：
  - 显存占用 1.69 GB（模型）与峰值 1.76 GB —— 8GB 卡余量充足
  - 权重加载 3.86s；单句（3.86s 音频）生成   - 冷启动 25.58s（Dynamo 编译缓存，每进程一次性）
    - 热启动 1.85s，RTF ≈ 0.479
  - 输出逐字无差且自带时间戳与 [S01] 说话人标签

为什么仍然保留 SenseVoice 作为回退：
  MOSS 依赖 torch + CUDA。在无 GPU / 无 vendored 仓库 / 显存不足的环境下，
  引擎调度器（voice.recognize_audio 默认 ZEROAI_ASR_ENGINE=auto）会自动
  回退到 SenseVoice（shrap-onnx，纯 CPU，中文短句 0.2s 内），不因环境缺失
  让语音功能整体不可用。显式指定 ZEROAI_ASR_ENGINE=moss 则不做回退，
  失败直接抛错（便于排障）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from zeroai.core.paths import (
    _ZEROAI_USER_DIR,
    _ensure_vendored_path,
    _find_resource_dir,
)

# ════════════════════════════════════════════════════════════════════
# 路径解析（与 SenseVoice 同风格：开发模式仓库目录 > 环境变量 > 用户目录）
# ════════════════════════════════════════════════════════════════════

_VENDOR_SUBDIR = ".local_vendor/MOSS-Transcribe-Diarize"
_MODEL_SUBDIR = "moss-transcribe-diarize"


def _vendor_dir() -> Path | None:
    """推理辅助代码 `moss_transcribe_diarize` Python 包所在目录。"""
    env = os.environ.get("ZEROAI_MOSS_VENDOR_DIR", "").strip()
    if env:
        p = Path(env)
        return p if (p / "moss_transcribe_diarize").is_dir() else None
    here = Path(__file__).resolve()
    repo_root = here.parents[2]          # zeroai/tools/asr_moss.py -> 仓库根
    p = repo_root / _VENDOR_SUBDIR
    if (p / "moss_transcribe_diarize").is_dir():
        return p
    back = Path(_ZEROAI_USER_DIR) / "vendor" / "MOSS-Transcribe-Diarize"
    if (back / "moss_transcribe_diarize").is_dir():
        return back
    return None


def model_dir() -> Path | None:
    """MOSS 权重目录（必须含 config.json 才认定有效）。"""
    env = os.environ.get("ZEROAI_MOSS_MODEL_DIR", "").strip()
    if env:
        p = Path(env)
        return p if (p / "config.json").is_file() else None
    p = Path(_find_resource_dir("models")) / _MODEL_SUBDIR
    if (p / "config.json").is_file():
        return p
    back = Path(_ZEROAI_USER_DIR) / "models" / _MODEL_SUBDIR
    if (back / "config.json").is_file():
        return back
    return None


def _ensure_vendor_path() -> Path:
    """把 vendored 的 `moss_transcribe_diarize` 包目录注入 sys.path（一次即可）。"""
    vd = _vendor_dir()
    if vd is None:
        raise ImportError(
            "未找到 MOSS-Transcribe-Diarize 推理代码：期望 "
            f"<仓库根>/{_VENDOR_SUBDIR} 或设置 ZEROAI_MOSS_VENDOR_DIR")
    s = str(vd)
    if s not in sys.path:
        sys.path.insert(0, s)
    return vd


# ════════════════════════════════════════════════════════════════════
# 惰性单例（torch / transformers 到调用时才加载，pytest 不必付这份成本）
# ════════════════════════════════════════════════════════════════════

_STATE: dict | None = None
_LOAD_LOCK = threading.RLock()
_NOTED: set[str] = set()
_LAST_SEGMENTS: list = []


def _note(msg: str) -> None:
    """stderr 提示，相同信息整个进程只报一次（避免日志刷屏）。"""
    if msg in _NOTED:
        return
    _NOTED.add(msg)
    print(f"[MOSS] {msg}", file=sys.stderr, flush=True)


def last_segments() -> list:
    """最近一次 recognize_audio_moss 的 (start, end, speaker, text) 段列表。"""
    return list(_LAST_SEGMENTS)


def get_model() -> dict:
    """惰性加载 MOSS-Transcribe-Diarize 到 GPU/CPU（单例复用）。

    Returns:
        dict: {model, processor, device, dtype, load_s}
    Raises:
        ImportError: vendored 依赖/transformers 版本不符
        FileNotFoundError: 权重目录缺失
        RuntimeError: 加载失败（显存/CUDA 等原因）
    """
    global _STATE
    if _STATE is not None:
        return _STATE
    with _LOAD_LOCK:
        if _STATE is not None:
            return _STATE

        fd = model_dir()
        if fd is None:
            raise FileNotFoundError(
                "未找到 MOSS 权重目录（期望含 config.json）："
                f"models/{_MODEL_SUBDIR} 或设置 ZEROAI_MOSS_MODEL_DIR")

        # 全本地文件即可离线加载；远端代码取自 snapshot 内的 *.py
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        _ensure_vendor_path()

        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        from moss_transcribe_diarize.inference_utils import resolve_device

        device = resolve_device("auto")
        dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
        t0 = time.time()
        model = AutoModelForCausalLM.from_pretrained(
            str(fd),
            trust_remote_code=True,
            dtype="auto",
            attn_implementation="sdpa",
        ).to(dtype=dtype).to(device).eval()
        processor = AutoProcessor.from_pretrained(str(fd), trust_remote_code=True)
        load_s = time.time() - t0

        if device.type == "cuda":
            used = torch.cuda.memory_allocated() / 1024**3
            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            if used > total * 0.7:
                _note(f"显存占用 {used:.2f}/{total:.1f} GB，长音频可能 OOM；"
                      "必要时改用零-ai 的 SenseVoice 引擎")
            else:
                _note(f"MOSS 0.9B 已加载 {load_s:.2f}s，显存 {used:.2f}/{total:.1f} GB")
        else:
            _note("MOSS 0.9B 以 CPU(fp32) 运行，吞吐较低；"
                  "短句尚可，长音频建议回退 ZEROAI_ASR_ENGINE=sensevoice")
        _STATE = {
            "model": model,
            "processor": processor,
            "device": device,
            "dtype": dtype,
            "load_s": load_s,
        }
    return _STATE


# ════════════════════════════════════════════════════════════════════
# 推理：波形 -> 文本（对话用）/ 媒体文件 -> 带说话人标签的分段（会议用）
# ════════════════════════════════════════════════════════════════════

_DIALOG_PROMPT = (
    "请将音频转写为文本，不标注时间戳与说话人，直接输出语音内容。"
)


def _wav_of(audio, sample_rate: int = 16000) -> Path:
    """float32 波形 -> 临时 16bit PCM wav（供 WhisperFeatureExtractor 读取）。"""
    import numpy as np

    arr = np.asarray(audio).reshape(-1).astype(np.float32)
    if float(np.abs(arr).max(initial=0.0)) > 1.0:
        arr = np.clip(arr, -1.0, 1.0)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes((arr * 32767).astype("<i2").tobytes())
    return Path(tmp.name)


def _generate(messages, *, max_new_tokens: int) -> tuple[str, list]:
    """跑一次推理并解析分段（内部共用）。"""
    st = get_model()
    from moss_transcribe_diarize.inference_utils import generate_transcription
    from moss_transcribe_diarize import parse_transcript

    t0 = time.time()
    result = generate_transcription(
        st["model"], st["processor"], messages,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        device=st["device"],
        dtype=st["dtype"],
    )
    infer_s = time.time() - t0
    raw = result["text"].strip()

    segs = []
    try:
        segs = list(parse_transcript(raw))
    except Exception as e:  # noqa: BLE001 —— 解析失败也把原文暴露给调用方
        _note(f"转写分段解析失败（回传原文）：{e}")

    global _LAST_SEGMENTS
    _LAST_SEGMENTS = [(s.start, s.end, s.speaker, s.text) for s in segs]
    st["last_infer_s"] = infer_s
    st["last_generated_tokens"] = result.get("generated_tokens")
    return raw, _LAST_SEGMENTS


def recognize_audio_moss(audio, sample_rate: int = 16000) -> str:
    """对话短句识别：float32 波形 -> 纯文本（无时间戳/说话人标签）。

    为避免 LLM 自由发挥，这里显式使用"不标注"提示词；
    若模型仍输出 [x.xx][Sxx]text 标签，解析后再剥离。
    """
    if not hasattr(audio, "__len__") or len(audio) == 0:
        return "（未识别到内容）"

    from moss_transcribe_diarize.inference_utils import build_transcription_messages

    wav = _wav_of(audio, sample_rate)
    try:
        messages = build_transcription_messages(str(wav), prompt=_DIALOG_PROMPT)
        raw, segs = _generate(messages, max_new_tokens=2048)
        if segs:
            text = "".join(s[3] for s in segs).strip()
        else:
            text = raw
        return text if text else "（未识别到内容）"
    finally:
        wav.unlink(missing_ok=True)


def transcribe_long_media(media_path, max_new_tokens: int = 65536) -> list[tuple]:
    """会议/长音频转写：文件 -> [(start, end, speaker, text), ...]。

    输出即 MoSS 目标格式，可直接导出 SRT/ASS（下游 moss_transcribe_diarize
    已内置 export_srt/export_ass 可选用）。
    """
    from moss_transcribe_diarize.inference_utils import build_transcription_messages

    messages = build_transcription_messages(str(media_path))
    _, segs = _generate(messages, max_new_tokens=max_new_tokens)
    return segs


def is_available() -> bool:
    """不加载权重的前提下判断引擎"装得上"：代码 + 权重 同时在场。"""
    return _vendor_dir() is not None and model_dir() is not None
