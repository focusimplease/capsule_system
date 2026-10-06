"""审核决定执行集成测试：三类审核 × 全部决定代码。"""

from __future__ import annotations

import pytest

from capsule_system.contracts.envelope import CapsuleError, failed as failed_env
from capsule_system.contracts.requests import ReviewDecideRequest
from capsule_system.review import service as review_service

from tests.helpers import (
    anchor_of,
    complete_ingest,
    continue_task,
    default_plan,
    default_understanding,
    make_scanned_pdf,
    prepare,
    rid,
)

EVIDENCE = "审核决定集成测试证据：用户决定采用追加记录，不恢复任务现场。"


def decide(backend, review_item_id, decision_code, *, user_note=None,
           target_refs=None, explicit_confirmation=False):
    """与 CLI dispatch 相同的错误转换：CapsuleError → 失败信封。"""
    request = ReviewDecideRequest(
        request_id=rid(), review_item_id=review_item_id,
        decision_code=decision_code, user_note=user_note,
        target_refs=target_refs or [],
        explicit_confirmation=explicit_confirmation)
    try:
        return review_service.decide_review(request, backend)
    except CapsuleError as exc:
        return failed_env(request.request_id, exc.code, exc.message,
                          retryable=exc.retryable, details=exc.details,
                          suggested_action=exc.suggested_action)


def make_interruption_item(backend, workspace, config, tmp_path, *, with_url=False):
    """经扫描 PDF 生成处理中断审核项。"""
    path = make_scanned_pdf(tmp_path / "scan.pdf")
    env = prepare(backend, workspace, config, "pdf", path=str(path),
                  url="https://example.com/scan.pdf" if with_url else None)
    assert env.status == "review_required"
    return env.result_refs[0], env.data["submission_id"]


def make_conflict_item(backend, workspace, config):
    """经冲突流程生成知识冲突审核项，返回 (item_id, page)。"""
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    env = complete_ingest(backend, workspace, config, task_ref, EVIDENCE)
    page = backend.get_page(env.data["affected_pages"][0])

    conflicting = EVIDENCE + "冲突：决定应保留双方并限定条件。"
    env = prepare(backend, workspace, config, "text", text=conflicting)
    task_ref = env.data["task_ref"]
    anchor = anchor_of(conflicting)
    understanding = default_understanding(conflicting)
    understanding["conflicts"] = [{
        "page_id": page.page_id, "existing_claim": "既有判断",
        "new_claim": "新判断", "evidence_anchor": anchor,
        "resolution": "needs_user"}]
    plan = default_plan(conflicting, title="冲突伴随页面")
    plan["pending_updates"] = [{
        "page_id": page.page_id, "revision_id": page.revision_id,
        "content": ("# 冲突更新\n\n新判断内容。\n\n## 定义\n\n定义。\n\n"
                    "## 边界\n\n边界。\n\n## 相近概念\n\n相近。\n\n"
                    "## 典型用法\n\n用法。"),
        "claim_anchors": [{"claim": "冲突判断", "evidence_anchor": anchor}],
    }]
    continue_task(backend, workspace, config, task_ref,
                  "compare_knowledge", understanding)
    continue_task(backend, workspace, config, task_ref,
                  "organize_pages", plan)
    from tests.helpers import commit
    env = commit(backend, workspace, config, task_ref)
    assert env.status == "review_required"
    return env.result_refs[0], page


class TestProcessingInterruptionDecisions:
    def test_terminate_closes_and_records(self, backend, workspace, config,
                                          tmp_path):
        item_id, submission_id = make_interruption_item(
            backend, workspace, config, tmp_path)
        env = decide(backend, item_id, "terminate", user_note="不再处理")
        assert env.status == "success"
        assert env.data["execution_result"]["effect"] == "terminated"
        assert backend.get_submission(submission_id).status == "terminated"
        records = backend.list_processing_records(submission_id)
        assert any(r.reason_code == "TERMINATED" for r in records)
        assert backend.get_review_item(item_id).status == "resolved"

    def test_supplement_context_requires_user_note(self, backend, workspace,
                                                   config, tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config, tmp_path)
        env = decide(backend, item_id, "supplement_context")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_supplement_context_suggests_new_task_with_url(
            self, backend, workspace, config, tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config,
                                            tmp_path, with_url=True)
        env = decide(backend, item_id, "supplement_context",
                     user_note="这是补充的上下文说明")
        assert env.status == "success"
        suggested = env.data["suggested_next_task"]
        assert suggested["requires_new_input"] is False
        assert suggested["input"] == {"kind": "web_url",
                                      "url": "https://example.com/scan.pdf"}
        assert suggested["user_context"] == "这是补充的上下文说明"

    def test_retry_without_url_requires_new_input(self, backend, workspace,
                                                  config, tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config, tmp_path)
        env = decide(backend, item_id, "retry")
        assert env.status == "success"
        suggested = env.data["suggested_next_task"]
        assert suggested["requires_new_input"] is True
        assert "未保存原始附件" in suggested["input_needed"]

    def test_specify_topic_builds_user_context(self, backend, workspace,
                                               config, tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config, tmp_path)
        env = decide(backend, item_id, "specify_topic", user_note="知识管理")
        assert env.status == "success"
        assert env.data["suggested_next_task"]["user_context"] == (
            "用户指定主题归属：知识管理")

    def test_resubmit_awaits_new_input(self, backend, workspace, config,
                                       tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config, tmp_path)
        env = decide(backend, item_id, "resubmit")
        assert env.status == "success"
        assert env.data["execution_result"]["effect"] == "await_resubmission"

    def test_conflict_decision_code_rejected_on_interruption(
            self, backend, workspace, config, tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config, tmp_path)
        env = decide(backend, item_id, "adopt_new")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"


class TestKnowledgeConflictDecisions:
    def test_keep_both_with_condition_merges_content(self, backend, workspace,
                                                     config):
        item_id, page = make_conflict_item(backend, workspace, config)
        original = backend.read_page_content(page.page_id)
        env = decide(backend, item_id, "keep_both_with_condition",
                     user_note="仅适用于小型个人系统")
        assert env.status == "success"
        content = backend.read_page_content(page.page_id)
        assert original.rstrip() in content, "既有判断保留"
        assert "补充判断" in content and "新判断内容" in content, "新观点并入"
        assert "限定条件（用户决定）：仅适用于小型个人系统" in content
        # 决定记录追加且审核项关闭
        assert backend.get_review_item(item_id).status == "resolved"
        assert len(backend.list_review_decisions(item_id)) == 1
        assert backend.list_contributions(page_id=page.page_id)

    def test_keep_both_without_condition_rejected(self, backend, workspace,
                                                  config):
        item_id, _ = make_conflict_item(backend, workspace, config)
        env = decide(backend, item_id, "keep_both_with_condition")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_adopt_new_replaces_content(self, backend, workspace, config):
        item_id, page = make_conflict_item(backend, workspace, config)
        env = decide(backend, item_id, "adopt_new")
        assert env.status == "success"
        content = backend.read_page_content(page.page_id)
        assert "新判断内容" in content
        assert "冲突更新" in content

    def test_adopt_existing_keeps_page_unchanged(self, backend, workspace,
                                                 config):
        item_id, page = make_conflict_item(backend, workspace, config)
        original = backend.read_page_content(page.page_id)
        revision = backend.get_page(page.page_id).revision_id
        env = decide(backend, item_id, "adopt_existing",
                     user_note="新证据不够可信")
        assert env.status == "success"
        assert env.data["execution_result"]["effect"] == "not_adopted"
        assert backend.read_page_content(page.page_id) == original
        assert backend.get_page(page.page_id).revision_id == revision

    def test_defer_cancels_item(self, backend, workspace, config):
        item_id, page = make_conflict_item(backend, workspace, config)
        original = backend.read_page_content(page.page_id)
        env = decide(backend, item_id, "defer")
        assert env.status == "success"
        assert backend.get_review_item(item_id).status == "cancelled"
        assert backend.read_page_content(page.page_id) == original

    def test_stale_page_revision_rejected(self, backend, workspace, config):
        item_id, page = make_conflict_item(backend, workspace, config)
        # 审核等待期间页面被其他任务更新
        backend.update_page_content(page.page_id, page.revision_id,
                                    "# 外部更新\n\n## 定义\n\nx\n\n## 边界\n\nx\n\n"
                                    "## 相近概念\n\nx\n\n## 典型用法\n\nx", "h")
        env = decide(backend, item_id, "adopt_new")
        assert env.status == "failed"
        assert env.error.code == "STALE_VERSION"

    def test_decide_twice_rejected(self, backend, workspace, config):
        item_id, _ = make_conflict_item(backend, workspace, config)
        assert decide(backend, item_id, "defer").status == "success"
        env = decide(backend, item_id, "defer")
        assert env.status == "failed"
        assert env.error.code == "STALE_VERSION"

    def test_unknown_decision_code_rejected(self, backend, workspace, config):
        item_id, _ = make_conflict_item(backend, workspace, config)
        env = decide(backend, item_id, "terminate")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"


class TestReviewListAndShow:
    def test_list_filters_by_type_and_trigger(self, backend, workspace, config,
                                              tmp_path):
        item_id, _ = make_interruption_item(backend, workspace, config, tmp_path)
        from capsule_system.contracts.requests import ReviewListRequest
        request = ReviewListRequest(request_id=rid(),
                                    review_type="processing_interruption")
        env = review_service.list_reviews(request, backend)
        assert env.data["count"] >= 1
        request = ReviewListRequest(request_id=rid(),
                                    trigger_code="EXTRACTION_INCOMPLETE")
        env = review_service.list_reviews(request, backend)
        assert any(i["review_item_id"] == item_id for i in env.data["items"])

    def test_show_presents_decision_information(self, backend, workspace,
                                                config):
        item_id, page = make_conflict_item(backend, workspace, config)
        from capsule_system.contracts.requests import ReviewShowRequest
        env = review_service.show_review(
            ReviewShowRequest(request_id=rid(), review_item_id=item_id), backend)
        item = env.data["review_item"]
        assert item["review_type"] == "knowledge_conflict"
        assert item["recommendation"]["conflicts"]
        assert item["recommendation"]["pending_updates"]
        affected_ids = [p["page_id"] for p in env.data["affected_pages"]]
        assert page.page_id in affected_ids, "冲突页面应列入受影响页面"
        assert env.data["evidence"], "审核项应展示关联证据"
        assert {d["code"] for d in item["allowed_decisions"]} == {
            "keep_both_with_condition", "adopt_existing", "adopt_new", "defer"}

    def test_show_unknown_item_not_found(self, backend):
        from capsule_system.contracts.requests import ReviewShowRequest
        with pytest.raises(Exception) as excinfo:
            review_service.show_review(
                ReviewShowRequest(request_id=rid(), review_item_id="ghost"),
                backend)
        assert "不存在" in str(excinfo.value)
