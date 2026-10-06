"""admin 命令组：环境检查与测试空间初始化。"""

from __future__ import annotations

import typer

from ...config import get_config
from ...contracts.envelope import success as success_env
from ...contracts.requests import AdminCheckRequest, BootstrapTestSpaceRequest
from ..runtime import build_backend, dispatch

app = typer.Typer(help="环境检查与测试空间管理")


@app.command("check")
def check(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """只读检查依赖、飞书身份与目标资源，不创建任何资源。"""

    def handler(request: AdminCheckRequest):
        backend = build_backend(get_config())
        return success_env(request.request_id, data=backend.check())

    dispatch(AdminCheckRequest, json_text, input_path, handler)


@app.command("bootstrap-test-space")
def bootstrap_test_space(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """只用于开发测试空间：创建约定 Wiki 分支与 Base 表，拒绝附件字段。"""

    def handler(request: BootstrapTestSpaceRequest):
        backend = build_backend(get_config())
        created = backend.bootstrap(request.environment)
        return success_env(request.request_id, data=created)

    dispatch(BootstrapTestSpaceRequest, json_text, input_path, handler)
