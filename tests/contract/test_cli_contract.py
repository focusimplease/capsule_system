"""CLI 契约测试：15 个命令面、统一信封与错误处理。

依据 sdd/001-capsule-main-flow/contracts/agent-cli.md：
- 每条命令只输出一个 JSON 结果信封；
- status 只允许 success / review_required / failed；
- 结果信封（含嵌套 data）不得出现 operation_id、checkpoint、
  resume_cursor、outbox_status、retry_at；
- Pydantic 校验失败与领域异常都转换为失败信封。
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from capsule_system.cli.entrypoint import app
from capsule_system.contracts.envelope import ERROR_CODES, FORBIDDEN_ENVELOPE_FIELDS

from tests.helpers import default_plan, default_understanding

EVIDENCE_TEXT = "契约测试证据：胶囊系统统一信封与九个稳定错误码。"

runner = CliRunner()


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("CAPSULE_MEMORY_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("CAPSULE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    return tmp_path


def invoke(*args) -> dict:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    # stdout 必须恰好是一个 JSON 信封
    envelope = json.loads(result.output)
    assert isinstance(envelope, dict)
    return envelope


def assert_envelope_invariants(envelope: dict, request_id: str) -> None:
    assert envelope["schema_version"] == "1.0"
    assert envelope["request_id"] == request_id
    assert envelope["status"] in {"success", "review_required", "failed"}
    if envelope["status"] == "failed":
        assert envelope["error"]["code"] in ERROR_CODES
        assert envelope["error"]["message"]
    else:
        assert envelope["error"] is None


def scan_forbidden_fields(value, path: str = "$", found: list | None = None) -> list:
    """递归扫描信封（含嵌套 data）中契约禁止的持久化字段。"""
    if found is None:
        found = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in FORBIDDEN_ENVELOPE_FIELDS:
                found.append(f"{path}.{key}")
            scan_forbidden_fields(sub, f"{path}.{key}", found)
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            scan_forbidden_fields(sub, f"{path}[{index}]", found)
    return found


def test_fifteen_command_surface_and_envelope_contract(cli_env):
    """按顺序驱动全部 15 个命令，校验信封不变量与禁止字段。"""
    envelopes: list[tuple[str, dict]] = []

    def record(envelope: dict) -> dict:
        assert_envelope_invariants(envelope, envelope.get("request_id", "unknown"))
        envelopes.append(("n/a", envelope))
        return envelope

    # 1. admin check
    env = record(invoke("admin", "check", "--json",
                        json.dumps({"request_id": "r-check"})))
    assert env["status"] == "success"
    assert env["data"]["backend"] == "memory"

    # 2. admin bootstrap-test-space
    env = record(invoke("admin", "bootstrap-test-space", "--json",
                        json.dumps({"request_id": "r-boot",
                                    "environment": "acceptance"})))
    assert env["status"] == "success"
    assert env["data"]["tables"] == 11
    assert env["data"]["attachment_fields"] == 0
    assert env["data"]["branches"] == ["主题", "概念", "证据库"]

    # 3. ingest prepare（text 输入）
    env = record(invoke("ingest", "prepare", "--json", json.dumps({
        "request_id": "r-prep",
        "input": {"kind": "text", "text": EVIDENCE_TEXT},
        "user_context": "契约测试上下文"})))
    assert env["status"] == "success"
    assert env["next_action"]["action_code"] == "compare_knowledge"
    assert env["next_action"]["allowed_next_commands"] == ["capsule ingest continue"]
    task_ref = env["data"]["task_ref"]

    # 4. ingest continue（compare_knowledge）
    env = record(invoke("ingest", "continue", "--json", json.dumps({
        "request_id": "r-cont1", "task_ref": task_ref,
        "action_code": "compare_knowledge",
        "result": default_understanding(EVIDENCE_TEXT)})))
    assert env["status"] == "success"
    assert env["next_action"]["action_code"] == "organize_pages"

    # 5. ingest continue（organize_pages）
    env = record(invoke("ingest", "continue", "--json", json.dumps({
        "request_id": "r-cont2", "task_ref": task_ref,
        "action_code": "organize_pages",
        "result": default_plan(EVIDENCE_TEXT)})))
    assert env["status"] == "success"
    assert env["data"]["ready_to_commit"] is True

    # 6. ingest commit
    env = record(invoke("ingest", "commit", "--json", json.dumps({
        "request_id": "r-commit", "task_ref": task_ref})))
    assert env["status"] == "success"
    assert env["data"]["outcome"] == "completed"
    submission_id = env["data"]["submission_id"]
    evidence_id = env["data"]["evidence_id"]
    assert env["data"]["affected_pages"]
    assert submission_id in env["result_refs"]

    # 7. result show
    env = record(invoke("result", "show", "--json", json.dumps({
        "request_id": "r-result", "submission_id": submission_id})))
    assert env["status"] == "success"
    assert env["data"]["submission"]["status"] == "completed"
    assert len(env["data"]["evidence"]) == 1
    assert env["data"]["latest_processing_record"]["outcome"] == "completed"

    # 8. evidence show
    env = record(invoke("evidence", "show", "--json", json.dumps({
        "request_id": "r-ev", "evidence_id": evidence_id})))
    assert env["status"] == "success"
    assert env["data"]["content"] == EVIDENCE_TEXT
    assert env["data"]["contributions"]

    # 9. evidence propose-deletion（补充命令）
    env = record(invoke("evidence", "propose-deletion", "--json", json.dumps({
        "request_id": "r-del", "target_kind": "derived_evidence",
        "target_id": evidence_id})))
    assert env["status"] == "review_required"
    review_item_id = env["data"]["review_item_id"]

    # 10. review list
    env = record(invoke("review", "list", "--json", json.dumps({
        "request_id": "r-list", "status": "open"})))
    assert env["status"] == "success"
    assert env["data"]["count"] == 1
    assert env["data"]["items"][0]["review_type"] == "irreversible_operation"

    # 11. review show
    env = record(invoke("review", "show", "--json", json.dumps({
        "request_id": "r-show", "review_item_id": review_item_id})))
    assert env["status"] == "success"
    codes = {d["code"] for d in
             env["data"]["review_item"]["allowed_decisions"]}
    assert codes == {"confirm_delete", "reject_delete"}

    # 12. review decide（reject_delete：可逆，无需确认）
    env = record(invoke("review", "decide", "--json", json.dumps({
        "request_id": "r-decide", "review_item_id": review_item_id,
        "decision_code": "reject_delete"})))
    assert env["status"] == "success"
    assert env["data"]["execution_result"]["effect"] == "deletion_rejected"

    # 13. rule-candidate create
    env = record(invoke("rule-candidate", "create", "--json", json.dumps({
        "request_id": "r-rc", "title": "扫描件处理规则缺口",
        "gap_summary": "多份扫描 PDF 触发同类中断，需要规则化处理",
        "review_item_ids": [review_item_id]})))
    assert env["status"] == "success"
    candidate_id = env["data"]["candidate_id"]
    assert env["data"]["linked_review_items"] == [review_item_id]

    # 14. rule-candidate list
    env = record(invoke("rule-candidate", "list", "--json", json.dumps({
        "request_id": "r-rclist", "status": "draft"})))
    assert env["status"] == "success"
    assert env["data"]["count"] == 1

    # 15. rule-candidate show
    env = record(invoke("rule-candidate", "show", "--json", json.dumps({
        "request_id": "r-rcshow", "candidate_id": candidate_id})))
    assert env["status"] == "success"
    assert len(env["data"]["cases"]) == 1

    # 16. rule-candidate close（补充命令）
    env = record(invoke("rule-candidate", "close", "--json", json.dumps({
        "request_id": "r-rcclose", "candidate_id": candidate_id})))
    assert env["status"] == "success"
    assert env["data"]["status"] == "closed"

    # 全部信封：递归扫描契约禁止的持久化字段
    for _, envelope in envelopes:
        violations = scan_forbidden_fields(envelope)
        assert violations == [], f"信封出现禁止字段：{violations}"


def test_input_file_variant(cli_env, tmp_path):
    payload_file = tmp_path / "request.json"
    payload_file.write_text(json.dumps({
        "request_id": "r-file",
        "input": {"kind": "text", "text": EVIDENCE_TEXT}}), encoding="utf-8")
    env = invoke("ingest", "prepare", "--input", str(payload_file))
    assert_envelope_invariants(env, "r-file")
    assert env["status"] == "success"


def test_json_and_input_mutually_exclusive(cli_env, tmp_path):
    payload_file = tmp_path / "request.json"
    payload_file.write_text("{}", encoding="utf-8")
    env = invoke("ingest", "prepare", "--json", "{}", "--input", str(payload_file))
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"


def test_missing_payload_rejected(cli_env):
    env = invoke("admin", "check")
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_json_rejected(cli_env):
    env = invoke("admin", "check", "--json", "{not json")
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"


def test_non_object_json_rejected(cli_env):
    env = invoke("admin", "check", "--json", "[1, 2]")
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"


def test_request_schema_violation_becomes_failed_envelope(cli_env):
    # input 字段缺失：Pydantic 校验失败 → 失败信封，而不是 traceback
    env = invoke("ingest", "prepare", "--json",
                 json.dumps({"request_id": "r-bad"}))
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"
    assert "请求不符合命令 Schema" in env["error"]["message"]


def test_unknown_task_ref_not_found(cli_env):
    env = invoke("ingest", "continue", "--json", json.dumps({
        "request_id": "r-ghost", "task_ref": "/nonexistent/task",
        "action_code": "compare_knowledge", "result": {}}))
    assert env["status"] == "failed"
    assert env["error"]["code"] in {"VALIDATION_ERROR", "NOT_FOUND"}


def test_commit_before_semantic_tasks_complete(cli_env):
    env = invoke("ingest", "prepare", "--json", json.dumps({
        "request_id": "r-prep", "input": {"kind": "text", "text": EVIDENCE_TEXT}}))
    task_ref = env["data"]["task_ref"]
    env = invoke("ingest", "commit", "--json", json.dumps({
        "request_id": "r-early", "task_ref": task_ref}))
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"


def test_action_code_mismatch_rejected(cli_env):
    env = invoke("ingest", "prepare", "--json", json.dumps({
        "request_id": "r-prep", "input": {"kind": "text", "text": EVIDENCE_TEXT}}))
    task_ref = env["data"]["task_ref"]
    env = invoke("ingest", "continue", "--json", json.dumps({
        "request_id": "r-wrong", "task_ref": task_ref,
        "action_code": "organize_pages", "result": default_plan(EVIDENCE_TEXT)}))
    assert env["status"] == "failed"
    assert env["error"]["code"] == "VALIDATION_ERROR"
    assert "动作代码不匹配" in env["error"]["message"]


def test_result_show_unknown_submission(cli_env):
    env = invoke("result", "show", "--json", json.dumps({
        "request_id": "r-404", "submission_id": "ghost"}))
    assert env["status"] == "failed"
    assert env["error"]["code"] == "NOT_FOUND"


def test_review_decide_unknown_item(cli_env):
    env = invoke("review", "decide", "--json", json.dumps({
        "request_id": "r-404", "review_item_id": "ghost",
        "decision_code": "terminate"}))
    assert env["status"] == "failed"
    assert env["error"]["code"] == "NOT_FOUND"


def test_workspace_removed_after_commit(cli_env):
    import subprocess
    import sys
    # 通过独立子进程验证任务工作区在 commit 后被清理（无残留可恢复状态）
    env = invoke("ingest", "prepare", "--json", json.dumps({
        "request_id": "r-prep", "input": {"kind": "text", "text": EVIDENCE_TEXT}}))
    task_ref = env["data"]["task_ref"]
    invoke("ingest", "continue", "--json", json.dumps({
        "request_id": "r-c1", "task_ref": task_ref,
        "action_code": "compare_knowledge",
        "result": default_understanding(EVIDENCE_TEXT)}))
    invoke("ingest", "continue", "--json", json.dumps({
        "request_id": "r-c2", "task_ref": task_ref,
        "action_code": "organize_pages",
        "result": default_plan(EVIDENCE_TEXT)}))
    invoke("ingest", "commit", "--json", json.dumps({
        "request_id": "r-commit", "task_ref": task_ref}))
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; from pathlib import Path; "
         "sys.exit(0 if Path(sys.argv[1]).exists() else 1)", task_ref],
        capture_output=True)
    assert probe.returncode != 0, "任务目录应在 commit 后删除"
    _ = cli_env  # fixture 仅提供环境
