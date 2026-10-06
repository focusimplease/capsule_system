"""图片输入校验：确定性检查交给 Pillow，语义理解交给当前 Agent 视觉能力。

- 只校验类型、尺寸和解压限制，不做 OCR；
- 系统只读取用户文件，不保存、不管理图片文件本身（FR-006/007）；
- 图片的语义理解（usable / no_evidence / review）由 Agent 在
  understand_image 动作中返回（FR-009）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..contracts.envelope import POLICY_BLOCKED, VALIDATION_ERROR, CapsuleError
from .base import ExtractionResult

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"}


def validate_image(path: str, max_pixels: int, max_bytes: int) -> ExtractionResult:
    file_path = Path(path)
    if not file_path.is_file():
        raise CapsuleError(VALIDATION_ERROR, f"图片文件不存在：{path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise CapsuleError(
            POLICY_BLOCKED, f"图片体积 {size} 字节超过上限 {max_bytes} 字节")
    try:
        with Image.open(file_path) as image:
            fmt = (image.format or "").upper()
            if fmt not in ALLOWED_FORMATS:
                raise CapsuleError(
                    VALIDATION_ERROR, f"不支持的图片格式：{fmt or '未知'}",
                    details={"allowed": sorted(ALLOWED_FORMATS)})
            width, height = image.size
    except UnidentifiedImageError as exc:
        raise CapsuleError(VALIDATION_ERROR, f"无法识别图片文件：{exc}") from exc
    except OSError as exc:
        raise CapsuleError(VALIDATION_ERROR, f"无法读取图片文件：{exc}") from exc
    pixels = width * height
    if pixels > max_pixels:
        raise CapsuleError(
            POLICY_BLOCKED,
            f"图片像素总数 {pixels} 超过上限 {max_pixels}，拒绝处理以保护本地资源")
    # 语义理解是 next_action（understand_image），此处无文字证据
    return ExtractionResult(
        text="", complete=False,
        quality={"kind": "image", "format": fmt, "width": width,
                 "height": height, "pixels": pixels, "size_bytes": size})
