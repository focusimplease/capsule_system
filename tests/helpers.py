"""测试共用助手：配置、流程驱动与确定性样例文件构造。"""

from __future__ import annotations

import uuid
import wave
from pathlib import Path

from capsule_system.config import Config
from capsule_system.contracts.envelope import Envelope
from capsule_system.contracts.requests import (
    IngestCommitRequest,
    IngestContinueRequest,
    IngestPrepareRequest,
    InputSpec,
)
from capsule_system.domain import task_engine
from capsule_system.temporary.workspace import TaskWorkspace


def rid(prefix: str = "req") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def make_config(tmp_dir: Path, **overrides) -> Config:
    """测试配置：内存后端 + 受控临时目录 + 收紧的限制。"""
    config = Config()
    config.storage_backend = "memory"
    config.workspace_dir = tmp_dir / "workspace"
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


# ---------------------------------------------------------------------------
# ingest 主流程驱动（进程内调用任务引擎，与 CLI 走同一代码路径）
# ---------------------------------------------------------------------------

def prepare(backend, workspace: TaskWorkspace, config: Config, kind: str, *,
            text: str | None = None, path: str | None = None,
            url: str | None = None,
            user_context: str | None = None) -> Envelope:
    request = IngestPrepareRequest(
        request_id=rid(),
        input=InputSpec(kind=kind, text=text, temporary_path=path, url=url),
        user_context=user_context)
    return task_engine.ingest_prepare(request, backend, workspace, config)


def continue_task(backend, workspace: TaskWorkspace, config: Config,
                  task_ref: str, action_code: str, result: dict) -> Envelope:
    request = IngestContinueRequest(
        request_id=rid(), task_ref=task_ref, action_code=action_code,
        result=result)
    return task_engine.ingest_continue(request, backend, workspace, config)


def commit(backend, workspace: TaskWorkspace, config: Config,
           task_ref: str) -> Envelope:
    request = IngestCommitRequest(request_id=rid(), task_ref=task_ref)
    return task_engine.ingest_commit(request, backend, workspace, config)


def anchor_of(evidence_text: str) -> str:
    """从证据文字取一段保证为真实子串的锚点。"""
    anchor = evidence_text[:24]
    assert anchor.strip(), "测试证据文字不能为空"
    return anchor


def default_understanding(evidence_text: str, *,
                          existing_page_id: str | None = None,
                          novel: bool = True) -> dict:
    anchor = anchor_of(evidence_text)
    return {
        "topics": [{"name": "测试主题", "existing_page_id": existing_page_id,
                    "rationale": "默认测试归属"}],
        "key_claims": [{"claim": "关键判断", "evidence_anchor": anchor}],
        "duplicate_parts": [],
        "novel_parts": ([{"kind": "explanation", "text": "默认新增内容",
                          "evidence_anchor": anchor}] if novel else []),
        "conflicts": [],
        "target_pages": [],
        "conservative_notes": [],
    }


CONCEPT_TEMPLATE = """# {title}

## 定义
{title} 是用于测试的概念页定义段落。

## 边界
{title} 不覆盖测试边界之外的场景。

## 相近概念
与测试相近概念的区别在于粒度与用途。

## 典型用法
在测试流程中作为新建概念页的默认正文。
"""


def default_plan(evidence_text: str, *, page_type: str = "concept",
                 title: str = "测试概念页") -> dict:
    anchor = anchor_of(evidence_text)
    return {
        "creates": [{
            "page_type": page_type,
            "title": title,
            "content": CONCEPT_TEMPLATE.format(title=title),
            "claim_anchors": [{"claim": "页面重要判断",
                               "evidence_anchor": anchor}],
        }],
        "updates": [],
        "pending_updates": [],
        "not_adopted_reason": None,
    }


def update_plan(page, evidence_text: str, *, title: str | None = None,
                extra_section: str = "新增补充段落") -> dict:
    """针对既有页面的安全更新方案（概念页更新保持四要素完整）。"""
    anchor = anchor_of(evidence_text)
    content = (f"# {title or page.title}\n\n既有正文保留。\n\n"
               f"## 定义\n\n更新后的定义。\n\n## 边界\n\n更新后的边界。\n\n"
               f"## 相近概念\n\n相近概念说明。\n\n## 典型用法\n\n典型用法说明。\n\n"
               f"## {extra_section}\n\n{extra_section}内容。")
    return {
        "creates": [],
        "updates": [{
            "page_id": page.page_id,
            "revision_id": page.revision_id,
            "content": content,
            "claim_anchors": [{"claim": "更新判断", "evidence_anchor": anchor}],
            "change_summary": f"测试更新：{extra_section}",
        }],
        "pending_updates": [],
        "not_adopted_reason": None,
    }


def complete_ingest(backend, workspace: TaskWorkspace, config: Config,
                    task_ref: str, evidence_text: str, *,
                    understanding: dict | None = None,
                    plan: dict | None = None) -> Envelope:
    """从 compare_knowledge 阶段推进到 commit，返回 commit 信封。"""
    env = continue_task(backend, workspace, config, task_ref,
                        "compare_knowledge",
                        understanding or default_understanding(evidence_text))
    assert env.status == "success", env.model_dump_json()
    env = continue_task(backend, workspace, config, task_ref,
                        "organize_pages", plan or default_plan(evidence_text))
    assert env.status == "success", env.model_dump_json()
    return commit(backend, workspace, config, task_ref)


# ---------------------------------------------------------------------------
# 确定性样例文件构造（不依赖外部资源）
# ---------------------------------------------------------------------------

def _pdf_bytes(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def make_pdf(path: Path, text: str) -> Path:
    """带文字层的最小 PDF（Helvetica + Tj）。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    path.write_bytes(_pdf_bytes(objects))
    return path


def make_scanned_pdf(path: Path, variant: int = 0) -> Path:
    """扫描版 PDF：只有图形绘制指令，没有任何文字层。

    variant 用于生成字节内容不同的文件，避免入口去重拦截。
    """
    stream = f"0 0 1 rg 100 100 {200 + variant} 200 re f".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    path.write_bytes(_pdf_bytes(objects))
    return path


def make_png(path: Path, width: int = 16, height: int = 16,
             color: tuple[int, int, int] = (120, 40, 200)) -> Path:
    from PIL import Image
    image = Image.new("RGB", (width, height), color=color)
    image.save(path, format="PNG")
    return path


def make_wav(path: Path, seconds: float = 0.2, rate: int = 16000) -> Path:
    """静音 PCM WAV：PyAV 可打开并检测到音频轨道。"""
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def make_video_no_audio(path: Path, frames: int = 8) -> Path:
    """只有视频轨、没有音频轨的最小视频文件。"""
    import av
    container = av.open(str(path), "w")
    stream = container.add_stream("mpeg4", rate=24)
    stream.width = 64
    stream.height = 64
    stream.pix_fmt = "yuv420p"
    for _ in range(frames):
        frame = av.VideoFrame(64, 64, "yuv420p")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


# ---------------------------------------------------------------------------
# faster-whisper 测试替身
# ---------------------------------------------------------------------------

def fake_whisper_factory(segments: list[tuple[float, float, str]] | None = None,
                         duration: float = 3.0, language: str = "zh"):
    """返回可替换 faster_whisper.WhisperModel 的假模型类。"""
    if segments is None:
        segments = [(0.0, 1.2, "你好，胶囊系统。"),
                    (1.2, 2.4, "这是一段测试语音。")]

    class FakeSegment:
        def __init__(self, start: float, end: float, text: str):
            self.start = start
            self.end = end
            self.text = text
            self.avg_logprob = -0.12
            self.no_speech_prob = 0.04

    class FakeInfo:
        def __init__(self):
            self.duration = duration
            self.language = language

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            return iter([FakeSegment(*s) for s in segments]), FakeInfo()

    return FakeModel
