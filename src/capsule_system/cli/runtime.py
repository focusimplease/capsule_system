"""CLI 运行时：后端装配、请求解析与统一错误转换。

依据 contracts/agent-cli.md：
- 请求通过 --json 或当前任务的临时 JSON 文件（--input）传入；
- stdout 只输出一个 JSON 结果信封；
- Pydantic 校验失败和领域异常都转换为失败信封，不让 traceback
  泄漏到 stdout。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ..config import Config, get_config
from ..contracts.envelope import (
    INTERNAL_ERROR,
    VALIDATION_ERROR,
    CapsuleError,
    Envelope,
    failed as failed_env,
)
from ..storage.feishu.adapter import FeishuBackend, LarkCliRunner
from ..storage.memory_backend import InMemoryBackend
from ..temporary.workspace import TaskWorkspace


def build_backend(config: Config):
    if config.storage_backend == "memory":
        return InMemoryBackend(config.memory_store_path or None)
    runner = LarkCliRunner(config.lark_cli)
    return FeishuBackend(
        runner,
        wiki_space_id=config.feishu_wiki_space_id,
        base_app_token=config.feishu_base_app_token,
        drive_root_folder_id=config.feishu_drive_root_folder_id,
        user_id=config.feishu_user_id)


def build_workspace(config: Config) -> TaskWorkspace:
    return TaskWorkspace(config.workspace_dir)


def emit(envelope: Envelope) -> None:
    print(envelope.model_dump_json())


def _load_payload(json_text: str | None, input_path: str | None) -> dict | None:
    if json_text and input_path:
        emit(failed_env("unknown", VALIDATION_ERROR,
                        "--json 与 --input 只能提供一个"))
        return None
    if not json_text and not input_path:
        emit(failed_env("unknown", VALIDATION_ERROR,
                        "必须通过 --json 或 --input 提供请求正文"))
        return None
    try:
        raw = (json_text if json_text is not None
               else Path(input_path).read_text(encoding="utf-8"))
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("请求正文必须是 JSON 对象")
        return payload
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        emit(failed_env("unknown", VALIDATION_ERROR, f"请求正文无法解析：{exc}"))
        return None


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(p) for p in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg')}")
    return "；".join(parts) or str(exc)


def dispatch(request_model, json_text: str | None, input_path: str | None,
             handler) -> None:
    """解析请求 → 调用处理器 → 打印一个结果信封。

    handler 签名：handler(request) -> Envelope。
    """
    payload = _load_payload(json_text, input_path)
    if payload is None:
        return
    request_id = payload.get("request_id") if isinstance(
        payload.get("request_id"), str) else "unknown"
    try:
        request = request_model(**payload)
    except ValidationError as exc:
        emit(failed_env(request_id, VALIDATION_ERROR,
                        f"请求不符合命令 Schema：{_format_validation_error(exc)}"))
        return
    try:
        envelope = handler(request)
    except CapsuleError as exc:
        envelope = failed_env(request.request_id, exc.code, exc.message,
                              retryable=exc.retryable, details=exc.details,
                              suggested_action=exc.suggested_action)
    except Exception as exc:  # noqa: BLE001 - 未知异常统一转为失败信封
        envelope = failed_env(request.request_id, INTERNAL_ERROR,
                              f"内部错误：{exc.__class__.__name__}: {exc}")
    emit(envelope)


def with_backend(service_fn):
    """把 (request, backend) 服务函数适配为 handler(request)。"""

    def handler(request):
        return service_fn(request, build_backend(get_config()))

    return handler
