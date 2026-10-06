"""统一结果信封、next_action 协议与稳定错误码。

依据 sdd/001-capsule-main-flow/contracts/agent-cli.md：
- stdout 只输出一个 JSON 结果信封；
- status 只允许 success / review_required / failed；
- 结果信封不得出现 operation_id、checkpoint、resume_cursor、outbox_status、retry_at。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Status = Literal["success", "review_required", "failed"]

# 稳定错误码（contracts/agent-cli.md「稳定错误」）
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
STALE_VERSION = "STALE_VERSION"
POLICY_BLOCKED = "POLICY_BLOCKED"
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
PARTIAL_WRITE = "PARTIAL_WRITE"
INTERNAL_ERROR = "INTERNAL_ERROR"

ERROR_CODES = {
    VALIDATION_ERROR,
    NOT_FOUND,
    STALE_VERSION,
    POLICY_BLOCKED,
    CONFIRMATION_REQUIRED,
    DEPENDENCY_UNAVAILABLE,
    RATE_LIMITED,
    PARTIAL_WRITE,
    INTERNAL_ERROR,
}

# 契约禁止出现在结果信封中的持久化字段（quickstart §5）
FORBIDDEN_ENVELOPE_FIELDS = {
    "operation_id",
    "checkpoint",
    "resume_cursor",
    "outbox_status",
    "retry_at",
}


class ApiError(BaseModel):
    """结构化错误对象。message 必须可向用户解释，不得包含秘密信息。"""

    code: str
    message: str
    retryable_in_current_task: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str | None = None


class NextAction(BaseModel):
    """要求当前 Agent 完成的语义任务。

    context_refs 只指向当前任务可用的文字、存储资源或受控临时输入，
    不是长期业务引用，会话中断后不得用于恢复。
    """

    action_code: str
    context_refs: list[str] = Field(default_factory=list)
    result_schema_version: str = SCHEMA_VERSION
    allowed_next_commands: list[str] = Field(default_factory=list)


class Envelope(BaseModel):
    """CLI 统一结果信封。"""

    schema_version: str = SCHEMA_VERSION
    request_id: str
    status: Status
    data: dict[str, Any] = Field(default_factory=dict)
    result_refs: list[str] = Field(default_factory=list)
    next_action: NextAction | None = None
    error: ApiError | None = None


def success(request_id: str, data: dict[str, Any] | None = None,
            result_refs: list[str] | None = None,
            next_action: NextAction | None = None) -> Envelope:
    return Envelope(request_id=request_id, status="success", data=data or {},
                    result_refs=result_refs or [], next_action=next_action)


def review_required(request_id: str, data: dict[str, Any] | None = None,
                    result_refs: list[str] | None = None) -> Envelope:
    return Envelope(request_id=request_id, status="review_required", data=data or {},
                    result_refs=result_refs or [])


def failed(request_id: str, code: str, message: str,
           retryable: bool = False, details: dict[str, Any] | None = None,
           suggested_action: str | None = None) -> Envelope:
    return Envelope(request_id=request_id, status="failed",
                    error=ApiError(code=code, message=message,
                                   retryable_in_current_task=retryable,
                                   details=details or {},
                                   suggested_action=suggested_action))


class CapsuleError(Exception):
    """领域层异常：携带稳定错误码，由 CLI 层转换为失败信封。"""

    def __init__(self, code: str, message: str, retryable: bool = False,
                 details: dict[str, Any] | None = None,
                 suggested_action: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.suggested_action = suggested_action
