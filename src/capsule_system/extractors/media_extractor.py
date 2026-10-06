"""音视频转写：PyAV 检测音频轨道 + faster-whisper 转写。

- 音频和视频共用本提取器（FR-001/011）；
- 视频只取其音频轨道转写为文字，不保存、不截取任何画面；
- PyAV（av 包）先做确定性的轨道检测：视频没有音频轨道时
  不调用模型，直接返回空结果，由任务引擎转入人工审核；
- 本地推理是确定性工具调用，不属于"Python 调用另一套通用大模型 API"。
"""

from __future__ import annotations

from pathlib import Path

from ..contracts.envelope import DEPENDENCY_UNAVAILABLE, VALIDATION_ERROR, CapsuleError
from .base import ExtractionResult


def extract_media(path: str, max_bytes: int, max_chars: int,
                  kind: str = "audio") -> ExtractionResult:
    file_path = Path(path)
    if not file_path.is_file():
        raise CapsuleError(VALIDATION_ERROR, f"{kind} 文件不存在：{path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise CapsuleError(
            VALIDATION_ERROR, f"{kind} 体积 {size} 字节超过上限 {max_bytes} 字节")

    try:
        import av
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, f"PyAV 不可用：{exc}") from exc

    # 确定性轨道检测：视频没有音频轨道时不能转写，不猜测画面内容
    try:
        with av.open(str(file_path)) as container:
            audio_streams = [s for s in container.streams if s.type == "audio"]
            duration = float(container.duration or 0.0) / 1_000_000.0
    except Exception as exc:
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, f"{kind} 解码失败：{exc}",
            suggested_action="检查文件是否可解码后重新提交") from exc
    if not audio_streams:
        if kind == "video":
            return ExtractionResult(
                text="", complete=False,
                omissions=["视频中没有音频轨道，画面内容无法确定性提取，"
                           "未补写内容"],
                quality={"kind": kind, "size_bytes": size,
                         "audio_streams": 0, "duration": round(duration, 2)})
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, "音频文件中没有可解码的音频轨道",
            suggested_action="检查音频文件是否损坏后重新提交")

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, f"faster-whisper 不可用：{exc}") from exc
    try:
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            str(file_path), vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500})
        lines: list[str] = []
        segment_stats: list[dict] = []
        total_chars = 0
        for segment in segments:
            text = segment.text.strip()
            start, end = round(segment.start, 2), round(segment.end, 2)
            lines.append(f"[{start:08.2f} -> {end:08.2f}] {text}")
            total_chars += len(text)
            if total_chars > max_chars:
                raise CapsuleError(
                    VALIDATION_ERROR, f"{kind} 转写文字超过上限 {max_chars} 字符")
            segment_stats.append({
                "start": start, "end": end,
                "avg_logprob": round(segment.avg_logprob, 4),
                "no_speech_prob": round(segment.no_speech_prob, 4)})
        text = "\n".join(lines)
    except CapsuleError:
        raise
    except Exception as exc:  # 转写失败、容器损坏等
        raise CapsuleError(
            DEPENDENCY_UNAVAILABLE, f"{kind} 转写失败：{exc}",
            suggested_action=f"检查{kind}文件是否可解码后重新提交") from exc
    return ExtractionResult(
        text=text, complete=bool(text),
        omissions=[] if text else [f"{kind} 中未检测到语音内容"],
        quality={"kind": kind, "duration": round(getattr(info, "duration", duration), 2),
                 "language": getattr(info, "language", None),
                 "audio_streams": len(audio_streams),
                 "segments": segment_stats, "size_bytes": size})
