"""PDF / 图片 / 音视频输入集成测试（真实文件 + mock 转写模型）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import (
    complete_ingest,
    continue_task,
    fake_whisper_factory,
    make_pdf,
    make_png,
    make_scanned_pdf,
    make_video_no_audio,
    make_wav,
    prepare,
)

PDF_TEXT = "PDF integration test: capsule text layer extraction."
AUDIO_TEXT = "音频转写证据：本地确定性转写。"
IMAGE_TEXT = "图片识别证据：图表标题为季度增长，数值为百分之二十。"


class TestPdf:
    def test_pdf_with_text_layer_completes(self, backend, workspace, config,
                                           tmp_path):
        path = make_pdf(tmp_path / "doc.pdf", PDF_TEXT)
        env = prepare(backend, workspace, config, "pdf", path=str(path))
        assert env.status == "success"
        assert env.next_action.action_code == "compare_knowledge"
        task_ref = env.data["task_ref"]
        env = complete_ingest(backend, workspace, config, task_ref, PDF_TEXT)
        assert env.status == "success"
        assert env.data["outcome"] == "completed"
        assert "PDF integration test" in backend.read_evidence_content(
            env.data["evidence_id"])

    def test_scanned_pdf_enters_review_immediately(self, backend, workspace,
                                                   config, tmp_path):
        path = make_scanned_pdf(tmp_path / "scan.pdf")
        env = prepare(backend, workspace, config, "pdf", path=str(path))
        # prepare 阶段直接写入终态：不创建可恢复任务
        assert env.status == "review_required"
        assert env.data["outcome"] == "needs_review"
        review_item_id = env.result_refs[0]
        submission_id = env.data["submission_id"]

        item = backend.get_review_item(review_item_id)
        assert item.review_type == "processing_interruption"
        assert item.trigger_code == "EXTRACTION_INCOMPLETE"
        assert item.rule_gap_flag is True
        assert "pdf_text_layer_extraction" in item.attempted_actions
        assert "OCR" in item.reason
        submission = backend.get_submission(submission_id)
        assert submission.status == "needs_review"
        assert not submission.source_url, "无 URL 时不保存原始证据"
        assert backend.get_original_evidence_by_submission(
            submission_id) is None
        # 扫描 PDF 无文字 → 无衍生证据
        assert backend.list_evidence_by_submission(submission_id) == []
        records = backend.list_processing_records(submission_id)
        assert records[0].outcome == "needs_review"
        assert records[0].reason_code == "EXTRACTION_INCOMPLETE"


class TestImage:
    def test_image_usable_flow(self, backend, workspace, config, tmp_path):
        path = make_png(tmp_path / "img.png")
        env = prepare(backend, workspace, config, "image", path=str(path))
        assert env.status == "success"
        assert env.next_action.action_code == "understand_image"
        task_ref = env.data["task_ref"]
        assert env.data["quality"]["format"] == "PNG"

        env = continue_task(backend, workspace, config, task_ref,
                            "understand_image", {
                                "outcome": "usable",
                                "evidence_text": IMAGE_TEXT,
                                "omissions": [],
                                "uncertainties": [],
                                "reason": "图表完整可读",
                            })
        assert env.status == "success"
        assert env.next_action.action_code == "compare_knowledge"
        env = complete_ingest(backend, workspace, config, task_ref, IMAGE_TEXT)
        assert env.status == "success"
        assert env.data["outcome"] == "completed"
        assert backend.read_evidence_content(
            env.data["evidence_id"]) == IMAGE_TEXT

    def test_image_no_evidence_saves_url_only(self, backend, workspace, config,
                                              tmp_path):
        """FR-004：no_evidence 终态也要保存来源 URL（存在时）。"""
        path = make_png(tmp_path / "img.png")
        env = prepare(backend, workspace, config, "image", path=str(path),
                      url="https://example.com/photo.png")
        task_ref = env.data["task_ref"]
        env = continue_task(backend, workspace, config, task_ref,
                            "understand_image", {
                                "outcome": "no_evidence",
                                "omissions": ["图片为风景照片，无知识性内容"],
                                "reason": "无可形成知识的主题或事实",
                            })
        assert env.status == "success"
        assert env.data["planned_outcome"] == "no_evidence"
        env = continue_task(backend, workspace, config, task_ref,
                            "understand_image", {})
        # 已就绪后重复 continue 被拒
        assert env.status == "failed"

    def test_image_no_evidence_commit(self, backend, workspace, config,
                                      tmp_path):
        from tests.helpers import commit
        path = make_png(tmp_path / "img.png")
        env = prepare(backend, workspace, config, "image", path=str(path),
                      url="https://example.com/photo.png")
        task_ref = env.data["task_ref"]
        continue_task(backend, workspace, config, task_ref, "understand_image", {
            "outcome": "no_evidence", "reason": "无知识内容"})
        env = commit(backend, workspace, config, task_ref)
        assert env.status == "success"
        assert env.data["outcome"] == "no_evidence"
        submission_id = env.data["submission_id"]
        assert "evidence_id" not in env.data or not env.data["evidence_id"]
        assert backend.list_evidence_by_submission(submission_id) == []
        records = backend.list_processing_records(submission_id)
        assert records[0].reason_code == "NO_KNOWLEDGE_EVIDENCE"
        # FR-004/005：无衍生证据时，来源 URL 仍保存为原始证据
        original = backend.get_original_evidence_by_submission(submission_id)
        assert original is not None
        assert original.url == "https://example.com/photo.png"
        assert not Path(task_ref).exists()

    def test_image_review_outcome(self, backend, workspace, config, tmp_path):
        path = make_png(tmp_path / "img.png")
        env = prepare(backend, workspace, config, "image", path=str(path))
        task_ref = env.data["task_ref"]
        env = continue_task(backend, workspace, config, task_ref,
                            "understand_image", {
                                "outcome": "review",
                                "uncertainties": ["局部文字模糊，无法确认数值"],
                                "reason": "只能识别局部信息",
                            })
        assert env.status == "success"
        assert env.data["planned_outcome"] == "needs_review"
        from tests.helpers import commit
        env = commit(backend, workspace, config, task_ref)
        assert env.status == "review_required"
        item = backend.get_review_item(env.result_refs[0])
        assert item.trigger_code == "IMAGE_UNRELIABLE"
        assert item.suggested_next_task.get("requires_new_input") is True

    def test_image_usable_requires_evidence_text(self, backend, workspace,
                                                 config, tmp_path):
        path = make_png(tmp_path / "img.png")
        env = prepare(backend, workspace, config, "image", path=str(path))
        task_ref = env.data["task_ref"]
        env = continue_task(backend, workspace, config, task_ref,
                            "understand_image", {
                                "outcome": "usable", "evidence_text": "  "})
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_image_oversized_pixels_rejected(self, backend, workspace, config,
                                             tmp_path):
        from capsule_system.temporary.workspace import TaskWorkspace
        from tests.helpers import make_config
        path = make_png(tmp_path / "big.png", width=256, height=256)
        strict = make_config(tmp_path, image_max_pixels=100)
        env = prepare(backend, TaskWorkspace(strict.workspace_dir), strict,
                      "image", path=str(path))
        assert env.status == "failed"
        assert env.error.code == "POLICY_BLOCKED"


class TestAudioVideo:
    @pytest.fixture(autouse=True)
    def fake_whisper(self, monkeypatch):
        monkeypatch.setattr("faster_whisper.WhisperModel", fake_whisper_factory(
            segments=[(0.0, 1.5, AUDIO_TEXT)]))

    def test_audio_transcription_completes(self, backend, workspace, config,
                                           tmp_path):
        path = make_wav(tmp_path / "a.wav")
        env = prepare(backend, workspace, config, "audio", path=str(path))
        assert env.status == "success"
        assert env.next_action.action_code == "compare_knowledge"
        task_ref = env.data["task_ref"]
        env = complete_ingest(backend, workspace, config, task_ref, AUDIO_TEXT)
        assert env.status == "success"
        assert env.data["outcome"] == "completed"
        assert AUDIO_TEXT in backend.read_evidence_content(
            env.data["evidence_id"])

    def test_video_without_audio_enters_review(self, backend, workspace,
                                               config, tmp_path):
        path = make_video_no_audio(tmp_path / "v.mp4")
        env = prepare(backend, workspace, config, "video", path=str(path))
        assert env.status == "review_required"
        item = backend.get_review_item(env.result_refs[0])
        assert item.trigger_code == "MEDIA_TRANSCRIPTION_EMPTY"
        assert item.review_type == "processing_interruption"
        assert item.rule_gap_flag is True
        assert env.data["outcome"] == "needs_review"
