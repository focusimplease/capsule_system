"""持久化业务实体（对应 data-model.md 的多维表格逻辑表）。

规则：
- 二进制输入路径只存在于当前任务的请求、内存或受控临时目录中，
  绝不进入任何实体字段；
- 系统只持久保存文本内容和来源 URL，不保存任何非文本附件；
- 用户决定采用追加记录（ReviewDecision），当前状态只是投影。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

InputKind = Literal["text", "pdf", "image", "audio", "video", "web_url"]
SubmissionStatus = Literal["completed", "no_evidence", "needs_review", "terminated"]
UrlAvailability = Literal["unknown", "reachable", "unreachable", "changed"]
EvidenceStatus = Literal["active", "superseded", "deletion_pending", "deleted"]
PageStatus = Literal["active", "conflicted", "archived"]
ReviewType = Literal["processing_interruption", "knowledge_conflict", "irreversible_operation"]
ReviewStatus = Literal["open", "resolved", "cancelled"]
CandidateStatus = Literal["draft", "accepted_for_spec_update", "closed"]


class Submission(BaseModel):
    submission_id: str
    input_kind: InputKind
    source_url: str | None = None
    user_context: str | None = None
    content_fingerprint: str | None = None
    status: SubmissionStatus
    created_at: str
    updated_at: str


class OriginalEvidence(BaseModel):
    """资料存在来源 URL 时保存的原始证据。

    原始媒体文件不属于系统保存的原始证据（FR-005/006/007）；
    没有 URL 的资料不创建本记录。
    """

    original_evidence_id: str
    submission_id: str
    url: str
    availability: UrlAvailability = "unknown"
    last_checked_at: str | None = None
    created_at: str
    updated_at: str


class DerivedEvidence(BaseModel):
    evidence_id: str
    submission_id: str
    original_evidence_id: str | None = None
    title: str
    content_hash: str
    preview: str = ""
    docx_token: str | None = None
    wiki_node_token: str | None = None
    status: EvidenceStatus = "active"
    supersedes_evidence_id: str | None = None
    created_at: str


class ProcessingRecord(BaseModel):
    processing_record_id: str
    submission_id: str
    outcome: SubmissionStatus
    reason_code: str | None = None
    reason_detail: str | None = None
    omissions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    affected_page_refs: list[str] = Field(default_factory=list)
    created_at: str


class UnderstandingResultRecord(BaseModel):
    understanding_id: str
    submission_id: str
    evidence_id: str
    schema_version: str
    topics: list[dict] = Field(default_factory=list)
    key_claims: list[dict] = Field(default_factory=list)
    duplicate_parts: list[str] = Field(default_factory=list)
    novel_parts: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    target_pages: list[str] = Field(default_factory=list)
    conservative_notes: list[str] = Field(default_factory=list)
    validation_status: Literal["valid", "needs_review", "rejected"] = "valid"
    created_at: str


class KnowledgePageRecord(BaseModel):
    page_id: str
    page_type: Literal["topic", "concept"]
    title: str
    docx_token: str | None = None
    wiki_node_token: str | None = None
    revision_id: str
    content_hash: str
    status: PageStatus = "active"
    created_at: str
    updated_at: str


class EvidenceContributionRecord(BaseModel):
    contribution_id: str
    evidence_id: str
    page_id: str
    understanding_id: str
    contribution_kind: Literal[
        "created", "added", "corrected", "example", "boundary", "definition"
    ]
    claim_anchors: list[dict] = Field(default_factory=list)
    page_revision_before: str | None = None
    page_revision_after: str | None = None
    summary: str = ""
    created_at: str


class ReviewItemRecord(BaseModel):
    review_item_id: str
    submission_id: str | None = None
    review_type: ReviewType
    status: ReviewStatus = "open"
    trigger_code: str
    detected_stage: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    affected_page_refs: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)
    attempted_actions: list[str] = Field(default_factory=list)
    allowed_decisions: list[dict] = Field(default_factory=list)
    recommendation: dict[str, Any] = Field(default_factory=dict)
    suggested_next_task: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    rule_gap_flag: bool = False
    created_at: str
    resolved_at: str | None = None


class ReviewDecisionRecord(BaseModel):
    review_decision_id: str
    review_item_id: str
    decision_code: str
    review_item_version: int
    user_note: str | None = None
    target_refs: list[str] = Field(default_factory=list)
    explicit_confirmation: bool = False
    execution_status: Literal["succeeded", "failed", "cancelled"]
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    executed_at: str | None = None


class RuleCandidateRecord(BaseModel):
    candidate_id: str
    title: str
    gap_summary: str
    trigger_pattern: dict = Field(default_factory=dict)
    affected_steps: list[str] = Field(default_factory=list)
    status: CandidateStatus = "draft"
    created_at: str
    updated_at: str


class RuleCandidateCaseRecord(BaseModel):
    candidate_id: str
    review_item_id: str
    case_summary: str = ""
    created_at: str
