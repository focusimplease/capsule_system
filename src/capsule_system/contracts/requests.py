"""每条 CLI 命令的专属请求模型（Pydantic v2）。

依据 contracts/agent-cli.md：请求通过 --json 或当前任务的临时 JSON 文件传入；
媒体与 PDF 路径只能作为临时输入，持久化序列化时必须排除。

命令清单与 agent-cli.md 一致（13 个契约命令），
另有两个为满足 SPEC FR-040/041 与审核边界情况而补充的入口：
- evidence propose-deletion（不可逆操作审核项的创建入口，见 feishu-storage.md 删除契约）
- rule-candidate close（边界情况：用户决定不处理某个规则缺口时可关闭候选）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InputKind = Literal["text", "pdf", "image", "audio", "video", "web_url"]
DeletionTargetKind = Literal["derived_evidence", "original_evidence_url"]

TEMPORARY_PATH_FIELDS = ("temporary_path",)


class RequestBase(BaseModel):
    request_id: str


class InputSpec(BaseModel):
    kind: InputKind
    text: str | None = None
    temporary_path: str | None = None
    url: str | None = None


class AdminCheckRequest(RequestBase):
    pass


class BootstrapTestSpaceRequest(RequestBase):
    environment: str


class IngestPrepareRequest(RequestBase):
    input: InputSpec
    user_context: str | None = None


class IngestContinueRequest(RequestBase):
    task_ref: str
    action_code: str
    result: dict
    result_schema_version: str = "1.0"


class IngestCommitRequest(RequestBase):
    task_ref: str


class ResultShowRequest(RequestBase):
    submission_id: str


class EvidenceShowRequest(RequestBase):
    evidence_id: str


class EvidenceProposeDeletionRequest(RequestBase):
    target_kind: DeletionTargetKind
    target_id: str


class ReviewListRequest(RequestBase):
    status: str | None = None
    review_type: str | None = None
    trigger_code: str | None = None
    detected_stage: str | None = None
    rule_gap_flag: bool | None = None
    limit: int = 50
    offset: int = 0


class ReviewShowRequest(RequestBase):
    review_item_id: str


class ReviewDecideRequest(RequestBase):
    review_item_id: str
    decision_code: str
    user_note: str | None = None
    target_refs: list[str] = Field(default_factory=list)
    explicit_confirmation: bool = False


class RuleCandidateCreateRequest(RequestBase):
    title: str
    gap_summary: str
    trigger_pattern: dict = Field(default_factory=dict)
    affected_steps: list[str] = Field(default_factory=list)
    review_item_ids: list[str] = Field(default_factory=list)


class RuleCandidateListRequest(RequestBase):
    status: str | None = None
    limit: int = 50
    offset: int = 0


class RuleCandidateShowRequest(RequestBase):
    candidate_id: str


class RuleCandidateCloseRequest(RequestBase):
    candidate_id: str
    reason: str | None = None
