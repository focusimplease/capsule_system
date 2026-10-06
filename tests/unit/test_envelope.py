"""统一结果信封单元测试：Schema、稳定错误码与禁止字段。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from capsule_system.contracts.envelope import (
    ERROR_CODES,
    FORBIDDEN_ENVELOPE_FIELDS,
    ApiError,
    CapsuleError,
    Envelope,
    NextAction,
    failed,
    review_required,
    success,
)


def test_status_only_three_values():
    with pytest.raises(ValidationError):
        Envelope(request_id="r", status="running")
    assert Envelope(request_id="r", status="success").status == "success"
    assert Envelope(request_id="r", status="review_required").status == "review_required"
    assert Envelope(request_id="r", status="failed").status == "failed"


def test_error_codes_are_the_nine_stable_codes():
    assert ERROR_CODES == {
        "VALIDATION_ERROR", "NOT_FOUND", "STALE_VERSION", "POLICY_BLOCKED",
        "CONFIRMATION_REQUIRED", "DEPENDENCY_UNAVAILABLE", "RATE_LIMITED",
        "PARTIAL_WRITE", "INTERNAL_ERROR",
    }


def test_forbidden_fields_match_contract():
    assert FORBIDDEN_ENVELOPE_FIELDS == {
        "operation_id", "checkpoint", "resume_cursor", "outbox_status", "retry_at",
    }


def test_envelope_top_level_schema_excludes_forbidden_fields():
    fields = set(Envelope.model_fields)
    assert not (fields & FORBIDDEN_ENVELOPE_FIELDS), (
        "信封顶层不得出现契约禁止的持久化字段")


def test_success_helper_carries_next_action_and_refs():
    envelope = success("r", data={"a": 1}, result_refs=["ref-1"],
                       next_action=NextAction(
                           action_code="compare_knowledge",
                           context_refs=["ctx"], allowed_next_commands=["capsule ingest continue"]))
    assert envelope.status == "success"
    assert envelope.next_action.action_code == "compare_knowledge"
    assert envelope.result_refs == ["ref-1"]
    assert envelope.error is None


def test_review_required_helper_has_no_next_action():
    envelope = review_required("r", data={"x": 1})
    assert envelope.status == "review_required"
    assert envelope.next_action is None


def test_failed_helper_populates_structured_error():
    envelope = failed("r", "POLICY_BLOCKED", "拒绝", retryable=False,
                      details={"limit": 10}, suggested_action="拆分")
    assert envelope.status == "failed"
    assert envelope.error.code == "POLICY_BLOCKED"
    assert envelope.error.retryable_in_current_task is False
    assert envelope.error.details == {"limit": 10}
    assert envelope.error.suggested_action == "拆分"


def test_capsule_error_attributes():
    exc = CapsuleError("RATE_LIMITED", "限流", retryable=True,
                       details={"wait": 5}, suggested_action="稍后重试")
    assert exc.code == "RATE_LIMITED"
    assert exc.retryable is True
    assert exc.details == {"wait": 5}
    assert exc.suggested_action == "稍后重试"


def test_api_error_message_is_user_explainable_type():
    error = ApiError(code="NOT_FOUND", message="页面不存在：p1")
    assert error.retryable_in_current_task is False
    assert error.details == {}
