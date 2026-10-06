"""text 输入主流程集成测试：完成、去重、不采纳、冲突与 FR-023。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import (
    anchor_of,
    commit,
    complete_ingest,
    continue_task,
    default_plan,
    default_understanding,
    prepare,
    update_plan,
)

EVIDENCE = ("胶囊系统主流程集成测试证据：先计算后提交，集中写入飞书终态。"
            "系统只保存文本与来源 URL。")


def test_full_text_flow_completed(backend, workspace, config):
    env = prepare(backend, workspace, config, "text", text=EVIDENCE,
                  user_context="主流程测试")
    assert env.status == "success"
    assert env.next_action.action_code == "compare_knowledge"
    task_ref = env.data["task_ref"]
    assert Path(task_ref).is_dir(), "任务中间状态应存在于受控工作区"

    # 证据上下文文件包含用户说明
    evidence_ctx = Path(task_ref) / "ctx" / "evidence.txt"
    assert "主流程测试" in evidence_ctx.read_text(encoding="utf-8")

    env = commit_complete = complete_ingest(backend, workspace, config,
                                            task_ref, EVIDENCE)
    assert env.status == "success"
    assert env.data["outcome"] == "completed"
    submission_id = env.data["submission_id"]
    evidence_id = env.data["evidence_id"]
    page_ids = env.data["affected_pages"]
    assert len(page_ids) == 1

    # 终态校验
    submission = backend.get_submission(submission_id)
    assert submission.status == "completed"
    assert submission.input_kind == "text"
    evidence = backend.get_derived_evidence(evidence_id)
    assert evidence.status == "active"
    assert backend.read_evidence_content(evidence_id) == EVIDENCE
    page = backend.get_page(page_ids[0])
    assert page.status == "active"
    assert backend.list_contributions(evidence_id=evidence_id), "必须记录贡献关系"
    records = backend.list_processing_records(submission_id)
    assert len(records) == 1
    assert records[0].outcome == "completed"
    assert records[0].reason_code is None, "有页面写入，不是 NOT_ADOPTED"
    understandings = backend.list_understandings(submission_id)
    assert len(understandings) == 1
    assert understandings[0].validation_status == "valid"

    # 任务工作区清理：无残留可恢复状态
    assert not Path(task_ref).exists()


def test_entry_dedup_blocks_identical_text(backend, workspace, config):
    # 去重针对已形成终态的提交记录：先完整处理第一份
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    complete_ingest(backend, workspace, config, task_ref, EVIDENCE)
    assert len(backend.submissions) == 1

    # 第二次提交相同文本：直接拦截，不创建任务
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    assert env.status == "success"
    assert env.data["entry_dedup"]["duplicate"] is True
    assert env.data["entry_dedup"]["duplicate_of"]
    assert env.next_action is None
    assert len(backend.submissions) == 1, "去重不创建新提交记录"


def test_not_adopted_completes_without_pages(backend, workspace, config):
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    understanding = default_understanding(EVIDENCE, novel=False)
    understanding["duplicate_parts"] = [EVIDENCE[:20]]
    plan = {"creates": [], "updates": [], "pending_updates": [],
            "not_adopted_reason": "内容与既有知识重复，无新增价值"}
    env = complete_ingest(backend, workspace, config, task_ref, EVIDENCE,
                          understanding=understanding, plan=plan)
    assert env.status == "success"
    assert env.data["outcome"] == "completed"
    assert env.data["affected_pages"] == []
    records = backend.list_processing_records(env.data["submission_id"])
    assert records[0].reason_code == "NOT_ADOPTED"
    assert "重复" in records[0].reason_detail


def test_novel_without_page_writes_rejected(backend, workspace, config):
    """FR-023/US-1.5：有已采纳新增内容却无页面写入，不能形成完成终态。"""
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    understanding = default_understanding(EVIDENCE, novel=True)
    plan = {"creates": [], "updates": [], "pending_updates": [],
            "not_adopted_reason": None}
    env = continue_task(backend, workspace, config, task_ref,
                        "compare_knowledge", understanding)
    assert env.status == "success"
    env = continue_task(backend, workspace, config, task_ref,
                        "organize_pages", plan)
    assert env.status == "success"
    env = commit(backend, workspace, config, task_ref)
    assert env.status == "failed"
    assert env.error.code == "POLICY_BLOCKED"
    assert "新增内容" in env.error.message


def test_conflict_flow_adopts_safe_parts_and_defers_conflict(
        backend, workspace, config):
    # 第一份资料创建既有页面
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    env = complete_ingest(backend, workspace, config, task_ref, EVIDENCE)
    existing_page = backend.get_page(env.data["affected_pages"][0])
    original_content = backend.read_page_content(existing_page.page_id)

    # 第二份资料与既有判断冲突
    conflicting = EVIDENCE + "新资料挑战既有判断：集中写入应改为分布式写入。"
    env = prepare(backend, workspace, config, "text", text=conflicting)
    task_ref = env.data["task_ref"]
    anchor = anchor_of(conflicting)
    understanding = default_understanding(conflicting)
    understanding["conflicts"] = [{
        "page_id": existing_page.page_id,
        "existing_claim": "先计算后提交",
        "new_claim": "分布式写入",
        "evidence_anchor": anchor,
        "resolution": "needs_user",
    }]
    # 非冲突部分：新建另一个页面；冲突部分：挂起更新
    # （挂起更新的对象是概念页，内容必须保持四要素完整）
    plan = default_plan(conflicting, title="冲突伴随的新页面")
    plan["pending_updates"] = [{
        "page_id": existing_page.page_id,
        "revision_id": existing_page.revision_id,
        "content": ("# 冲突更新\n\n分布式写入的判断。\n\n## 定义\n\n更新定义。\n\n"
                    "## 边界\n\n更新边界。\n\n## 相近概念\n\n相近。\n\n"
                    "## 典型用法\n\n用法。"),
        "claim_anchors": [{"claim": "冲突判断", "evidence_anchor": anchor}],
        "change_summary": "冲突挂起更新",
    }]
    env = continue_task(backend, workspace, config, task_ref,
                        "compare_knowledge", understanding)
    assert env.status == "success"
    env = continue_task(backend, workspace, config, task_ref,
                        "organize_pages", plan)
    assert env.status == "success"
    assert env.data["planned_outcome"] == "needs_review"
    assert env.data["note"]

    env = commit(backend, workspace, config, task_ref)
    assert env.status == "review_required"
    review_item_id = env.result_refs[0]
    submission = backend.get_submission(env.data["submission_id"])
    assert submission.status == "needs_review"

    item = backend.get_review_item(review_item_id)
    assert item.review_type == "knowledge_conflict"
    assert item.trigger_code == "KNOWLEDGE_CONFLICT"
    assert item.recommendation["conflicts"][0]["new_claim"] == "分布式写入"
    assert item.recommendation["pending_updates"][0]["page_id"] == existing_page.page_id
    assert {d["code"] for d in item.allowed_decisions} == {
        "keep_both_with_condition", "adopt_existing", "adopt_new", "defer"}

    # 非冲突部分已采纳：新页面创建；冲突部分未写入：既有页面保持
    assert backend.read_page_content(existing_page.page_id) == original_content
    records = backend.list_processing_records(submission.submission_id)
    assert records[0].affected_page_refs, "非冲突部分（新页面）应正常采纳"


def test_unresolved_conflict_without_pending_updates_rejected(
        backend, workspace, config):
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    env = complete_ingest(backend, workspace, config, task_ref, EVIDENCE)
    page = backend.get_page(env.data["affected_pages"][0])

    conflicting = EVIDENCE + "冲突内容：不应静默覆盖。"
    env = prepare(backend, workspace, config, "text", text=conflicting)
    task_ref = env.data["task_ref"]
    understanding = default_understanding(conflicting)
    understanding["conflicts"] = [{
        "page_id": page.page_id, "existing_claim": "旧", "new_claim": "新",
        "evidence_anchor": anchor_of(conflicting), "resolution": "needs_user",
    }]
    plan = default_plan(conflicting, title="无挂起更新的方案")
    env = continue_task(backend, workspace, config, task_ref,
                        "compare_knowledge", understanding)
    assert env.status == "success"
    env = continue_task(backend, workspace, config, task_ref,
                        "organize_pages", plan)
    assert env.status == "failed"
    assert env.error.code == "VALIDATION_ERROR"
    assert "pending_updates" in env.error.message


def test_page_update_increments_revision_and_records_contribution(
        backend, workspace, config):
    env = prepare(backend, workspace, config, "text", text=EVIDENCE)
    task_ref = env.data["task_ref"]
    env = complete_ingest(backend, workspace, config, task_ref, EVIDENCE)
    page = backend.get_page(env.data["affected_pages"][0])
    first_revision = page.revision_id

    follow_up = EVIDENCE + "补充材料：更新既有页面的新增内容。"
    env = prepare(backend, workspace, config, "text", text=follow_up)
    task_ref = env.data["task_ref"]
    understanding = default_understanding(follow_up)
    understanding["topics"] = [{
        "name": page.title, "existing_page_id": page.page_id,
        "rationale": "归属既有页面"}]
    plan = update_plan(page, follow_up)
    env = complete_ingest(backend, workspace, config, task_ref, follow_up,
                          understanding=understanding, plan=plan)
    assert env.status == "success"
    assert env.data["affected_pages"] == [page.page_id]

    updated = backend.get_page(page.page_id)
    assert updated.revision_id != first_revision
    assert "新增补充段落" in backend.read_page_content(page.page_id)
    contributions = backend.list_contributions(page_id=page.page_id)
    assert len(contributions) == 2, "两次证据贡献都应可追溯"
    assert contributions[-1].contribution_kind == "added"
    assert contributions[-1].page_revision_before == first_revision


class TestInputValidation:
    def test_empty_text_rejected(self, backend, workspace, config):
        env = prepare(backend, workspace, config, "text", text="   ")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_text_with_path_rejected(self, backend, workspace, config, tmp_path):
        env = prepare(backend, workspace, config, "text", text=EVIDENCE,
                      path=str(tmp_path / "x.txt"))
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"
        assert "不接受本地文件路径" in env.error.message

    def test_media_without_path_rejected(self, backend, workspace, config):
        env = prepare(backend, workspace, config, "pdf")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_web_without_url_rejected(self, backend, workspace, config):
        env = prepare(backend, workspace, config, "web_url")
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_web_with_path_rejected(self, backend, workspace, config, tmp_path):
        env = prepare(backend, workspace, config, "web_url", url="https://x.example",
                      path=str(tmp_path / "x.html"))
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"
        assert "不接受本地文件路径" in env.error.message

    def test_commit_before_ready_rejected(self, backend, workspace, config):
        env = prepare(backend, workspace, config, "text", text=EVIDENCE)
        task_ref = env.data["task_ref"]
        env = commit(backend, workspace, config, task_ref)
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_action_mismatch_rejected(self, backend, workspace, config):
        env = prepare(backend, workspace, config, "text", text=EVIDENCE)
        task_ref = env.data["task_ref"]
        env = continue_task(backend, workspace, config, task_ref,
                            "organize_pages", default_plan(EVIDENCE))
        assert env.status == "failed"
        assert env.error.code == "VALIDATION_ERROR"

    def test_unknown_task_rejected(self, backend, workspace, config):
        env = continue_task(backend, workspace, config, "/nonexistent/task",
                            "compare_knowledge", {})
        assert env.status == "failed"
        assert env.error.code in {"VALIDATION_ERROR", "NOT_FOUND"}


@pytest.mark.parametrize("kind", ["pdf", "image", "audio", "video"])
def test_media_kinds_require_path(backend, workspace, config, kind):
    env = prepare(backend, workspace, config, kind)
    assert env.status == "failed"
    assert env.error.code == "VALIDATION_ERROR"
