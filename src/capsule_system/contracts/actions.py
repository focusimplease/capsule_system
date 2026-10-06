"""语义动作的结构化结果模型（Agent 通过 ingest continue 提交）。

Python 不信任这些结果：动作代码、Schema、证据锚点、引用和策略约束
都必须在 domain/policies.py 中重新校验。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---- 动作代码（ingest 主循环的 next_action 集合） ----
ACTION_UNDERSTAND_IMAGE = "understand_image"
ACTION_UNDERSTAND_WEB_IMAGES = "understand_web_images"
ACTION_COMPARE_KNOWLEDGE = "compare_knowledge"
ACTION_ORGANIZE_PAGES = "organize_pages"

ALL_ACTION_CODES = {
    ACTION_UNDERSTAND_IMAGE,
    ACTION_UNDERSTAND_WEB_IMAGES,
    ACTION_COMPARE_KNOWLEDGE,
    ACTION_ORGANIZE_PAGES,
}


class UnderstandImageResult(BaseModel):
    """understand_image 的结果：只能是 usable / no_evidence / review。

    - usable：图片不依赖外部上下文完整表达知识，evidence_text 为识别出的文字；
    - no_evidence：图片无可形成知识的主题或事实；
    - review：只能识别局部信息，无法确认完整含义，不补写未知内容。
    """

    outcome: Literal["usable", "no_evidence", "review"]
    evidence_text: str = ""
    omissions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    reason: str = ""


class WebImageHandling(BaseModel):
    """单个网页图片的处理结果。

    ignored=True 表示装饰性或重复正文图片；
    否则必须提供转换为文字、Markdown 表格或文本定义图形的 text。
    """

    image_key: str
    ignored: bool = False
    reason: str = ""
    text: str = ""
    core_incomplete: bool = False


class UnderstandWebImagesResult(BaseModel):
    """understand_web_images 的结果。

    core_incomplete=True 表示网页核心内容依赖无法可靠转换的图片，
    证据不完整并进入审核（FR-016）。
    """

    images: list[WebImageHandling] = Field(default_factory=list)
    core_incomplete: bool = False
    reason: str = ""


class KeyClaim(BaseModel):
    claim: str
    evidence_anchor: str


class TopicRef(BaseModel):
    """理解结果中的主题归属判断。existing_page_id 为空表示新建。"""

    name: str
    existing_page_id: str | None = None
    rationale: str = ""


class NovelPart(BaseModel):
    """值得合并进知识页的新增价值（例子、边界、解释或修正）。"""

    kind: Literal["example", "boundary", "explanation", "correction", "other"] = "other"
    text: str
    evidence_anchor: str = ""


class ConflictItem(BaseModel):
    page_id: str
    existing_claim: str
    new_claim: str
    evidence_anchor: str
    resolution: Literal["auto_supersede", "needs_user"]


class UnderstandEvidenceResult(BaseModel):
    """compare_knowledge 的结果：证据与现有知识的关系判断。"""

    topics: list[TopicRef] = Field(default_factory=list)
    key_claims: list[KeyClaim] = Field(default_factory=list)
    duplicate_parts: list[str] = Field(default_factory=list)
    novel_parts: list[NovelPart] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    target_pages: list[str] = Field(default_factory=list)
    # 轻微不确定的保守处理说明（FR-053）：不改变核心结果时记录后继续
    conservative_notes: list[str] = Field(default_factory=list)


class ClaimAnchor(BaseModel):
    claim: str
    evidence_anchor: str


class PageCreate(BaseModel):
    page_type: Literal["topic", "concept"]
    title: str
    content: str
    claim_anchors: list[ClaimAnchor] = Field(default_factory=list)


class PageUpdate(BaseModel):
    page_id: str
    revision_id: str
    content: str
    claim_anchors: list[ClaimAnchor] = Field(default_factory=list)
    change_summary: str = ""


class OrganizePagesResult(BaseModel):
    """organize_pages 的结果：安全更新与冲突挂起更新分开提交。"""

    creates: list[PageCreate] = Field(default_factory=list)
    updates: list[PageUpdate] = Field(default_factory=list)
    pending_updates: list[PageUpdate] = Field(default_factory=list)
    not_adopted_reason: str | None = None
