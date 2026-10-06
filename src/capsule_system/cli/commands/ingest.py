"""ingest 命令组：prepare → continue（next_action 循环）→ commit。"""

from __future__ import annotations

import typer

from ...config import get_config
from ...contracts.requests import (
    IngestCommitRequest,
    IngestContinueRequest,
    IngestPrepareRequest,
)
from ...domain import task_engine
from ..runtime import build_backend, build_workspace, dispatch

app = typer.Typer(help="资料处理主流程")


@app.command("prepare")
def prepare(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """读取本次输入并完成当前可确定执行的提取、校验和查询。"""

    def handler(request: IngestPrepareRequest):
        config = get_config()
        return task_engine.ingest_prepare(
            request, build_backend(config), build_workspace(config), config)

    dispatch(IngestPrepareRequest, json_text, input_path, handler)


@app.command("continue")
def continue_(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """提交 Agent 针对上一项 next_action 生成的结构化结果。"""

    def handler(request: IngestContinueRequest):
        config = get_config()
        return task_engine.ingest_continue(
            request, build_backend(config), build_workspace(config), config)

    dispatch(IngestContinueRequest, json_text, input_path, handler)


@app.command("commit")
def commit(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """集中提交已通过全部校验的最终写入集合。"""

    def handler(request: IngestCommitRequest):
        config = get_config()
        return task_engine.ingest_commit(
            request, build_backend(config), build_workspace(config), config)

    dispatch(IngestCommitRequest, json_text, input_path, handler)
