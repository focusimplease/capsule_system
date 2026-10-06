"""结果可见性服务：查看处理结果、阅读证据、提出删除建议。

对应 FR-043~046 与 feishu-storage.md 删除契约：
- 只返回终态记录，不存在运行中状态；
- 证据展示包含权威文字，存在来源 URL 时返回该 URL；
- 删除是不可逆操作：本服务只创建审核项并把资源标记为
  deletion_pending，永久删除必须经 review decide 的明确确认（FR-041）。
"""

from __future__ import annotations

from ..contracts.envelope import (
    NOT_FOUND,
    POLICY_BLOCKED,
    VALIDATION_ERROR,
    CapsuleError,
    review_required as review_env,
    success as success_env,
)
from ..domain import ids
from ..domain.entities import ReviewItemRecord


def show_result(request, backend):
    submission = backend.get_submission(request.submission_id)
    if submission is None:
        raise CapsuleError(NOT_FOUND, f"提交记录不存在：{request.submission_id}")
    records = backend.list_processing_records(request.submission_id)
    understandings = backend.list_understandings(request.submission_id)
    evidence_list = backend.list_evidence_by_submission(request.submission_id)
    original = backend.get_original_evidence_by_submission(request.submission_id)
    review_items = backend.list_review_items(submission_id=request.submission_id)
    decisions = []
    for item in review_items:
        decisions.extend(
            {"review_item_id": item.review_item_id, **d.model_dump(exclude={"review_item_id"})}
            for d in backend.list_review_decisions(item.review_item_id))

    pages = []
    for page_id in {p for r in records for p in r.affected_page_refs}:
        page = backend.get_page(page_id)
        if page is not None:
            pages.append({"page_id": page.page_id, "title": page.title,
                          "page_type": page.page_type, "status": page.status,
                          "revision_id": page.revision_id})
    latest = records[-1] if records else None
    understanding = understandings[-1] if understandings else None
    return success_env(
        request.request_id,
        data={
            "submission": submission.model_dump(),
            "latest_processing_record":
                latest.model_dump() if latest else None,
            "understanding": ({
                "topics": understanding.topics,
                "duplicate_parts": understanding.duplicate_parts,
                "novel_parts": understanding.novel_parts,
                "conflicts": understanding.conflicts,
                "conservative_notes": understanding.conservative_notes,
                "key_claims": understanding.key_claims,
            } if understanding else None),
            "evidence": [{"evidence_id": e.evidence_id, "title": e.title,
                          "status": e.status, "preview": e.preview}
                         for e in evidence_list],
            "source_url": original.url if original else None,
            "source_availability": original.availability if original else None,
            "affected_pages": pages,
            "review": [{"review_item_id": i.review_item_id,
                        "review_type": i.review_type, "status": i.status,
                        "trigger_code": i.trigger_code}
                       for i in review_items],
            "review_decisions": decisions,
        },
        result_refs=[request.submission_id])


def show_evidence(request, backend):
    evidence = backend.get_derived_evidence(request.evidence_id)
    if evidence is None:
        raise CapsuleError(NOT_FOUND, f"证据不存在：{request.evidence_id}")
    content = ""
    if evidence.status != "deleted":
        content = backend.read_evidence_content(request.evidence_id)
    original = None
    if evidence.original_evidence_id:
        original = backend.get_original_evidence(evidence.original_evidence_id)
    contributions = backend.list_contributions(evidence_id=request.evidence_id)
    return success_env(
        request.request_id,
        data={
            "evidence": {"evidence_id": evidence.evidence_id,
                         "title": evidence.title, "status": evidence.status,
                         "preview": evidence.preview,
                         "created_at": evidence.created_at},
            "content": content,
            "source_url": original.url if original else None,
            "source_availability": original.availability if original else None,
            "contributions": [{"page_id": c.page_id,
                               "contribution_kind": c.contribution_kind,
                               "summary": c.summary}
                              for c in contributions],
        },
        result_refs=[request.evidence_id])


def propose_evidence_deletion(request, backend):
    """创建不可逆操作审核项（FR-040/041）。

    - "建议删除"（本命令）与"永久删除"（review decide 确认后）是不同结果；
    - 准备删除衍生证据前检查仍在使用的知识页贡献关系，
      并在审核项中说明对追溯能力的影响（feishu-storage.md 删除契约）。
    """
    now = ids.now_utc()
    if request.target_kind == "derived_evidence":
        evidence = backend.get_derived_evidence(request.target_id)
        if evidence is None:
            raise CapsuleError(NOT_FOUND, f"证据不存在：{request.target_id}")
        if evidence.status == "deleted":
            raise CapsuleError(
                POLICY_BLOCKED, "证据已经被删除，不需要再次提议")
        if evidence.status == "deletion_pending":
            raise CapsuleError(
                POLICY_BLOCKED, "证据已处于待删除状态，等待用户确认")
        contributions = backend.list_contributions(evidence_id=request.target_id)
        affected_pages = sorted({c.page_id for c in contributions})
        evidence.status = "deletion_pending"
        backend.save_derived_evidence(evidence)
        target_refs = [request.target_id]
        impact = (f"该证据仍被 {len(affected_pages)} 个知识页的贡献关系引用"
                  if affected_pages else "该证据当前没有被知识页引用")
        reason = (f"建议删除衍生证据「{evidence.title}」。{impact}；"
                  "永久删除后相关页面判断将失去该证据依据，"
                  "历史贡献记录保留但指向已删除证据")
    else:
        original = backend.get_original_evidence(request.target_id)
        if original is None:
            raise CapsuleError(NOT_FOUND, f"原始证据不存在：{request.target_id}")
        target_refs = [request.target_id]
        reason = (f"建议删除原始证据 URL 记录（{original.url}）。"
                  "删除后衍生证据保留，但失去回到原始来源的路径")

    review_item_id = ids.new_id()
    item = ReviewItemRecord(
        review_item_id=review_item_id,
        submission_id=None,
        review_type="irreversible_operation",
        status="open",
        trigger_code="EVIDENCE_DELETION_PROPOSED",
        detected_stage="evidence_management",
        reason=reason,
        evidence_refs=[request.target_id] if request.target_kind == "derived_evidence" else [],
        target_refs=target_refs,
        attempted_actions=[],
        allowed_decisions=[
            {"code": "confirm_delete", "display": "确认永久删除",
             "consequence": "永久删除列出的对象；该操作不可逆",
             "irreversible": True},
            {"code": "reject_delete", "display": "拒绝删除建议",
             "consequence": "对象恢复为 active 并保留，拒绝不视为不采纳"},
        ],
        recommendation={"target_kind": request.target_kind,
                        "target_id": request.target_id},
        suggested_next_task={},
        created_at=now)
    backend.save_review_item(item)
    return review_env(
        request.request_id,
        data={"review_item_id": review_item_id,
              "target_kind": request.target_kind,
              "target_refs": target_refs,
              "note": "已生成不可逆操作审核项；永久删除需要用户对明确对象确认"},
        result_refs=[review_item_id])
