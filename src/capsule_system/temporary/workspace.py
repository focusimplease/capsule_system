"""受控任务工作区：一次任务内多次 CLI 调用的中间上下文。

- 位于受控临时目录，不属于长期业务数据层；
- 任务终态（成功 / 生成审核项 / 失败）时删除；
- 不提供 resume / list 类命令：会话中断后任务引用丢失，即无法恢复；
- 超期目录在下一次任意命令开始时按 TTL 清理（尽力而为）。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from ..contracts.envelope import INTERNAL_ERROR, NOT_FOUND, VALIDATION_ERROR, CapsuleError


class TaskWorkspace:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.tasks_dir = self.base_dir / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    # ---- 生命周期 ----

    def create(self, task_id: str) -> str:
        task_dir = self.tasks_dir / task_id
        if task_dir.exists():
            raise CapsuleError(INTERNAL_ERROR, f"任务目录已存在：{task_id}")
        (task_dir / "ctx").mkdir(parents=True)
        return str(task_dir)

    def validate(self, task_ref: str) -> Path:
        task_dir = Path(task_ref)
        # 必须位于受控基目录内，拒绝任意路径
        try:
            task_dir.resolve().relative_to(self.tasks_dir.resolve())
        except ValueError:
            raise CapsuleError(
                VALIDATION_ERROR, "非法的任务引用：不在受控工作区内") from None
        if not task_dir.is_dir():
            raise CapsuleError(
                NOT_FOUND, "任务不存在或已结束；未完成任务必须重新发起",
                suggested_action="重新提交资料或重新发起操作")
        return task_dir

    def cleanup(self, task_ref: str) -> None:
        shutil.rmtree(task_ref, ignore_errors=True)

    def sweep_stale(self, ttl_hours: int) -> int:
        """删除超过 TTL 的任务目录：会话中断后不留可恢复状态。"""
        removed = 0
        cutoff = time.time() - ttl_hours * 3600
        for entry in self.tasks_dir.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed

    # ---- 状态文件 ----

    def save_state(self, task_ref: str, state: dict) -> None:
        task_dir = self.validate(task_ref)
        tmp = task_dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, task_dir / "state.json")

    def load_state(self, task_ref: str) -> dict:
        task_dir = self.validate(task_ref)
        state_file = task_dir / "state.json"
        if not state_file.exists():
            raise CapsuleError(NOT_FOUND, "任务状态文件缺失，任务必须重新发起")
        return json.loads(state_file.read_text(encoding="utf-8"))

    # ---- 语义上下文文件（Agent 通过 context_refs 读取） ----

    def write_context(self, task_ref: str, name: str, text: str) -> str:
        task_dir = self.validate(task_ref)
        path = task_dir / "ctx" / name
        path.write_text(text, encoding="utf-8")
        return str(path)
