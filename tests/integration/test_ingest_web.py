"""web_url 输入集成测试：mock 网络抽取，覆盖图片候选分流与空正文审核。"""

from __future__ import annotations

from pathlib import Path

import pytest

from capsule_system.contracts.requests import InputSpec, IngestPrepareRequest
from capsule_system.domain import task_engine
from capsule_system.extractors.base import ExtractionResult

from tests.helpers import (
    complete_ingest,
    continue_task,
    prepare,
    rid,
)

URL = "https://example.com/article"
BODY = ("网页正文：胶囊系统的网页输入只保存正文文字与来源 URL。"
        "正文中的表格和图形必须转换为可编辑文字。")


def stub_web(monkeypatch, text: str = BODY,
             image_candidates: list[dict] | None = None,
             omissions: list[str] | None = None) -> None:
    """替换任务引擎的网页抽取（不发起真实网络请求）。"""

    def fake_extract_web(url, max_bytes, timeout, max_chars):
        return ExtractionResult(
            text=text, complete=bool(text),
            image_candidates=image_candidates or [],
            omissions=omissions if omissions is not None else (
                [] if image_candidates else
                ["网页中没有检测到可能携带独立信息的图片候选"]),
            quality={"kind": "web_url", "chars": len(text)})

    monkeypatch.setattr(task_engine, "extract_web", fake_extract_web)


def web_prepare(backend, workspace, config, url=URL):
    request = IngestPrepareRequest(
        request_id=rid(), input=InputSpec(kind="web_url", url=url))
    return task_engine.ingest_prepare(request, backend, workspace, config)


def test_web_without_candidates_goes_straight_to_compare(
        backend, workspace, config, monkeypatch):
    stub_web(monkeypatch)
    env = web_prepare(backend, workspace, config)
    assert env.status == "success"
    assert env.next_action.action_code == "compare_knowledge"
    task_ref = env.data["task_ref"]
    env = complete_ingest(backend, workspace, config, task_ref, BODY)
    assert env.status == "success"
    assert env.data["outcome"] == "completed"
    original = backend.get_original_evidence_by_submission(
        env.data["submission_id"])
    assert original.url == URL


def test_web_with_candidates_routes_to_understand_web_images(
        backend, workspace, config, monkeypatch):
    stub_web(monkeypatch, image_candidates=[
        {"key": "figure-0", "description": "架构图：主流程",
         "src": "https://example.com/arch.png"},
        {"key": "img-1", "description": "数据表示意", "src": "https://example.com/data.png"},
    ])
    env = web_prepare(backend, workspace, config)
    assert env.status == "success"
    assert env.next_action.action_code == "understand_web_images"
    task_ref = env.data["task_ref"]
    assert len(env.data["image_candidates"]) == 2
    # 上下文文件：正文 + 图片候选说明
    ctx = Path(task_ref) / "ctx"
    assert "网页正文" in (ctx / "evidence.txt").read_text(encoding="utf-8")
    assert "figure-0" in (ctx / "web-images.txt").read_text(encoding="utf-8")

    env = continue_task(backend, workspace, config, task_ref,
                        "understand_web_images", {
                            "images": [
                                {"image_key": "figure-0", "ignored": False,
                                 "text": "架构图文字化：输入 → 提取 → 理解 → 组织。"},
                                {"image_key": "img-1", "ignored": True,
                                 "reason": "装饰性数据图，正文已覆盖"},
                            ],
                            "core_incomplete": False,
                        })
    assert env.status == "success"
    assert env.next_action.action_code == "compare_knowledge"

    merged = (BODY + "\n\n（网页图片转换内容）\n"
              "架构图文字化：输入 → 提取 → 理解 → 组织。")
    env = complete_ingest(backend, workspace, config, task_ref, merged)
    assert env.status == "success"
    evidence_text = backend.read_evidence_content(env.data["evidence_id"])
    assert "架构图文字化" in evidence_text
    # 忽略的图片记录为省略，不保存原图（FR-013/046）
    records = backend.list_processing_records(env.data["submission_id"])
    assert any("忽略网页图片 img-1" in o for o in records[0].omissions)
    assert "example.com/arch.png" not in evidence_text


def test_web_images_unknown_key_rejected(backend, workspace, config,
                                         monkeypatch):
    stub_web(monkeypatch, image_candidates=[
        {"key": "img-1", "description": "d", "src": "https://x/i.png"}])
    env = web_prepare(backend, workspace, config)
    task_ref = env.data["task_ref"]
    env = continue_task(backend, workspace, config, task_ref,
                        "understand_web_images", {
                            "images": [{"image_key": "ghost", "ignored": True,
                                        "reason": "r"}]})
    assert env.status == "failed"
    assert env.error.code == "NOT_FOUND"


def test_web_images_not_ignored_requires_text(backend, workspace, config,
                                              monkeypatch):
    stub_web(monkeypatch, image_candidates=[
        {"key": "img-1", "description": "d", "src": "https://x/i.png"}])
    env = web_prepare(backend, workspace, config)
    task_ref = env.data["task_ref"]
    env = continue_task(backend, workspace, config, task_ref,
                        "understand_web_images", {
                            "images": [{"image_key": "img-1"}]})
    assert env.status == "failed"
    assert env.error.code == "VALIDATION_ERROR"


def test_web_images_core_incomplete_enters_review(backend, workspace, config,
                                                  monkeypatch):
    stub_web(monkeypatch, image_candidates=[
        {"key": "figure-0", "description": "核心结果图",
         "src": "https://example.com/core.png"}])
    env = web_prepare(backend, workspace, config)
    task_ref = env.data["task_ref"]
    env = continue_task(backend, workspace, config, task_ref,
                        "understand_web_images", {
                            "images": [{"image_key": "figure-0",
                                        "ignored": False,
                                        "text": "只能识别局部标题",
                                        "core_incomplete": True}],
                            "core_incomplete": True,
                            "reason": "核心数据以图片呈现，无法可靠转换",
                        })
    assert env.status == "success"
    assert env.data["planned_outcome"] == "needs_review"

    from tests.helpers import commit
    env = commit(backend, workspace, config, task_ref)
    assert env.status == "review_required"
    item = backend.get_review_item(env.result_refs[0])
    assert item.trigger_code == "WEB_IMAGE_CORE_INCOMPLETE"
    assert item.suggested_next_task.get("requires_new_input") is True
    assert "web_image_understanding" in item.attempted_actions


def test_empty_web_extraction_enters_review_immediately(
        backend, workspace, config, monkeypatch):
    stub_web(monkeypatch, text="", omissions=[
        "网页正文抽取结果为空（可能仅 JavaScript 渲染或需要登录），未补写内容"])
    env = web_prepare(backend, workspace, config)
    assert env.status == "review_required"
    item = backend.get_review_item(env.result_refs[0])
    assert item.trigger_code == "EMPTY_WEB_EXTRACTION"
    assert item.attempted_actions == ["web_body_extraction"]
    # 有 URL：后续决定可以建议同 URL 重试
    assert item.suggested_next_task.get("requires_new_input") is not True


def test_web_url_dedup_ignores_tracking_params(backend, workspace, config,
                                               monkeypatch):
    stub_web(monkeypatch)
    env = web_prepare(backend, workspace, config, URL)
    complete_ingest(backend, workspace, config, env.data["task_ref"], BODY)
    assert len(backend.submissions) == 1

    env = web_prepare(backend, workspace, config,
                      "https://example.com/article?utm_source=test#top")
    assert env.status == "success"
    assert env.data["entry_dedup"]["duplicate"] is True
    assert len(backend.submissions) == 1
