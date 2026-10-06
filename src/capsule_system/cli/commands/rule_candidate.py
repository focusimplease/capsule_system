"""rule-candidate 命令组：规则改进候选管理。"""

from __future__ import annotations

import typer

from ...contracts.requests import (
    RuleCandidateCloseRequest,
    RuleCandidateCreateRequest,
    RuleCandidateListRequest,
    RuleCandidateShowRequest,
)
from ...review import rules
from ..runtime import dispatch, with_backend

app = typer.Typer(help="规则改进候选")


@app.command("create")
def create(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """从审核案例创建规则改进候选（不改变全局规则）。"""

    dispatch(RuleCandidateCreateRequest, json_text, input_path,
             with_backend(rules.create_rule_candidate))


@app.command("list")
def list_candidates(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """按状态筛选规则改进候选。"""

    dispatch(RuleCandidateListRequest, json_text, input_path,
             with_backend(rules.list_rule_candidates))


@app.command("show")
def show(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """读取候选详情与关联的来源案例。"""

    dispatch(RuleCandidateShowRequest, json_text, input_path,
             with_backend(rules.show_rule_candidate))


@app.command("close")
def close(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """关闭候选：不修改已完成的个案决定，审核记录保留。"""

    dispatch(RuleCandidateCloseRequest, json_text, input_path,
             with_backend(rules.close_rule_candidate))
