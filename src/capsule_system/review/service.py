"""审核队列服务：列表、详情与用户决定执行。

对应 FR-031~042：
- 只接受审核项列出的决定代码，不可逆决定必须携带对明确对象的确认；
- 决定追加保存（ReviewDecision），当前状态只是投影；
- 需要继续处理时返回建议的新任务输入，由 Agent 另行调用
  ingest prepare，不恢复原任务执行现场（FR-037）。
"""

from __future__ import annotations

from ..contracts.envelope import (
    NOT_FOUND,
    STALE_VERSION,
    VALIDATION_ERROR,
    CapsuleError,
    success as success_env,
)
from ..domain import fingerprint as fp
from ..domain import ids, policies
from ..domain.entities import (
    EvidenceContributionRecord,
    ProcessingRecord,
    ReviewDecisionRecord,
)

_INTERRUPTION_DECISIONS = {"supplement_context", "retry", "specify_topic",
                           "resubmit", "terminate"}
_CONFLICT_DECISIONS = {"keep_both_with_condition", "adopt_existing",
                       "adopt_new", "defer"}


def list_reviews(request, backend):
    items = backend.list_review_items(
        status=request.status, review_type=request.review_type,
        trigger_code=request.trigger_code,
        detected_stage=request.detected_stage,
        rule_gap_flag=request.rule_gap_flag,
        limit=request.limit, offset=request.offset)
    return success_env(
        request.request_id,
        data={"items": [{
            "review_item_id": i.review_item_id,
            "review_type": i.review_type,
            "status": i.status,
            "trigger_code": i.trigger_code,
            "detected_stage": i.detected_stage,
            "rule_gap_flag": i.rule_gap_flag,
            "reason": i.reason,
            "created_at": i.created_at,
        } for i in items], "count": len(items)})


def show_review(request, backend):
    item = backend.get_review_item(request.review_item_id)
    if item is None:
        raise CapsuleError(NOT_FOUND, f"审核项不存在：{request.review_item_id}")
    decisions = backend.list_review_decisions(request.review_item_id)

    source_url = None
    source_availability = None
    if item.submission_id:
        original = backend.get_original_evidence_by_submission(item.submission_id)
        if original:
            source_url = original.url
            source_availability = original.availability
    evidence = []
    for evidence_id in item.evidence_refs:
        record = backend.get_derived_evidence(evidence_id)
        if record is not None:
            evidence.append({"evidence_id": record.evidence_id,
                             "title": record.title, "status": record.status,
                             "preview": record.preview})
    pages = []
    for page_id in item.affected_page_refs:
        page = backend.get_page(page_id)
        if page is not None:
            pages.append({"page_id": page.page_id, "title": page.title,
                          "page_type": page.page_type,
                          "revision_id": page.revision_id})
    return success_env(
        request.request_id,
        data={
            "review_item": item.model_dump(),
            "source_url": source_url,
            "source_availability": source_availability,
            "evidence": evidence,
            "affected_pages": pages,
            "decisions": [d.model_dump() for d in decisions],
        },
        result_refs=[item.review_item_id])


def decide_review(request, backend):
    item = backend.get_review_item(request.review_item_id)
    if item is None:
        raise CapsuleError(NOT_FOUND, f"审核项不存在：{request.review_item_id}")
    chosen = policies.validate_review_decision(
        item, request.decision_code, request.explicit_confirmation,
        request.target_refs)

    code = request.decision_code
    if item.review_type == "processing_interruption":
        if code not in _INTERRUPTION_DECISIONS:
            raise CapsuleError(
                VALIDATION_ERROR, f"处理中断审核项不接受决定：{code}")
        result, suggested = _execute_interruption(item, request, backend)
    elif item.review_type == "knowledge_conflict":
        if code not in _CONFLICT_DECISIONS:
            raise CapsuleError(
                VALIDATION_ERROR, f"知识冲突审核项不接受决定：{code}")
        result, suggested = _execute_conflict(item, request, backend)
    else:
        result, suggested = _execute_irreversible(item, request, backend)

    now = ids.now_utc()
    decision = ReviewDecisionRecord(
        review_decision_id=ids.new_id(),
        review_item_id=item.review_item_id,
        decision_code=code,
        review_item_version=item.version,
        user_note=request.user_note,
        target_refs=request.target_refs,
        explicit_confirmation=request.explicit_confirmation,
        execution_status="succeeded",
        result=result,
        created_at=now, executed_at=now)
    backend.append_review_decision(decision)

    # 关闭审核项；暂缓处理视为取消而不是解决
    item.status = "cancelled" if code == "defer" else "resolved"
    item.resolved_at = now
    item.version += 1
    backend.update_review_item(item)

    return success_env(
        request.request_id,
        data={"review_item_id": item.review_item_id,
              "decision_code": code,
              "review_item_status": item.status,
              "execution_result": result,
              "suggested_next_task": suggested,
              "note": "单次用户决定不会自动成为全局规则（FR-056）"},
        result_refs=[item.review_item_id, decision.review_decision_id])


# ---- 处理中断 ----

def _submission_input_hint(item, backend) -> dict:
    """根据已保存终态整理新任务的输入建议（FR-036/037）。"""
    hint: dict = {"note": "决定后由 Agent 启动新的处理任务，不恢复原任务执行现场"}
    if not item.submission_id:
        hint["requires_new_input"] = True
        return hint
    submission = backend.get_submission(item.submission_id)
    if submission is None:
        hint["requires_new_input"] = True
        return hint
    original = backend.get_original_evidence_by_submission(item.submission_id)
    if original is not None:
        hint["requires_new_input"] = False
        hint["input"] = {"kind": "web_url", "url": original.url}
        return hint
    hint["requires_new_input"] = True
    hint["input_needed"] = ("资料没有来源 URL，系统未保存原始附件；"
                            "继续处理需要重新提交资料")
    return hint


def _execute_interruption(item, request, backend):
    code = request.decision_code
    result: dict = {}
    suggested = _submission_input_hint(item, backend)
    if code in ("supplement_context", "specify_topic") and not (request.user_note or "").strip():
        raise CapsuleError(
            VALIDATION_ERROR,
            f"决定 {code} 必须附带用户补充的{'说明' if code == 'supplement_context' else '主题指定'}",
            suggested_action="在 user_note 中提供内容后重新提交决定")
    if code == "supplement_context":
        suggested["user_context"] = request.user_note
        result = {"effect": "new_task_with_supplement_context"}
    elif code == "retry":
        suggested["mode"] = "retry_once"
        result = {"effect": "new_task_retry"}
    elif code == "specify_topic":
        suggested["user_context"] = f"用户指定主题归属：{request.user_note}"
        result = {"effect": "new_task_with_specified_topic"}
    elif code == "resubmit":
        suggested["requires_new_input"] = True
        suggested["input_needed"] = "提供可读取的新版本输入（如带文字层的文件）"
        result = {"effect": "await_resubmission"}
    elif code == "terminate":
        if item.submission_id:
            submission = backend.get_submission(item.submission_id)
            if submission is not None:
                submission.status = "terminated"
                submission.updated_at = ids.now_utc()
                backend.save_submission(submission)
                backend.save_processing_record(ProcessingRecord(
                    processing_record_id=ids.new_id(),
                    submission_id=item.submission_id,
                    outcome="terminated", reason_code="TERMINATED",
                    reason_detail=request.user_note,
                    omissions=[], evidence_refs=list(item.evidence_refs),
                    affected_page_refs=list(item.affected_page_refs),
                    created_at=ids.now_utc()))
        result = {"effect": "terminated", "submission_status": "terminated"}
        suggested = {"note": "处理已按用户决定终止，记录保留"}
    return result, suggested


# ---- 知识冲突 ----

def _apply_pending_updates(item, request, backend, keep_both: bool) -> dict:
    pending = item.recommendation.get("pending_updates", [])
    if not pending:
        raise CapsuleError(
            VALIDATION_ERROR, "审核项没有挂起的页面更新，无法执行该决定")
    updated_pages = []
    evidence_id = item.evidence_refs[0] if item.evidence_refs else None
    now = ids.now_utc()
    for update in pending:
        page = backend.get_page(update["page_id"])
        if page is None:
            raise CapsuleError(NOT_FOUND, f"页面不存在：{update['page_id']}")
        if update["revision_id"] != page.revision_id:
            raise CapsuleError(
                STALE_VERSION,
                f"页面「{page.title}」版本已变化（挂起 {update['revision_id']}，"
                f"当前 {page.revision_id}），不能执行过期更新",
                suggested_action="重新发起处理任务以重新组织该页面")
        if keep_both:
            existing = backend.read_page_content(page.page_id)
            condition = f"\n\n> 限定条件（用户决定）：{request.user_note}\n" if request.user_note else "\n"
            content = (existing.rstrip()
                       + "\n\n## 补充判断（保留双方）\n\n"
                       + update["content"].strip() + condition)
        else:
            content = update["content"]
        updated = backend.update_page_content(
            page.page_id, update["revision_id"], content, fp.content_hash(content))
        if evidence_id:
            backend.save_contribution(EvidenceContributionRecord(
                contribution_id=ids.new_id(), evidence_id=evidence_id,
                page_id=page.page_id, understanding_id="",
                contribution_kind="added",
                claim_anchors=update.get("claim_anchors", []),
                page_revision_before=update["revision_id"],
                page_revision_after=updated.revision_id,
                summary=update.get("change_summary")
                or f"冲突决定{'保留双方' if keep_both else '采纳新观点'}后更新页面「{page.title}」",
                created_at=now))
        updated_pages.append({"page_id": page.page_id, "title": page.title,
                              "revision_id": updated.revision_id})
    return {"effect": "pages_updated", "pages": updated_pages}


def _execute_conflict(item, request, backend):
    code = request.decision_code
    if code == "keep_both_with_condition":
        if not (request.user_note or "").strip():
            raise CapsuleError(
                VALIDATION_ERROR, "保留双方并限定条件必须附带用户的限定说明",
                suggested_action="在 user_note 中提供限定条件后重新提交决定")
        result = _apply_pending_updates(item, request, backend, keep_both=True)
        suggested = {"note": "新观点已作为补充判断写入页面，用户说明已记录为限定条件"}
    elif code == "adopt_new":
        result = _apply_pending_updates(item, request, backend, keep_both=False)
        suggested = {"note": "挂起的更新已替换对应页面内容"}
    elif code == "adopt_existing":
        result = {"effect": "not_adopted",
                  "note": "页面保持既有判断，冲突部分记为不被采纳"}
        suggested = {"note": "未修改页面；不采纳理由已随决定保留"}
    else:  # defer
        result = {"effect": "deferred", "note": "本次不修改页面，可另行发起处理"}
        suggested = {"note": "审核项已关闭；需要处理时重新发起任务"}
    return result, suggested


# ---- 不可逆操作 ----

def _execute_irreversible(item, request, backend):
    code = request.decision_code
    recommendation = item.recommendation or {}
    target_kind = recommendation.get("target_kind")
    target_id = recommendation.get("target_id") or (
        item.target_refs[0] if item.target_refs else None)
    if code == "confirm_delete":
        if not target_id:
            raise CapsuleError(
                VALIDATION_ERROR, "审核项没有列明准备影响的对象，不能执行删除")
        if target_kind == "derived_evidence":
            backend.mark_evidence_deleted(target_id)
            result = {"effect": "permanently_deleted",
                      "target_kind": target_kind, "target_id": target_id}
        elif target_kind == "original_evidence_url":
            backend.delete_original_evidence(target_id)
            result = {"effect": "permanently_deleted",
                      "target_kind": target_kind, "target_id": target_id}
        else:
            raise CapsuleError(
                VALIDATION_ERROR,
                f"未知的删除对象类型：{target_kind}，拒绝执行")
        return result, {"note": "对象已永久删除，操作不可逆"}
    # reject_delete
    if target_kind == "derived_evidence":
        evidence = backend.get_derived_evidence(target_id) if target_id else None
        if evidence is not None and evidence.status == "deletion_pending":
            evidence.status = "active"
            backend.save_derived_evidence(evidence)
    result = {"effect": "deletion_rejected",
              "note": "对象继续保留；拒绝删除不视为不采纳"}
    return result, {"note": "删除建议被拒绝，对象保留"}
