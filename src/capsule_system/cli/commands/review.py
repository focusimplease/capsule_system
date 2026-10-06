"""review 命令组：审核队列查询与用户决定。"""

from __future__ import annotations

import typer

from ...contracts.requests import (
    ReviewDecideRequest,
    ReviewListRequest,
    ReviewShowRequest,
)
from ...review import service as review_service
from ..runtime import dispatch, with_backend

app = typer.Typer(help="审核队列与用户决定")


@app.command("list")
def review_list(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """按状态、类型、触发码、步骤和规则缺口标记筛选审核项。"""

    dispatch(ReviewListRequest, json_text, input_path,
             with_backend(review_service.list_reviews))


@app.command("show")
def review_show(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """返回审核项详情与作出决定所需的全部信息。"""

    dispatch(ReviewShowRequest, json_text, input_path,
             with_backend(review_service.show_review))


@app.command("decide")
def review_decide(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """执行用户决定：校验决定代码、目标与确认后追加决定记录。"""

    dispatch(ReviewDecideRequest, json_text, input_path,
             with_backend(review_service.decide_review))
