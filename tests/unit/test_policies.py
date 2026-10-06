"""策略校验单元测试：Python 对 Agent 结果的全部不信任检查。"""

from __future__ import annotations

import pytest

from capsule_system.contracts import actions as act
from capsule_system.contracts.envelope import CapsuleError
from capsule_system.domain import policies
from capsule_system.domain.entities import KnowledgePageRecord, ReviewItemRecord

EVIDENCE = "胶囊系统只保存文本与来源 URL，不保存任何附件。"

PAGE = KnowledgePageRecord(
    page_id="page-1", page_type="concept", title="既有概念",
    revision_id="rev-1", content_hash="h", created_at="2026-01-01T00:00:00Z",
    updated_at="2026-01-01T00:00:00Z")


def understanding(**overrides) -> act.UnderstandEvidenceResult:
    data = {
        "topics": [{"name": "主题", "existing_page_id": None}],
        "key_claims": [{"claim": "判断", "evidence_anchor": EVIDENCE[:10]}],
        "duplicate_parts": [],
        "novel_parts": [{"kind": "explanation", "text": "新增",
                         "evidence_anchor": EVIDENCE[:10]}],
        "conflicts": [],
        "target_pages": [],
        "conservative_notes": [],
    }
    data.update(overrides)
    return act.UnderstandEvidenceResult(**data)


class TestCheckNoImageRefs:
    def test_markdown_image_ref_blocked(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.check_no_image_refs("看图 ![alt](http://x/y.png)", "页面")
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_external_image_url_blocked(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.check_no_image_refs("参考 http://x/y.JPG", "页面")
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_text_graphics_allowed(self):
        # ASCII 图与 Mermaid 源码是文字，允许保存
        policies.check_no_image_refs("```mermaid\nA-->B\n```\n+---+\n| a |\n+---+", "页面")

    def test_plain_markdown_table_allowed(self):
        policies.check_no_image_refs("| a | b |\n| - | - |\n| 1 | 2 |", "页面")


class TestValidateUnderstanding:
    def test_valid_result_passes(self):
        policies.validate_understanding(understanding(), EVIDENCE, set())

    def test_empty_topics_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_understanding(understanding(topics=[]), EVIDENCE, set())
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_anchor_must_be_true_substring(self):
        result = understanding(key_claims=[
            {"claim": "判断", "evidence_anchor": "不存在于证据中的锚点"}])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_understanding(result, EVIDENCE, set())
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_empty_anchor_rejected(self):
        result = understanding(key_claims=[{"claim": "判断", "evidence_anchor": "  "}])
        with pytest.raises(CapsuleError):
            policies.validate_understanding(result, EVIDENCE, set())

    def test_topic_reference_to_missing_page_rejected(self):
        result = understanding(topics=[{"name": "主题", "existing_page_id": "ghost"}])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_understanding(result, EVIDENCE, set())
        assert excinfo.value.code == "NOT_FOUND"

    def test_conflict_must_reference_existing_page(self):
        result = understanding(conflicts=[{
            "page_id": "ghost", "existing_claim": "旧", "new_claim": "新",
            "evidence_anchor": EVIDENCE[:10], "resolution": "needs_user"}])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_understanding(result, EVIDENCE, set())
        assert excinfo.value.code == "NOT_FOUND"

    def test_conflict_anchor_validated(self):
        result = understanding(conflicts=[{
            "page_id": "page-1", "existing_claim": "旧", "new_claim": "新",
            "evidence_anchor": "凭空锚点", "resolution": "needs_user"}])
        with pytest.raises(CapsuleError):
            policies.validate_understanding(result, EVIDENCE, {"page-1"})

    def test_novel_and_duplicate_must_not_overlap(self):
        result = understanding(
            duplicate_parts=["同一段内容"],
            novel_parts=[{"kind": "example", "text": "同一段内容",
                          "evidence_anchor": EVIDENCE[:10]}])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_understanding(result, EVIDENCE, set())
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_all_empty_rejected(self):
        result = understanding(duplicate_parts=[], novel_parts=[], conflicts=[])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_understanding(result, EVIDENCE, set())
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_duplicate_only_is_valid(self):
        result = understanding(duplicate_parts=[EVIDENCE[:10]], novel_parts=[])
        policies.validate_understanding(result, EVIDENCE, set())

    def test_conservative_notes_do_not_block(self):
        result = understanding(conservative_notes=["轻微不确定，保守继续"])
        policies.validate_understanding(result, EVIDENCE, set())


def test_unresolved_conflicts_filters_needs_user():
    result = understanding(conflicts=[
        {"page_id": "page-1", "existing_claim": "旧", "new_claim": "新",
         "evidence_anchor": EVIDENCE[:10], "resolution": "auto_supersede"},
        {"page_id": "page-1", "existing_claim": "旧2", "new_claim": "新2",
         "evidence_anchor": EVIDENCE[:10], "resolution": "needs_user"}])
    unresolved = policies.unresolved_conflicts(result)
    assert [c.new_claim for c in unresolved] == ["新2"]


class TestValidateWebImagesResult:
    def test_ignored_with_reason_passes(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(image_key="img-1", ignored=True, reason="装饰性图片")])
        policies.validate_web_images_result(result, {"img-1"})

    def test_converted_text_passes(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(image_key="img-1", text="图中的表格：| a | 1 |")])
        policies.validate_web_images_result(result, {"img-1"})

    def test_unknown_key_rejected(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(image_key="ghost", ignored=True, reason="r")])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_web_images_result(result, {"img-1"})
        assert excinfo.value.code == "NOT_FOUND"

    def test_duplicate_key_rejected(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(image_key="img-1", ignored=True, reason="r"),
            act.WebImageHandling(image_key="img-1", ignored=True, reason="r")])
        with pytest.raises(CapsuleError):
            policies.validate_web_images_result(result, {"img-1"})

    def test_ignored_without_reason_rejected(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(image_key="img-1", ignored=True)])
        with pytest.raises(CapsuleError):
            policies.validate_web_images_result(result, {"img-1"})

    def test_not_ignored_without_text_rejected(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(image_key="img-1")])
        with pytest.raises(CapsuleError):
            policies.validate_web_images_result(result, {"img-1"})

    def test_converted_text_with_image_url_rejected(self):
        result = act.UnderstandWebImagesResult(images=[
            act.WebImageHandling(
                image_key="img-1", text="见 http://x/y.png 原图")])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_web_images_result(result, {"img-1"})
        assert excinfo.value.code == "POLICY_BLOCKED"


class TestValidatePagePlan:
    def valid_plan(self, **overrides) -> act.OrganizePagesResult:
        data = {
            "creates": [{
                "page_type": "concept", "title": "新概念",
                "content": "# 新概念\n\n## 定义\n定义。\n\n## 边界\n边界。\n\n"
                           "## 相近概念\n相近。\n\n## 典型用法\n用法。\n",
                "claim_anchors": [{"claim": "判断",
                                   "evidence_anchor": EVIDENCE[:10]}],
            }],
            "updates": [], "pending_updates": [], "not_adopted_reason": None,
        }
        data.update(overrides)
        return act.OrganizePagesResult(**data)

    def test_valid_create_passes(self):
        policies.validate_page_plan(self.valid_plan(), {}, EVIDENCE, 60000, set())

    def test_empty_content_rejected(self):
        plan = self.valid_plan(creates=[{
            "page_type": "topic", "title": "t", "content": "   ",
        }])
        with pytest.raises(CapsuleError):
            policies.validate_page_plan(plan, {}, EVIDENCE, 60000, set())

    def test_content_without_heading_rejected(self):
        plan = self.valid_plan(creates=[{
            "page_type": "topic", "title": "t",
            "content": "没有标题行的正文段落。",
        }])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_page_plan(plan, {}, EVIDENCE, 60000, set())
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_page_too_large_rejected(self):
        plan = self.valid_plan(creates=[{
            "page_type": "topic", "title": "t",
            "content": "# 标题\n" + "长" * 100,
        }])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_page_plan(plan, {}, EVIDENCE, 50, set())
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_duplicate_active_title_rejected(self):
        plan = self.valid_plan(creates=[{
            "page_type": "topic", "title": "既有页面",
            "content": "# 既有页面\n正文",
        }])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_page_plan(plan, {}, EVIDENCE, 60000,
                                        {("topic", "既有页面")})
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_concept_page_requires_four_sections(self):
        plan = self.valid_plan(creates=[{
            "page_type": "concept", "title": "缺要素",
            "content": "# 缺要素\n\n## 定义\n只有定义。\n",
        }])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_page_plan(plan, {}, EVIDENCE, 60000, set())
        assert excinfo.value.code == "POLICY_BLOCKED"
        assert "边界" in excinfo.value.message

    def test_concept_sections_in_english_also_accepted(self):
        plan = self.valid_plan(creates=[{
            "page_type": "concept", "title": "English",
            "content": ("# English\n\n## definition\nd.\n\n## boundary\nb.\n\n"
                        "## similar\ns.\n\n## usage\nu.\n"),
        }])
        policies.validate_page_plan(plan, {}, EVIDENCE, 60000, set())

    def test_update_with_stale_revision_rejected(self):
        page2 = PAGE.model_copy(update={"revision_id": "rev-9"})
        plan = self.valid_plan(updates=[{
            "page_id": "page-1", "revision_id": "rev-1",
            "content": "# 标题\n更新内容",
        }])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_page_plan(plan, {"page-1": page2}, EVIDENCE, 60000, set())
        assert excinfo.value.code == "STALE_VERSION"

    def test_update_missing_page_rejected(self):
        plan = self.valid_plan(updates=[{
            "page_id": "ghost", "revision_id": "rev-1",
            "content": "# 标题\n更新内容",
        }])
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_page_plan(plan, {}, EVIDENCE, 60000, set())
        assert excinfo.value.code == "NOT_FOUND"

    def test_update_anchor_not_in_evidence_rejected(self):
        plan = self.valid_plan(updates=[{
            "page_id": "page-1", "revision_id": "rev-1",
            "content": "# 标题\n更新内容",
            "claim_anchors": [{"claim": "判断", "evidence_anchor": "不存在锚点"}],
        }])
        with pytest.raises(CapsuleError):
            policies.validate_page_plan(plan, {"page-1": PAGE}, EVIDENCE, 60000, set())

    def test_pending_updates_validated_like_updates(self):
        plan = self.valid_plan(pending_updates=[{
            "page_id": "ghost", "revision_id": "rev-1",
            "content": "# 标题\n内容",
        }])
        with pytest.raises(CapsuleError):
            policies.validate_page_plan(plan, {"page-1": PAGE}, EVIDENCE, 60000, set())

    def test_content_with_image_ref_rejected(self):
        plan = self.valid_plan(creates=[{
            "page_type": "topic", "title": "t",
            "content": "# t\n\n![x](http://x/y.png)",
        }])
        with pytest.raises(CapsuleError):
            policies.validate_page_plan(plan, {}, EVIDENCE, 60000, set())


def review_item(**overrides) -> ReviewItemRecord:
    data = {
        "review_item_id": "ri-1", "review_type": "irreversible_operation",
        "trigger_code": "EVIDENCE_DELETION_PROPOSED",
        "detected_stage": "evidence_management", "reason": "r",
        "target_refs": ["ev-1"],
        "allowed_decisions": [
            {"code": "confirm_delete", "irreversible": True},
            {"code": "reject_delete"},
        ],
        "created_at": "2026-01-01T00:00:00Z",
    }
    data.update(overrides)
    return ReviewItemRecord(**data)


class TestValidateReviewDecision:
    def test_open_item_valid_code_passes(self):
        chosen = policies.validate_review_decision(
            review_item(), "reject_delete", False, [])
        assert chosen["code"] == "reject_delete"

    def test_closed_item_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_review_decision(
                review_item(status="resolved"), "reject_delete", False, [])
        assert excinfo.value.code == "STALE_VERSION"

    def test_unknown_code_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_review_decision(review_item(), "adopt_new", False, [])
        assert excinfo.value.code == "VALIDATION_ERROR"

    def test_irreversible_without_confirmation_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_review_decision(
                review_item(), "confirm_delete", False, ["ev-1"])
        assert excinfo.value.code == "CONFIRMATION_REQUIRED"

    def test_irreversible_with_wrong_target_rejected(self):
        with pytest.raises(CapsuleError) as excinfo:
            policies.validate_review_decision(
                review_item(), "confirm_delete", True, ["ev-other"])
        assert excinfo.value.code == "CONFIRMATION_REQUIRED"

    def test_irreversible_with_exact_target_passes(self):
        chosen = policies.validate_review_decision(
            review_item(), "confirm_delete", True, ["ev-1"])
        assert chosen["irreversible"] is True
