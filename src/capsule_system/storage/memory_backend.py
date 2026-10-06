"""内存后端：测试与模拟端到端验收使用。

- 进程内字典实现，可选持久化到 JSON 文件以支持多次 CLI 调用；
- 这是测试替身，不是生产持久化方式（生产使用飞书适配器）；
- 只保存文本和来源 URL，不保存任何附件字节。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..contracts.envelope import NOT_FOUND, STALE_VERSION, CapsuleError
from ..domain.entities import (
    DerivedEvidence,
    EvidenceContributionRecord,
    KnowledgePageRecord,
    OriginalEvidence,
    ProcessingRecord,
    ReviewDecisionRecord,
    ReviewItemRecord,
    RuleCandidateCaseRecord,
    RuleCandidateRecord,
    Submission,
    UnderstandingResultRecord,
)

_LOCK = threading.Lock()


class InMemoryBackend:
    def __init__(self, persist_path: str | None = None):
        self.persist_path = Path(persist_path) if persist_path else None
        self.submissions: dict[str, dict] = {}
        self.original_evidence: dict[str, dict] = {}
        self.derived_evidence: dict[str, dict] = {}
        self.evidence_contents: dict[str, str] = {}
        self.pages: dict[str, dict] = {}
        self.page_contents: dict[str, str] = {}
        self.contributions: dict[str, dict] = {}
        self.processing_records: dict[str, dict] = {}
        self.understandings: dict[str, dict] = {}
        self.review_items: dict[str, dict] = {}
        self.review_decisions: dict[str, dict] = {}
        self.rule_candidates: dict[str, dict] = {}
        self.rule_candidate_cases: dict[str, dict] = {}
        self._revision_counter = 0
        if self.persist_path and self.persist_path.exists():
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            for key, value in data.items():
                setattr(self, key, value)
            # 恢复页面修订计数器：跨进程加载后新修订号不能回退或重复
            for record in self.pages.values():
                revision = str(record.get("revision_id", ""))
                if revision.startswith("rev-"):
                    try:
                        self._revision_counter = max(
                            self._revision_counter, int(revision[4:]))
                    except ValueError:
                        continue

    # ---- 持久化 ----
    def _persist(self) -> None:
        if not self.persist_path:
            return
        data = {
            "submissions": self.submissions,
            "original_evidence": self.original_evidence,
            "derived_evidence": self.derived_evidence,
            "evidence_contents": self.evidence_contents,
            "pages": self.pages,
            "page_contents": self.page_contents,
            "contributions": self.contributions,
            "processing_records": self.processing_records,
            "understandings": self.understandings,
            "review_items": self.review_items,
            "review_decisions": self.review_decisions,
            "rule_candidates": self.rule_candidates,
            "rule_candidate_cases": self.rule_candidate_cases,
        }
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.persist_path.write_text(json.dumps(data, ensure_ascii=False),
                                     encoding="utf-8")

    # ---- 检查与初始化 ----
    def check(self) -> dict:
        return {"backend": "memory", "ok": True,
                "detail": "内存测试后端，不检查真实飞书资源"}

    def bootstrap(self, environment: str) -> dict:
        # 11 张表与飞书适配器 TABLES 一致（含 original_evidence，偏离
        # feishu-storage.md 的 10 张清单：FR-004 要求 no_evidence 结果
        # 也要保存来源 URL，需要独立于 evidence_index 的存储位置）
        return {"backend": "memory", "environment": environment,
                "branches": ["主题", "概念", "证据库"],
                "tables": 11, "attachment_fields": 0}

    # ---- 提交与入口去重 ----
    def find_submission_by_fingerprint(self, fingerprint: str) -> Submission | None:
        for record in self.submissions.values():
            if record.get("content_fingerprint") == fingerprint:
                return Submission(**record)
        return None

    def save_submission(self, submission: Submission) -> None:
        with _LOCK:
            self.submissions[submission.submission_id] = submission.model_dump()
            self._persist()

    def get_submission(self, submission_id: str) -> Submission | None:
        record = self.submissions.get(submission_id)
        return Submission(**record) if record else None

    # ---- 原始证据（仅来源 URL） ----
    def save_original_evidence(self, original: OriginalEvidence) -> None:
        with _LOCK:
            self.original_evidence[original.original_evidence_id] = original.model_dump()
            self._persist()

    def get_original_evidence(self, original_evidence_id: str) -> OriginalEvidence | None:
        record = self.original_evidence.get(original_evidence_id)
        return OriginalEvidence(**record) if record else None

    def get_original_evidence_by_submission(self, submission_id: str) -> OriginalEvidence | None:
        for record in self.original_evidence.values():
            if record.get("submission_id") == submission_id:
                return OriginalEvidence(**record)
        return None

    def delete_original_evidence(self, original_evidence_id: str) -> None:
        if original_evidence_id not in self.original_evidence:
            raise CapsuleError(NOT_FOUND, f"原始证据不存在：{original_evidence_id}")
        with _LOCK:
            del self.original_evidence[original_evidence_id]
            self._persist()

    # ---- 衍生证据 ----
    def save_derived_evidence(self, evidence: DerivedEvidence) -> None:
        with _LOCK:
            self.derived_evidence[evidence.evidence_id] = evidence.model_dump()
            self._persist()

    def get_derived_evidence(self, evidence_id: str) -> DerivedEvidence | None:
        record = self.derived_evidence.get(evidence_id)
        return DerivedEvidence(**record) if record else None

    def list_evidence_by_submission(self, submission_id: str) -> list[DerivedEvidence]:
        return [DerivedEvidence(**r) for r in self.derived_evidence.values()
                if r.get("submission_id") == submission_id]

    def save_evidence_content(self, evidence_id: str, content: str) -> None:
        with _LOCK:
            self.evidence_contents[evidence_id] = content
            self._persist()

    def read_evidence_content(self, evidence_id: str) -> str:
        content = self.evidence_contents.get(evidence_id)
        if content is None:
            raise CapsuleError(NOT_FOUND, f"证据内容不存在：{evidence_id}")
        return content

    def mark_evidence_deleted(self, evidence_id: str) -> None:
        record = self.derived_evidence.get(evidence_id)
        if not record:
            raise CapsuleError(NOT_FOUND, f"证据不存在：{evidence_id}")
        record["status"] = "deleted"
        self.evidence_contents.pop(evidence_id, None)
        self._persist()

    # ---- 知识页 ----
    def save_page(self, page: KnowledgePageRecord) -> None:
        with _LOCK:
            self.pages[page.page_id] = page.model_dump()
            self.page_contents[page.page_id] = ""
            self._persist()

    def get_page(self, page_id: str) -> KnowledgePageRecord | None:
        record = self.pages.get(page_id)
        return KnowledgePageRecord(**record) if record else None

    def list_pages(self, page_type=None, title_contains=None, status=None,
                   limit=50, offset=0) -> list[KnowledgePageRecord]:
        records = []
        for record in self.pages.values():
            if page_type and record.get("page_type") != page_type:
                continue
            if title_contains and title_contains not in record.get("title", ""):
                continue
            if status and record.get("status") != status:
                continue
            records.append(KnowledgePageRecord(**record))
        records.sort(key=lambda p: p.title)
        return records[offset:offset + limit]

    def read_page_content(self, page_id: str) -> str:
        if page_id not in self.pages:
            raise CapsuleError(NOT_FOUND, f"知识页不存在：{page_id}")
        return self.page_contents.get(page_id, "")

    def update_page_content(self, page_id: str, revision_id: str, content: str,
                            content_hash: str) -> KnowledgePageRecord:
        record = self.pages.get(page_id)
        if not record:
            raise CapsuleError(NOT_FOUND, f"知识页不存在：{page_id}")
        if record["revision_id"] != revision_id:
            raise CapsuleError(
                STALE_VERSION,
                f"页面「{record['title']}」版本已变化（读取 {revision_id}，"
                f"当前 {record['revision_id']}）")
        with _LOCK:
            self._revision_counter += 1
            record["revision_id"] = f"rev-{self._revision_counter}"
            record["content_hash"] = content_hash
            self.page_contents[page_id] = content
            self._persist()
        return KnowledgePageRecord(**record)

    def set_page_status(self, page_id: str, status: str) -> None:
        record = self.pages.get(page_id)
        if not record:
            raise CapsuleError(NOT_FOUND, f"知识页不存在：{page_id}")
        record["status"] = status
        self._persist()

    # ---- 追溯关系 ----
    def save_contribution(self, contribution: EvidenceContributionRecord) -> None:
        with _LOCK:
            self.contributions[contribution.contribution_id] = contribution.model_dump()
            self._persist()

    def list_contributions(self, page_id=None, evidence_id=None) -> list[EvidenceContributionRecord]:
        result = []
        for record in self.contributions.values():
            if page_id and record.get("page_id") != page_id:
                continue
            if evidence_id and record.get("evidence_id") != evidence_id:
                continue
            result.append(EvidenceContributionRecord(**record))
        return result

    # ---- 处理记录与理解结果 ----
    def save_processing_record(self, record: ProcessingRecord) -> None:
        with _LOCK:
            self.processing_records[record.processing_record_id] = record.model_dump()
            self._persist()

    def list_processing_records(self, submission_id: str) -> list[ProcessingRecord]:
        return [ProcessingRecord(**r) for r in self.processing_records.values()
                if r.get("submission_id") == submission_id]

    def save_understanding(self, understanding: UnderstandingResultRecord) -> None:
        with _LOCK:
            self.understandings[understanding.understanding_id] = understanding.model_dump()
            self._persist()

    def list_understandings(self, submission_id: str) -> list[UnderstandingResultRecord]:
        return [UnderstandingResultRecord(**u) for u in self.understandings.values()
                if u.get("submission_id") == submission_id]

    # ---- 审核 ----
    def save_review_item(self, item: ReviewItemRecord) -> None:
        with _LOCK:
            self.review_items[item.review_item_id] = item.model_dump()
            self._persist()

    def get_review_item(self, review_item_id: str) -> ReviewItemRecord | None:
        record = self.review_items.get(review_item_id)
        return ReviewItemRecord(**record) if record else None

    def list_review_items(self, submission_id=None, status=None, review_type=None,
                          trigger_code=None, detected_stage=None, rule_gap_flag=None,
                          limit=50, offset=0) -> list[ReviewItemRecord]:
        records = []
        for record in self.review_items.values():
            if submission_id and record.get("submission_id") != submission_id:
                continue
            if status and record.get("status") != status:
                continue
            if review_type and record.get("review_type") != review_type:
                continue
            if trigger_code and record.get("trigger_code") != trigger_code:
                continue
            if detected_stage and record.get("detected_stage") != detected_stage:
                continue
            if rule_gap_flag is not None and record.get("rule_gap_flag") != rule_gap_flag:
                continue
            records.append(ReviewItemRecord(**record))
        records.sort(key=lambda r: r.created_at)
        return records[offset:offset + limit]

    def update_review_item(self, item: ReviewItemRecord) -> None:
        with _LOCK:
            self.review_items[item.review_item_id] = item.model_dump()
            self._persist()

    def append_review_decision(self, decision: ReviewDecisionRecord) -> None:
        with _LOCK:
            self.review_decisions[decision.review_decision_id] = decision.model_dump()
            self._persist()

    def list_review_decisions(self, review_item_id: str) -> list[ReviewDecisionRecord]:
        return [ReviewDecisionRecord(**d) for d in self.review_decisions.values()
                if d.get("review_item_id") == review_item_id]

    # ---- 规则改进候选 ----
    def save_rule_candidate(self, candidate: RuleCandidateRecord) -> None:
        with _LOCK:
            self.rule_candidates[candidate.candidate_id] = candidate.model_dump()
            self._persist()

    def get_rule_candidate(self, candidate_id: str) -> RuleCandidateRecord | None:
        record = self.rule_candidates.get(candidate_id)
        return RuleCandidateRecord(**record) if record else None

    def list_rule_candidates(self, status=None, limit=50, offset=0) -> list[RuleCandidateRecord]:
        records = []
        for record in self.rule_candidates.values():
            if status and record.get("status") != status:
                continue
            records.append(RuleCandidateRecord(**record))
        records.sort(key=lambda c: c.created_at)
        return records[offset:offset + limit]

    def update_rule_candidate(self, candidate: RuleCandidateRecord) -> None:
        with _LOCK:
            self.rule_candidates[candidate.candidate_id] = candidate.model_dump()
            self._persist()

    def save_rule_candidate_case(self, case: RuleCandidateCaseRecord) -> None:
        with _LOCK:
            self.rule_candidate_cases[f"{case.candidate_id}:{case.review_item_id}"] = case.model_dump()
            self._persist()

    def list_rule_candidate_cases(self, candidate_id: str) -> list[RuleCandidateCaseRecord]:
        return [RuleCandidateCaseRecord(**c) for c in self.rule_candidate_cases.values()
                if c.get("candidate_id") == candidate_id]
