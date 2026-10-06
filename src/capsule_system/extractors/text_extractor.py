"""纯文本提取：保留原意并整理成可阅读格式。"""

from __future__ import annotations

from ..contracts.envelope import POLICY_BLOCKED, VALIDATION_ERROR, CapsuleError
from .base import ExtractionResult


def extract_text(text: str, max_chars: int) -> ExtractionResult:
    if not text or not text.strip():
        raise CapsuleError(VALIDATION_ERROR, "纯文本输入不能为空")
    if len(text) > max_chars:
        raise CapsuleError(
            POLICY_BLOCKED,
            f"纯文本输入超过上限 {max_chars} 字符", suggested_action="分段提交")
    # 保留原意：只做行尾空白与连续空行的规范化，不改写内容
    lines = [line.rstrip() for line in text.splitlines()]
    normalized = "\n".join(lines).strip("\n")
    return ExtractionResult(text=normalized, quality={"kind": "text",
                                                      "chars": len(normalized)})
