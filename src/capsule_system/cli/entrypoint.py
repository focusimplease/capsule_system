"""capsule CLI 入口：组装全部命令组。

命令清单与 sdd/001-capsule-main-flow/contracts/agent-cli.md 一致
（13 个契约命令），另有两个补充入口（evidence propose-deletion、
rule-candidate close），用于满足 SPEC FR-040/041 与审核边界情况。
"""

from __future__ import annotations

import typer

from .commands import admin, ingest, results, review, rule_candidate

app = typer.Typer(
    name="capsule",
    help="胶囊系统命令行接口：一次调用一项边界明确的操作",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(admin.app, name="admin")
app.add_typer(ingest.app, name="ingest")
app.add_typer(results.result_app, name="result")
app.add_typer(results.evidence_app, name="evidence")
app.add_typer(review.app, name="review")
app.add_typer(rule_candidate.app, name="rule-candidate")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
