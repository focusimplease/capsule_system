"""规则改进候选服务（FR-054~056）。

- 候选由一组相关审核案例形成，本身不改变全局规则；
- 只有用户明确批准并且需求、SPEC 和验收样例完成更新后，
  规则才可能生效（本服务不提供任何自动启用路径）；
- 关闭候选不反向修改已经完成的个案决定（边界情况）。
"""

from __future__ import annotations

from ..contracts.envelope import (
    NOT_FOUND,
    POLICY_BLOCKED,
    VALIDATION_ERROR,
    CapsuleError,
    success as success_env,
)
from ..domain import ids
from ..domain.entities import RuleCandidateCaseRecord, RuleCandidateRecord


def create_rule_candidate(request, backend):
    if not request.title.strip():
        raise CapsuleError(VALIDATION_ERROR, "候选标题不能为空")
    if not request.gap_summary.strip():
        raise CapsuleError(VALIDATION_ERROR, "候选的缺口说明不能为空")
    now = ids.now_utc()
    candidate_id = ids.new_id()
    backend.save_rule_candidate(RuleCandidateRecord(
        candidate_id=candidate_id,
        title=request.title.strip(),
        gap_summary=request.gap_summary.strip(),
        trigger_pattern=request.trigger_pattern,
        affected_steps=request.affected_steps,
        status="draft", created_at=now, updated_at=now))
    linked = []
    for review_item_id in request.review_item_ids:
        item = backend.get_review_item(review_item_id)
        if item is None:
            raise CapsuleError(
                NOT_FOUND, f"关联的审核项不存在：{review_item_id}")
        backend.save_rule_candidate_case(RuleCandidateCaseRecord(
            candidate_id=candidate_id, review_item_id=review_item_id,
            case_summary=f"{item.trigger_code}@{item.detected_stage}",
            created_at=now))
        linked.append(review_item_id)
    return success_env(
        request.request_id,
        data={"candidate_id": candidate_id, "status": "draft",
              "linked_review_items": linked,
              "note": "候选不会改变全局规则；规则生效必须先更新需求、SPEC 和验收样例"},
        result_refs=[candidate_id])


def list_rule_candidates(request, backend):
    candidates = backend.list_rule_candidates(
        status=request.status, limit=request.limit, offset=request.offset)
    return success_env(
        request.request_id,
        data={"items": [c.model_dump() for c in candidates],
              "count": len(candidates)})


def show_rule_candidate(request, backend):
    candidate = backend.get_rule_candidate(request.candidate_id)
    if candidate is None:
        raise CapsuleError(NOT_FOUND, f"规则改进候选不存在：{request.candidate_id}")
    cases = backend.list_rule_candidate_cases(request.candidate_id)
    # US5 场景 3：候选需概括来源案例的已有决定，便于评估共同缺口
    case_payloads = []
    for case in cases:
        decisions = backend.list_review_decisions(case.review_item_id)
        case_payloads.append({
            **case.model_dump(),
            "decisions": [d.model_dump(exclude={"review_item_id"})
                          for d in decisions],
        })
    return success_env(
        request.request_id,
        data={"candidate": candidate.model_dump(),
              "cases": case_payloads},
        result_refs=[request.candidate_id])


def close_rule_candidate(request, backend):
    candidate = backend.get_rule_candidate(request.candidate_id)
    if candidate is None:
        raise CapsuleError(NOT_FOUND, f"规则改进候选不存在：{request.candidate_id}")
    if candidate.status == "closed":
        raise CapsuleError(POLICY_BLOCKED, "候选已经关闭")
    candidate.status = "closed"
    candidate.updated_at = ids.now_utc()
    backend.update_rule_candidate(candidate)
    return success_env(
        request.request_id,
        data={"candidate_id": request.candidate_id, "status": "closed",
              "reason": request.reason,
              "note": "关闭候选不修改已经完成的个案决定；相关审核记录继续保留"},
        result_refs=[request.candidate_id])
