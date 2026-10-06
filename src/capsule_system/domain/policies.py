"""策略校验：Python 不信任 Agent 提交的结构化结果。

覆盖：
- 理解结果（证据锚点必须是证据文字的真实子串，冲突必须引用既有页面，
  重复与新增不能混同，轻微不确定只能保守继续）；
- 页面组织（版本匹配、结构完整、粒度上限、标题唯一、概念页四要素、
  纯文本图形约束）；
- 审核决定（决定代码合法、版本匹配、不可逆确认）。
"""

from __future__ import annotations

import re

from ..contracts import actions as act
from ..contracts.envelope import (
    CONFIRMATION_REQUIRED,
    NOT_FOUND,
    POLICY_BLOCKED,
    STALE_VERSION,
    VALIDATION_ERROR,
    CapsuleError,
)

# 外部图片引用禁令（FR-015/030、SC-006/012）：
# 不得保存、嵌入或通过外部图片地址显示原图
_IMAGE_REF_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_IMAGE_URL_PATTERN = re.compile(
    r"https?://\S+\.(?:png|jpe?g|gif|webp|svg|bmp)(?:\?\S*)?", re.IGNORECASE)

# 概念页四要素（FR-026）：定义、边界、相近概念、典型用法
_CONCEPT_SECTIONS = (("定义", "definition"), ("边界", "boundary"),
                     ("相近概念", "similar"), ("典型用法", "usage"))


def check_no_image_refs(content: str, what: str) -> None:
    if _IMAGE_REF_PATTERN.search(content):
        raise CapsuleError(
            POLICY_BLOCKED, f"{what}包含 Markdown 图片引用，只能保存可编辑的文字源",
            suggested_action="把图片内容转换为文字、Markdown 表格或文本定义的图形")
    if _IMAGE_URL_PATTERN.search(content):
        raise CapsuleError(
            POLICY_BLOCKED, f"{what}包含外部图片地址，不得保存或引用网页原图",
            suggested_action="移除图片 URL，只保留转换后的文字信息")


# ---- 理解结果 ----

def _require_anchor(evidence_text: str, anchor: str, what: str) -> None:
    if not anchor.strip():
        raise CapsuleError(VALIDATION_ERROR, f"{what} 的证据锚点不能为空")
    if anchor not in evidence_text:
        raise CapsuleError(
            VALIDATION_ERROR,
            f"{what} 的证据锚点未出现在证据文字中，不能作为判断依据",
            details={"anchor": anchor[:80]})


def validate_understanding(result: act.UnderstandEvidenceResult,
                           evidence_text: str,
                           existing_page_ids: set[str]) -> None:
    if not result.topics:
        raise CapsuleError(VALIDATION_ERROR, "理解结果必须给出主题归属判断")
    for topic in result.topics:
        if topic.existing_page_id and topic.existing_page_id not in existing_page_ids:
            raise CapsuleError(
                NOT_FOUND, f"主题引用的页面不存在：{topic.existing_page_id}")
    for claim in result.key_claims:
        _require_anchor(evidence_text, claim.evidence_anchor, "关键判断")
    for conflict in result.conflicts:
        if conflict.page_id not in existing_page_ids:
            raise CapsuleError(
                NOT_FOUND, f"冲突引用的既有页面不存在：{conflict.page_id}")
        _require_anchor(evidence_text, conflict.evidence_anchor, "冲突新判断")
    for novel in result.novel_parts:
        if not novel.text.strip():
            raise CapsuleError(VALIDATION_ERROR, "新增价值部分不能为空")
        if novel.evidence_anchor:
            _require_anchor(evidence_text, novel.evidence_anchor,
                            f"新增价值（{novel.kind}）")
    # 重复与新增不能混同：同一段内容不能既是重复又是新增价值
    duplicated = {p.strip() for p in result.duplicate_parts if p.strip()}
    for novel in result.novel_parts:
        if novel.text.strip() in duplicated:
            raise CapsuleError(
                VALIDATION_ERROR,
                "同一段内容不能同时标记为重复和新增价值",
                details={"text": novel.text[:80]})
    # 重复、新增、冲突不能全为空：完全重复时 duplicate 非空
    if not result.duplicate_parts and not result.novel_parts and not result.conflicts:
        raise CapsuleError(
            VALIDATION_ERROR,
            "理解结果必须区分重复、新增或冲突，不能只生成孤立摘要")
    check_no_image_refs(evidence_text, "证据文字")


def unresolved_conflicts(result: act.UnderstandEvidenceResult) -> list[act.ConflictItem]:
    return [c for c in result.conflicts if c.resolution == "needs_user"]


# ---- 网页图片理解 ----

def validate_web_images_result(result: act.UnderstandWebImagesResult,
                               expected_keys: set[str]) -> None:
    seen: set[str] = set()
    for handling in result.images:
        if handling.image_key not in expected_keys:
            raise CapsuleError(
                NOT_FOUND, f"网页图片候选不存在：{handling.image_key}")
        if handling.image_key in seen:
            raise CapsuleError(
                VALIDATION_ERROR, f"网页图片候选重复处理：{handling.image_key}")
        seen.add(handling.image_key)
        if handling.ignored:
            if not handling.reason.strip():
                raise CapsuleError(VALIDATION_ERROR,
                                   f"忽略图片 {handling.image_key} 必须记录原因")
            continue
        if not handling.text.strip():
            raise CapsuleError(
                VALIDATION_ERROR,
                f"图片 {handling.image_key} 未被忽略，必须提供转换为文字的内容")
        check_no_image_refs(handling.text, f"图片 {handling.image_key} 的转换结果")


# ---- 页面组织 ----

def _validate_page_content(content: str, page_max_chars: int, what: str) -> None:
    if not content.strip():
        raise CapsuleError(VALIDATION_ERROR, f"{what} 的内容不能为空")
    if len(content) > page_max_chars:
        raise CapsuleError(
            POLICY_BLOCKED,
            f"{what} 超过粒度上限 {page_max_chars} 字符，"
            "页面可能退化成资料仓库，应拆分为更聚焦的知识线")
    if not any(line.strip().startswith("#") for line in content.splitlines()):
        raise CapsuleError(
            POLICY_BLOCKED,
            f"{what} 必须保持结构完整的文章（至少包含一个标题），"
            "不能是无结构的条目堆积")
    check_no_image_refs(content, what)


def _validate_concept_sections(content: str, title: str) -> None:
    lowered = content.lower()
    for zh, en in _CONCEPT_SECTIONS:
        if zh not in content and en not in lowered:
            raise CapsuleError(
                POLICY_BLOCKED,
                f"概念页「{title}」必须说明定义、边界、相近概念和典型用法"
                f"（缺少「{zh}」部分）",
                suggested_action="补充缺失的部分后再提交")


def validate_page_plan(plan: act.OrganizePagesResult,
                       pages_by_id: dict,
                       evidence_text: str,
                       page_max_chars: int,
                       active_titles: set[tuple[str, str]]) -> None:
    for create in plan.creates:
        _validate_page_content(create.content, page_max_chars, f"新页面「{create.title}」")
        if not create.title.strip():
            raise CapsuleError(VALIDATION_ERROR, "新页面标题不能为空")
        key = (create.page_type, create.title.strip())
        if key in active_titles:
            raise CapsuleError(
                POLICY_BLOCKED,
                f"同名页面已存在：{create.page_type}/{create.title}，应更新而不是重复创建")
        if create.page_type == "concept":
            _validate_concept_sections(create.content, create.title)
        for anchor in create.claim_anchors:
            _require_anchor(evidence_text, anchor.evidence_anchor,
                            f"新页面「{create.title}」的重要判断")
    for update_list, what in ((plan.updates, "页面更新"), (plan.pending_updates, "冲突挂起更新")):
        for update in update_list:
            page = pages_by_id.get(update.page_id)
            if page is None:
                raise CapsuleError(NOT_FOUND, f"{what} 引用的页面不存在：{update.page_id}")
            if update.revision_id != page.revision_id:
                raise CapsuleError(
                    STALE_VERSION,
                    f"页面「{page.title}」的版本已变化（读取 {update.revision_id}，"
                    f"当前 {page.revision_id}），需要重新读取和组织",
                    suggested_action="重新读取页面当前版本后再提交组织结果")
            _validate_page_content(update.content, page_max_chars,
                                   f"页面「{page.title}」更新")
            if page.page_type == "concept":
                _validate_concept_sections(update.content, page.title)
            for anchor in update.claim_anchors:
                _require_anchor(evidence_text, anchor.evidence_anchor,
                                f"页面「{page.title}」的重要判断")


# ---- 审核决定 ----

def validate_review_decision(review_item, decision_code: str,
                             explicit_confirmation: bool,
                             target_refs: list[str]) -> dict:
    """返回选中的决定定义；校验失败抛出 CapsuleError。"""
    if review_item.status != "open":
        raise CapsuleError(
            STALE_VERSION, f"审核项已经{review_item.status}，不能重复决定")
    allowed = {d["code"]: d for d in review_item.allowed_decisions}
    chosen = allowed.get(decision_code)
    if chosen is None:
        raise CapsuleError(
            VALIDATION_ERROR,
            f"决定代码不在允许列表中：{decision_code}",
            details={"allowed": sorted(allowed)})
    if chosen.get("irreversible", False):
        if not explicit_confirmation:
            raise CapsuleError(
                CONFIRMATION_REQUIRED,
                "不可逆操作必须经用户对明确对象确认后才能执行")
        expected = set(review_item.target_refs or [])
        provided = set(target_refs or [])
        if not expected or expected != provided:
            raise CapsuleError(
                CONFIRMATION_REQUIRED,
                "确认对象必须与审核项列出的准备影响对象完全一致",
                details={"expected": sorted(expected), "provided": sorted(provided)})
    return chosen
