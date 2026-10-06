"""PDF 提取：只读取已有文字层，不渲染页面、不对扫描页执行 OCR。

扫描版 PDF 没有可用文字层时，核心内容无法取得，由任务引擎生成
"处理中断"审核项（SPEC FR-008、US-2）。系统不保存、不管理 PDF 文件
本身（FR-006/007）；需要扫描件内容时由用户重新提供可读取的版本。
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..contracts.envelope import POLICY_BLOCKED, VALIDATION_ERROR, CapsuleError
from .base import ExtractionResult


def extract_pdf(path: str, max_pages: int, max_bytes: int,
                max_chars: int) -> ExtractionResult:
    file_path = Path(path)
    if not file_path.is_file():
        raise CapsuleError(VALIDATION_ERROR, f"PDF 文件不存在：{path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise CapsuleError(
            POLICY_BLOCKED, f"PDF 体积 {size} 字节超过上限 {max_bytes} 字节")
    try:
        reader = PdfReader(str(file_path))
    except PdfReadError as exc:
        raise CapsuleError(VALIDATION_ERROR, f"无法读取 PDF：{exc}") from exc
    page_count = len(reader.pages)
    if page_count > max_pages:
        raise CapsuleError(
            POLICY_BLOCKED, f"PDF 页数 {page_count} 超过上限 {max_pages} 页")

    parts: list[str] = []
    omissions: list[str] = []
    empty_pages: list[int] = []
    total_chars = 0
    for index, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception:  # pypdf 对损坏内容流可能抛出多种异常
            omissions.append(f"第 {index + 1} 页无法解析")
            continue
        if not page_text.strip():
            empty_pages.append(index + 1)
            continue
        total_chars += len(page_text)
        if total_chars > max_chars:
            raise CapsuleError(
                POLICY_BLOCKED, f"PDF 文字总量超过上限 {max_chars} 字符",
                suggested_action="分段提交或仅提交关键章节")
        parts.append(f"[第 {index + 1} 页]\n{page_text.strip()}")
    if empty_pages:
        omissions.append(
            f"第 {', '.join(map(str, empty_pages))} 页没有可用文字层（可能为扫描图片），"
            "未执行 OCR，未补写内容")
    text = "\n\n".join(parts).strip()
    complete = bool(text) and not empty_pages
    return ExtractionResult(
        text=text, omissions=omissions, complete=complete,
        quality={"kind": "pdf", "pages": page_count,
                 "empty_pages": empty_pages, "size_bytes": size})
