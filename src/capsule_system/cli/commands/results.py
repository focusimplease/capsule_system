"""result / evidence 命令组：终态结果与证据可见性。"""

from __future__ import annotations

import typer

from ...contracts.requests import (
    EvidenceProposeDeletionRequest,
    EvidenceShowRequest,
    ResultShowRequest,
)
from ...knowledge import service as knowledge_service
from ..runtime import dispatch, with_backend

result_app = typer.Typer(help="处理结果查询")
evidence_app = typer.Typer(help="衍生证据查询与删除建议")


@result_app.command("show")
def result_show(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """根据 submission_id 读取终态处理结果与全部关联记录。"""

    dispatch(ResultShowRequest, json_text, input_path,
             with_backend(knowledge_service.show_result))


@evidence_app.command("show")
def evidence_show(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """读取证据元数据与权威文字；存在来源 URL 时返回该 URL。"""

    dispatch(EvidenceShowRequest, json_text, input_path,
             with_backend(knowledge_service.show_evidence))


@evidence_app.command("propose-deletion")
def evidence_propose_deletion(
    json_text: str = typer.Option(None, "--json", help="请求 JSON 正文"),
    input_path: str = typer.Option(None, "--input", help="请求 JSON 文件路径"),
) -> None:
    """提出删除建议：生成不可逆操作审核项，等待用户确认。"""

    dispatch(EvidenceProposeDeletionRequest, json_text, input_path,
             with_backend(knowledge_service.propose_evidence_deletion))
