"""提取器单元测试：确定性检查，不依赖网络与真实模型推理。"""

from __future__ import annotations

import pytest

from capsule_system.contracts.envelope import CapsuleError
from capsule_system.extractors import media_extractor, pdf_extractor, text_extractor
from capsule_system.extractors.image_extractor import validate_image
from capsule_system.extractors.web_extractor import detect_image_candidates, extract_web

from tests.helpers import (
    fake_whisper_factory,
    make_pdf,
    make_png,
    make_scanned_pdf,
    make_video_no_audio,
    make_wav,
)


class TestTextExtractor:
    def test_extracts_and_preserves_content(self):
        # 保留原意：只做行尾空白与首尾空行规范化，不改写内容
        result = text_extractor.extract_text("第一行  \n\n\n第二行\n\n", 1000)
        assert result.text == "第一行\n\n\n第二行"
        assert result.complete is True

    def test_empty_text_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            text_extractor.extract_text("   \n  ", 1000)
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_over_limit_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            text_extractor.extract_text("x" * 20, 10)
        assert excinfo.value.code == "POLICY_BLOCKED"


class TestPdfExtractor:
    def test_text_layer_extracted(self, tmp_path):
        path = make_pdf(tmp_path / "doc.pdf", "Capsule text layer content")
        result = pdf_extractor.extract_pdf(str(path), 10, 10_000_000, 100_000)
        assert "Capsule text layer content" in result.text
        assert result.complete is True
        assert result.quality["pages"] == 1

    def test_scanned_pdf_has_no_text(self, tmp_path):
        path = make_scanned_pdf(tmp_path / "scan.pdf")
        result = pdf_extractor.extract_pdf(str(path), 10, 10_000_000, 100_000)
        assert result.text == ""
        assert result.complete is False
        assert any("没有可用文字层" in o for o in result.omissions)
        assert "未执行 OCR" in result.omissions[0]

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(CapsuleError) as excinfo:
            pdf_extractor.extract_pdf(str(tmp_path / "ghost.pdf"), 10, 100, 100)
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_over_max_bytes_rejected(self, tmp_path):
        path = make_pdf(tmp_path / "doc.pdf", "content")
        with pytest.raises(CapsuleError) as excinfo:
            pdf_extractor.extract_pdf(str(path), 10, 10, 100)
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_over_max_pages_rejected(self, tmp_path):
        path = make_pdf(tmp_path / "doc.pdf", "content")
        with pytest.raises(CapsuleError) as excinfo:
            pdf_extractor.extract_pdf(str(path), 0, 10_000_000, 100)
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_over_max_chars_rejected(self, tmp_path):
        path = make_pdf(tmp_path / "doc.pdf", "long text " * 50)
        with pytest.raises(CapsuleError) as excinfo:
            pdf_extractor.extract_pdf(str(path), 10, 10_000_000, 20)
        assert excinfo.value.code == "POLICY_BLOCKED"


class TestImageValidator:
    def test_valid_png_quality_only(self, tmp_path):
        path = make_png(tmp_path / "img.png")
        result = validate_image(str(path), 1_000_000, 1_000_000)
        assert result.text == ""
        assert result.complete is False
        assert result.quality["format"] == "PNG"
        assert result.quality["width"] == 16

    def test_oversized_pixels_rejected(self, tmp_path):
        path = make_png(tmp_path / "img.png", width=64, height=64)
        with pytest.raises(CapsuleError) as excinfo:
            validate_image(str(path), 100, 1_000_000)
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_oversized_bytes_rejected(self, tmp_path):
        path = make_png(tmp_path / "img.png", width=512, height=512)
        with pytest.raises(CapsuleError) as excinfo:
            validate_image(str(path), 100_000_000, 100)
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_unidentified_file_rejected(self, tmp_path):
        path = tmp_path / "fake.png"
        path.write_bytes(b"not an image at all")
        with pytest.raises(CapsuleError) as excinfo:
            validate_image(str(path), 1_000_000, 1_000_000)
        assert excinfo.value.code == "VALIDATION_ERROR"


class TestWebImageCandidates:
    def test_figure_with_figcaption_is_candidate(self):
        html = ('<html><body><figure><img src="/a.png">'
                '<figcaption>架构图：胶囊系统主流程</figcaption></figure></body></html>')
        candidates = detect_image_candidates(html)
        assert len(candidates) == 1
        assert candidates[0]["key"] == "figure-0"
        assert "架构图" in candidates[0]["description"]

    def test_descriptive_alt_is_candidate(self):
        html = ('<html><body><img src="/b.png" alt="数据流转示意图表">'
                '</body></html>')
        candidates = detect_image_candidates(html)
        assert len(candidates) == 1
        assert candidates[0]["description"] == "数据流转示意图表"

    def test_decorative_alt_excluded(self):
        html = ('<html><body><img src="/logo.png" alt="logo">'
                '<img src="/icon.png" alt="icon"></body></html>')
        assert detect_image_candidates(html) == []

    def test_data_uri_and_tracking_pixel_excluded(self):
        html = ('<html><body><img src="data:image/png;base64,xxxx" alt="示意图说明文字">'
                '<img src="/t.png" width="1" height="1" alt="跟踪像素示意图说明"></body></html>')
        assert detect_image_candidates(html) == []

    def test_short_alt_excluded(self):
        html = '<html><body><img src="/c.png" alt="图"></body></html>'
        assert detect_image_candidates(html) == []

    def test_duplicate_src_deduplicated(self):
        html = ('<html><body><img src="/d.png" alt="说明文字甲乙丙丁">'
                '<img src="/d.png" alt="说明文字甲乙丙丁"></body></html>')
        assert len(detect_image_candidates(html)) == 1

    def test_max_candidates_capped(self):
        imgs = "".join(f'<img src="/i{i}.png" alt="说明性文字编号{i}">'
                       for i in range(30))
        html = f"<html><body>{imgs}</body></html>"
        assert len(detect_image_candidates(html)) == 20

    def test_malformed_html_returns_empty(self):
        assert detect_image_candidates("<html><body><figure><img") == []

    def test_extract_web_rejects_non_http_url(self):
        with pytest.raises(CapsuleError) as excinfo:
            extract_web("ftp://example.com/x", 1000, 5, 1000)
        assert excinfo.value.code == "VALIDATION_ERROR"


class TestMediaExtractor:
    @pytest.fixture(autouse=True)
    def fake_whisper(self, monkeypatch):
        monkeypatch.setattr("faster_whisper.WhisperModel", fake_whisper_factory())

    def test_audio_transcribed_with_segments(self, tmp_path):
        path = make_wav(tmp_path / "a.wav")
        result = media_extractor.extract_media(str(path), 10_000_000, 100_000, "audio")
        assert "你好，胶囊系统。" in result.text
        assert "[00000.00 ->" in result.text
        assert result.complete is True
        assert result.quality["audio_streams"] == 1
        assert result.quality["language"] == "zh"

    def test_video_without_audio_track_returns_empty(self, tmp_path):
        path = make_video_no_audio(tmp_path / "v.mp4")
        result = media_extractor.extract_media(str(path), 10_000_000, 100_000, "video")
        assert result.text == ""
        assert result.complete is False
        assert result.quality["audio_streams"] == 0
        assert any("音频轨道" in o for o in result.omissions)

    def test_video_file_as_audio_rejected(self, tmp_path):
        # 无音频轨道的文件按音频提交：无法转写，进入依赖不可用
        path = make_video_no_audio(tmp_path / "v.mp4")
        with pytest.raises(CapsuleError) as excinfo:
            media_extractor.extract_media(str(path), 10_000_000, 100_000, "audio")
        assert excinfo.value.code == "DEPENDENCY_UNAVAILABLE"

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(CapsuleError) as excinfo:
            media_extractor.extract_media(str(tmp_path / "ghost.wav"),
                                          10_000_000, 100_000, "audio")
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_over_max_bytes_rejected(self, tmp_path):
        path = make_wav(tmp_path / "a.wav", seconds=1.0)
        with pytest.raises(CapsuleError) as excinfo:
            media_extractor.extract_media(str(path), 100, 100_000, "audio")
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_transcript_over_max_chars_rejected(self, tmp_path):
        path = make_wav(tmp_path / "a.wav")
        with pytest.raises(CapsuleError) as excinfo:
            media_extractor.extract_media(str(path), 10_000_000, 5, "audio")
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_corrupt_container_rejected(self, tmp_path):
        path = tmp_path / "bad.wav"
        path.write_bytes(b"RIFFnot-a-real-wave-file")
        with pytest.raises(CapsuleError) as excinfo:
            media_extractor.extract_media(str(path), 10_000_000, 100_000, "audio")
        assert excinfo.value.code == "DEPENDENCY_UNAVAILABLE"
