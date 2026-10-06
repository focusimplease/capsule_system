"""提取器结果与共同限制。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    """确定性提取结果：text 为证据草稿，omissions 记录已知省略。

    image_candidates 仅网页输入使用：确定性规则检测出的、可能携带
    独立信息的图片候选，由 Agent 受控视觉路径逐张判断忽略或转换
    （FR-013/014）；检测本身不做语义判断。
    """

    text: str = ""
    omissions: list[str] = Field(default_factory=list)
    quality: dict = Field(default_factory=dict)
    complete: bool = True
    image_candidates: list[dict] = Field(default_factory=list)
