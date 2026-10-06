"""端到端验收测试：按 SPEC 用户故事 1–5 驱动完整 CLI 主流程。

与契约、集成测试的区别：所有操作通过 `capsule` CLI 表面完成（与
Agent 基座调用方式一致），按 sdd/001-capsule-main-flow/spec.md 的
验收场景与成功标准（SC-001~SC-015）组织断言。存储使用内存后端的
持久化模式，模拟跨命令的连续会话；不发起真实网络请求。

成功标准覆盖映射（测试内以 SC 注释标注）：
- SC-001/003/004/010/011 → 用户故事 1 与 4
- SC-002/005/006/012     → 用户故事 2
- SC-007/008/009/013     → 用户故事 3
- SC-014/015             → 用户故事 5
"""

from __future__ import annotations

import json
import uuid

import pytest
from typer.testing import CliRunner

from capsule_system.cli.entrypoint import app

from tests.helpers import (
    CONCEPT_TEMPLATE,
    anchor_of,
    fake_whisper_factory,
    make_pdf,
    make_png,
    make_scanned_pdf,
    make_wav,
)

runner = CliRunner()

US1_TEXT = "验收证据一：胶囊系统需要把新资料融合进主题页，而不是保存孤立摘要。"
US1_MORE = "验收补充：新资料只补充了一个有效边界说明，其余内容与已有知识重复。"
US1_DUP = "验收重复：这份资料与已有知识完全重复，没有新增价值。"
AUDIO_TEXT = "音频验收证据：本地确定性转写只保留文字形式的衍生证据。"


def rid() -> str:
    return f"acc-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("CAPSULE_MEMORY_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("CAPSULE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    return tmp_path


def invoke(*args) -> dict:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] in {"success", "review_required", "failed"}, envelope
    return envelope


def req(payload: dict) -> list[str]:
    return ["--json", json.dumps({"request_id": rid(), **payload},
                                 ensure_ascii=False)]


def ingest_text(text: str, *, user_context: str | None = None) -> dict:
    payload = {"input": {"kind": "text", "text": text}}
    if user_context:
        payload["user_context"] = user_context
    return invoke("ingest", "prepare", *req(payload))


def understanding(text: str, *, novel: bool = True, existing_page_id: str | None = None,
                  duplicates: list[str] | None = None,
                  conflicts: list[dict] | None = None) -> dict:
    anchor = anchor_of(text)
    return {
        "topics": [{"name": "验收主题", "existing_page_id": existing_page_id,
                    "rationale": "验收归属判断"}],
        "key_claims": [{"claim": "验收关键判断", "evidence_anchor": anchor}],
        "duplicate_parts": duplicates or [],
        "novel_parts": ([{"kind": "explanation", "text": "验收新增内容",
                          "evidence_anchor": anchor}] if novel else []),
        "conflicts": conflicts or [],
        "target_pages": [],
        "conservative_notes": [],
    }


def plan_create(text: str, title: str = "验收概念页") -> dict:
    anchor = anchor_of(text)
    return {
        "creates": [{"page_type": "concept", "title": title,
                     "content": CONCEPT_TEMPLATE.format(title=title),
                     "claim_anchors": [{"claim": "页面重要判断",
                                        "evidence_anchor": anchor}]}],
        "updates": [], "pending_updates": [], "not_adopted_reason": None,
    }


def plan_update(page_id: str, revision_id: str, text: str) -> dict:
    anchor = anchor_of(text)
    return {
        "creates": [],
        "updates": [{"page_id": page_id, "revision_id": revision_id,
                     "content": (f"# 验收更新页\n\n既有正文保留。\n\n"
                                 "## 定义\n\n更新后定义。\n\n## 边界\n\n更新后边界。\n\n"
                                 "## 相近概念\n\n相近说明。\n\n## 典型用法\n\n用法说明。\n\n"
                                 "## 验收补充段落\n\n只融合新增部分。"),
                     "claim_anchors": [{"claim": "更新判断",
                                        "evidence_anchor": anchor}],
                     "change_summary": "验收：融合新增边界"}],
        "pending_updates": [], "not_adopted_reason": None,
    }


def plan_not_adopted(reason: str) -> dict:
    return {"creates": [], "updates": [], "pending_updates": [],
            "not_adopted_reason": reason}


def continue_task(task_ref: str, action_code: str, result: dict) -> dict:
    return invoke("ingest", "continue", *req(
        {"task_ref": task_ref, "action_code": action_code, "result": result}))


def commit_task(task_ref: str) -> dict:
    return invoke("ingest", "commit", *req({"task_ref": task_ref}))


def complete_text_flow(text: str, *, plan: dict | None = None,
                       understanding_result: dict | None = None,
                       user_context: str | None = None) -> dict:
    """prepare → compare → organize → commit，返回 commit 信封。"""
    env = ingest_text(text, user_context=user_context)
    assert env["status"] == "success", env
    task_ref = env["data"]["task_ref"]
    env = continue_task(task_ref, "compare_knowledge",
                        understanding_result or understanding(text))
    assert env["status"] == "success", env
    env = continue_task(task_ref, "organize_pages", plan or plan_create(text))
    assert env["status"] == "success", env
    return commit_task(task_ref)


# ---------------------------------------------------------------------------
# 用户故事 1（P1）：让资料进入长期维护的 Wiki
# ---------------------------------------------------------------------------

class TestUserStory1WikiEvolution:
    def test_new_viewpoint_creates_page_and_records(self, cli_env):
        """场景 1/5，SC-003：新观点融合进概念页，处理不停留在证据记录。"""
        env = complete_text_flow(US1_TEXT)
        assert env["status"] == "success"
        assert env["data"]["outcome"] == "completed"
        submission_id = env["data"]["submission_id"]
        assert env["data"]["affected_pages"], "必须记录受影响页面"

        # SC-004：页面重要判断可追溯到衍生证据
        env = invoke("result", "show", *req({"submission_id": submission_id}))
        assert env["data"]["submission"]["status"] == "completed"
        assert env["data"]["latest_processing_record"]["outcome"] == "completed"
        page_id = env["data"]["affected_pages"][0]["page_id"]
        evidence_id = env["data"]["evidence"][0]["evidence_id"]

        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        assert US1_TEXT in env["data"]["content"]
        contributions = env["data"]["contributions"]
        assert any(c["page_id"] == page_id for c in contributions), \
            "SC-003：已采纳内容必须关联页面新建或更新"

    def test_supplementary_content_merges_only_new_parts(self, cli_env):
        """场景 2，SC-011：只融合新增部分，不因重复放弃整份资料。"""
        first = complete_text_flow(US1_TEXT)
        page_id = first["data"]["affected_pages"][0]
        env = invoke("result", "show", *req(
            {"submission_id": first["data"]["submission_id"]}))
        revision_id = env["data"]["affected_pages"][0]["revision_id"]

        env = complete_text_flow(
            US1_MORE,
            understanding_result=understanding(
                US1_MORE, existing_page_id=page_id,
                duplicates=["既有正文已覆盖的重复表达"]),
            plan=plan_update(page_id, revision_id, US1_MORE))
        assert env["status"] == "success"
        assert env["data"]["outcome"] == "completed"
        assert page_id in env["data"]["affected_pages"]

    def test_fully_duplicate_material_not_rewritten(self, cli_env):
        """场景 3，SC-011：完全重复的表达不再写入知识页，保留不采纳理由。"""
        first = complete_text_flow(US1_TEXT)
        page_count = len(first["data"]["affected_pages"])

        env = complete_text_flow(
            US1_DUP,
            understanding_result=understanding(US1_DUP, novel=False,
                                               duplicates=["全部内容重复"]),
            plan=plan_not_adopted("内容与已有知识完全重复，无新增价值"))
        assert env["status"] == "success"
        assert env["data"]["outcome"] == "completed"
        assert env["data"]["affected_pages"] == []
        assert page_count >= 1, "前置页面存在，重复资料未新增页面写入"

        env = invoke("result", "show", *req(
            {"submission_id": env["data"]["submission_id"]}))
        record = env["data"]["latest_processing_record"]
        assert record["reason_code"] == "NOT_ADOPTED"
        assert "无新增价值" in record["reason_detail"]

    def test_one_material_affects_topic_and_concept_pages(self, cli_env):
        """边界情况：同一资料同时影响主题页和概念页时，两页都保留证据对应关系。"""
        anchor = anchor_of(US1_TEXT)
        env = ingest_text(US1_TEXT)
        task_ref = env["data"]["task_ref"]
        continue_task(task_ref, "compare_knowledge", understanding(US1_TEXT))
        env = continue_task(task_ref, "organize_pages", {
            "creates": [
                {"page_type": "topic", "title": "验收主题页",
                 "content": CONCEPT_TEMPLATE.format(title="验收主题页"),
                 "claim_anchors": [{"claim": "主题判断",
                                    "evidence_anchor": anchor}]},
                {"page_type": "concept", "title": "验收概念页",
                 "content": CONCEPT_TEMPLATE.format(title="验收概念页"),
                 "claim_anchors": [{"claim": "概念判断",
                                    "evidence_anchor": anchor}]}],
            "updates": [], "pending_updates": [], "not_adopted_reason": None})
        assert env["status"] == "success"
        env = commit_task(task_ref)
        assert env["status"] == "success"
        assert len(env["data"]["affected_pages"]) == 2

        # 两类页面都保留与衍生证据的对应关系（FR-028）
        evidence_id = env["data"]["evidence_id"]
        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        contributed_pages = {c["page_id"] for c in env["data"]["contributions"]}
        assert len(contributed_pages) == 2, "主题页与概念页都必须有贡献记录"

    def test_conflict_keeps_page_and_generates_review(self, cli_env):
        """场景 4：无法自动决定的冲突不覆盖旧判断，生成知识冲突审核项。"""
        first = complete_text_flow(US1_TEXT)
        page_id = first["data"]["affected_pages"][0]
        env = invoke("result", "show", *req(
            {"submission_id": first["data"]["submission_id"]}))
        page = env["data"]["affected_pages"][0]
        original_show = invoke("evidence", "show", *req(
            {"evidence_id": env["data"]["evidence"][0]["evidence_id"]}))

        conflicting = US1_TEXT + "冲突：新资料挑战既有判断，需要用户决定。"
        anchor = anchor_of(conflicting)
        env = ingest_text(conflicting)
        task_ref = env["data"]["task_ref"]
        env = continue_task(task_ref, "compare_knowledge", understanding(
            conflicting, existing_page_id=page_id,
            conflicts=[{"page_id": page_id, "existing_claim": "既有判断",
                        "new_claim": "新判断", "evidence_anchor": anchor,
                        "resolution": "needs_user"}]))
        assert env["status"] == "success"
        env = continue_task(task_ref, "organize_pages", {
            "creates": [], "updates": [],
            "pending_updates": [{
                "page_id": page_id, "revision_id": page["revision_id"],
                "content": "# 冲突更新\n\n新判断内容。\n\n## 定义\n\n定义。\n\n"
                           "## 边界\n\n边界。\n\n## 相近概念\n\n相近。\n\n"
                           "## 典型用法\n\n用法。",
                "claim_anchors": [{"claim": "冲突判断",
                                   "evidence_anchor": anchor}]}],
            "not_adopted_reason": None})
        assert env["status"] == "success"
        env = commit_task(task_ref)
        assert env["status"] == "review_required"
        review_item_id = env["result_refs"][0]

        # 审核期间页面保持可核对，未被静默覆盖
        env = invoke("review", "show", *req({"review_item_id": review_item_id}))
        item = env["data"]["review_item"]
        assert item["review_type"] == "knowledge_conflict"
        assert item["recommendation"]["conflicts"]
        affected_ids = [p["page_id"] for p in env["data"]["affected_pages"]]
        assert page_id in affected_ids


# ---------------------------------------------------------------------------
# 用户故事 2（P1）：获得纯文本且可追溯的证据
# ---------------------------------------------------------------------------

class TestUserStory2Evidence:
    def test_pdf_text_layer_extraction_without_file(self, cli_env, tmp_path):
        """场景 2，SC-005：提取文字层、忽略图片、不保存 PDF 文件。"""
        path = make_pdf(tmp_path / "doc.pdf", "PDF acceptance: text layer only.")
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "pdf", "temporary_path": str(path)}}))
        assert env["status"] == "success"
        task_ref = env["data"]["task_ref"]
        env = complete_via_cli(task_ref, "PDF acceptance: text layer only.")
        assert env["status"] == "success"
        # 非文本附件持久化数量为 0：信封与结果不包含任何附件引用
        assert "attachment" not in json.dumps(env)
        assert not path.with_suffix(".saved").exists()

    def test_image_outcomes_usable_no_evidence_review(self, cli_env, tmp_path):
        """场景 3/4/5，SC-002：图片三分支——可用、无知识、局部信息。"""
        image_text = "图片验收证据：图表标题为季度增长，数值为百分之二十。"

        # 分支 1：usable → 衍生证据
        path = make_png(tmp_path / "photo-1.png", color=(120, 40, 200))
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "image", "temporary_path": str(path)},
             "user_context": "图表：季度增长百分之二十"}))
        assert env["status"] == "success"
        assert env["next_action"]["action_code"] == "understand_image"
        task_ref = env["data"]["task_ref"]
        env = continue_task(task_ref, "understand_image", {
            "outcome": "usable", "evidence_text": image_text,
            "omissions": [], "uncertainties": [], "reason": ""})
        assert env["status"] == "success"
        env = complete_via_cli(task_ref, image_text)
        assert env["status"] == "success"
        assert env["data"]["evidence_id"]

        # 分支 2：no_evidence → 不生成衍生证据
        path = make_png(tmp_path / "photo-2.png", color=(40, 120, 200))
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "image", "temporary_path": str(path)}}))
        task_ref = env["data"]["task_ref"]
        env = continue_task(task_ref, "understand_image", {
            "outcome": "no_evidence", "evidence_text": "", "omissions": [],
            "uncertainties": [], "reason": "图片没有可形成知识的主题或事实"})
        assert env["status"] == "success"
        env = commit_task(task_ref)
        assert env["status"] == "success"
        assert env["data"]["outcome"] == "no_evidence"
        assert not env["data"].get("evidence_id")

        # 分支 3：review → 处理中断审核项，不补写内容
        path = make_png(tmp_path / "photo-3.png", color=(200, 120, 40))
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "image", "temporary_path": str(path)}}))
        task_ref = env["data"]["task_ref"]
        env = continue_task(task_ref, "understand_image", {
            "outcome": "review", "evidence_text": "", "omissions": [],
            "uncertainties": ["只能识别局部区域，无法确认完整含义"],
            "reason": "局部信息不足以确认完整含义"})
        assert env["status"] == "success"
        env = commit_task(task_ref)
        assert env["status"] == "review_required"
        env = invoke("review", "show", *req(
            {"review_item_id": env["result_refs"][0]}))
        assert env["data"]["review_item"]["trigger_code"] == "IMAGE_UNRELIABLE"

    def test_audio_transcription_text_only(self, cli_env, tmp_path, monkeypatch):
        """场景 6/7，SC-005：可靠转写只保存文字证据，不保存媒体文件。"""
        monkeypatch.setattr("faster_whisper.WhisperModel", fake_whisper_factory())
        path = make_wav(tmp_path / "voice.wav")
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "audio", "temporary_path": str(path)},
             "user_context": "会议记录音频"}))
        assert env["status"] == "success"
        task_ref = env["data"]["task_ref"]
        transcript = env["data"]["extraction_summary"]
        assert "测试语音" in transcript
        env = complete_via_cli(task_ref, transcript)
        assert env["status"] == "success"
        env = invoke("evidence", "show", *req(
            {"evidence_id": env["data"]["evidence_id"]}))
        assert "测试语音" in env["data"]["content"]
        assert "attachment" not in json.dumps(env)

    def test_web_images_and_url_original_evidence(self, cli_env, monkeypatch):
        """场景 8/9/10，SC-006/012：装饰图忽略、信息图转文字、URL 原始证据。"""
        from capsule_system.domain import task_engine
        from capsule_system.extractors.base import ExtractionResult
        url = "https://example.com/acceptance"
        body = "网页验收正文：核心论述不依赖图片即可理解。"

        def fake_extract_web(u, max_bytes, timeout, max_chars):
            return ExtractionResult(
                text=body, complete=True,
                image_candidates=[
                    {"key": "deco-0", "description": "装饰性分隔图",
                     "src": "https://example.com/deco.png"},
                    {"key": "fig-1", "description": "数据表示意",
                     "src": "https://example.com/data.png"}],
                omissions=[],
                quality={"kind": "web_url", "chars": len(body)})

        monkeypatch.setattr(task_engine, "extract_web", fake_extract_web)
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "web_url", "url": url}}))
        assert env["status"] == "success"
        assert env["next_action"]["action_code"] == "understand_web_images"
        task_ref = env["data"]["task_ref"]
        env = continue_task(task_ref, "understand_web_images", {
            "images": [
                {"image_key": "deco-0", "ignored": True,
                 "reason": "装饰性图片，与正文信息重复", "text": ""},
                {"image_key": "fig-1", "ignored": False, "reason": "",
                 "text": "| 指标 | 数值 |\n| --- | --- |\n| 增长 | 20% |"}],
            "core_incomplete": False, "reason": ""})
        assert env["status"] == "success"
        merged = body + "\n\n（网页图片转换内容）\n| 指标 | 数值 |\n| --- | --- |\n| 增长 | 20% |"
        env = complete_via_cli(task_ref, merged)
        assert env["status"] == "success"

        # 场景 10：只把 URL 保存为原始证据，不保存图片 URL
        env = invoke("result", "show", *req(
            {"submission_id": env["data"]["submission_id"]}))
        assert env["data"]["submission"]["source_url"] == url
        evidence_id = env["data"]["evidence"][0]["evidence_id"]
        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        assert env["data"]["source_url"] == url
        assert "deco.png" not in json.dumps(env), "网页图片 URL 不得另存"
        assert "data.png" not in json.dumps(env)

    def test_no_url_no_original_evidence(self, cli_env):
        """场景 11：无来源 URL 时不建立原始证据。"""
        env = complete_text_flow(US1_TEXT)
        env = invoke("result", "show", *req(
            {"submission_id": env["data"]["submission_id"]}))
        assert env["data"]["submission"]["source_url"] is None
        evidence_id = env["data"]["evidence"][0]["evidence_id"]
        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        assert env["data"]["source_url"] is None


def complete_via_cli(task_ref: str, evidence_text: str) -> dict:
    """从 compare_knowledge 阶段推进到 commit（CLI 版）。

    理解结果与页面方案的证据锚点都取自真实证据文本。
    """
    env = continue_task(task_ref, "compare_knowledge",
                        understanding(evidence_text))
    assert env["status"] == "success", env
    env = continue_task(task_ref, "organize_pages", plan_create(evidence_text))
    assert env["status"] == "success", env
    return commit_task(task_ref)


# ---------------------------------------------------------------------------
# 用户故事 3（P1）：在审核队列完成人工判断
# ---------------------------------------------------------------------------

class TestUserStory3ReviewQueue:
    _scan_seq = 0

    def make_scanned_pdf_item(self, tmp_path, *, url=None):
        """生成唯一变体的扫描 PDF 中断项，避免入口去重拦截。"""
        self._scan_seq += 1
        path = make_scanned_pdf(tmp_path / f"scan-{self._scan_seq}.pdf",
                                variant=self._scan_seq)
        payload = {"input": {"kind": "pdf", "temporary_path": str(path)}}
        if url:
            payload["input"]["url"] = url
        env = invoke("ingest", "prepare", *req(payload))
        assert env["status"] == "review_required", env
        return env

    def test_interruption_item_shows_decision_info(self, cli_env, tmp_path):
        """场景 1，SC-007/014：中断审核项完整展示决定所需信息。"""
        env = self.make_scanned_pdf_item(
            tmp_path, url="https://example.com/scan-acceptance.pdf")
        review_item_id = env["result_refs"][0]
        submission_id = env["data"]["submission_id"]

        env = invoke("review", "show", *req({"review_item_id": review_item_id}))
        item = env["data"]["review_item"]
        assert item["review_type"] == "processing_interruption"
        assert item["detected_stage"], "必须记录发现问题的业务步骤"
        assert item["reason"], "必须记录原因"
        assert item["attempted_actions"], "必须记录已尝试处理"
        assert item["rule_gap_flag"] is True
        assert {d["code"] for d in item["allowed_decisions"]} == {
            "supplement_context", "retry", "specify_topic", "resubmit",
            "terminate"}
        # FR-033：可用来源 URL 必须展示
        assert env["data"]["source_url"] == "https://example.com/scan-acceptance.pdf"

        # SC-001：提交资料具有明确处理状态
        env = invoke("result", "show", *req({"submission_id": submission_id}))
        assert env["data"]["submission"]["status"] == "needs_review"

    def test_deletion_requires_explicit_confirmation(self, cli_env):
        """场景 3/8，SC-009：未经确认不得永久删除证据。"""
        first = complete_text_flow(US1_TEXT)
        evidence_id = first["data"]["evidence_id"]

        env = invoke("evidence", "propose-deletion", *req(
            {"target_kind": "derived_evidence", "target_id": evidence_id}))
        assert env["status"] == "review_required"
        review_item_id = env["data"]["review_item_id"]

        # 未携带明确确认 → 拒绝执行
        env = invoke("review", "decide", *req(
            {"review_item_id": review_item_id, "decision_code": "confirm_delete",
             "target_refs": [evidence_id], "explicit_confirmation": False}))
        assert env["status"] == "failed"
        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        assert env["status"] == "success", "未确认前证据必须保留"

        # 拒绝删除：对象保留，拒绝不视为不采纳
        env = invoke("review", "decide", *req(
            {"review_item_id": review_item_id, "decision_code": "reject_delete"}))
        assert env["status"] == "success"
        assert env["data"]["execution_result"]["effect"] == "deletion_rejected"
        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        assert env["status"] == "success"

    def test_interruption_decisions_and_new_task(self, cli_env, tmp_path):
        """场景 4/5/6，SC-008：决定后启动新任务建议，不恢复执行现场。"""
        # 带 URL 的中断项：supplement_context 给出新任务输入
        env = self.make_scanned_pdf_item(
            tmp_path, url="https://example.com/scan.pdf")
        review_item_id = env["result_refs"][0]
        env = invoke("review", "decide", *req(
            {"review_item_id": review_item_id, "decision_code": "supplement_context",
             "user_note": "这是扫描版合同，请按文本重新提交"}))
        assert env["status"] == "success"
        suggested = env["data"]["suggested_next_task"]
        assert suggested["requires_new_input"] is False
        assert suggested["input"] == {"kind": "web_url",
                                      "url": "https://example.com/scan.pdf"}

        # 无 URL 的中断项：明确要求重新提交，不声称保留附件
        env = self.make_scanned_pdf_item(tmp_path)
        review_item_id = env["result_refs"][0]
        env = invoke("review", "decide", *req(
            {"review_item_id": review_item_id, "decision_code": "retry"}))
        assert env["status"] == "success"
        suggested = env["data"]["suggested_next_task"]
        assert suggested["requires_new_input"] is True
        assert "未保存原始附件" in suggested["input_needed"]

        # 终止（另一条中断项）：停止后续步骤，保留决定与结果
        env = self.make_scanned_pdf_item(tmp_path)
        review_item_id = env["result_refs"][0]
        submission_id = env["data"]["submission_id"]
        env = invoke("review", "decide", *req(
            {"review_item_id": review_item_id, "decision_code": "terminate",
             "user_note": "不再处理这份资料"}))
        assert env["status"] == "success"
        assert env["data"]["execution_result"]["effect"] == "terminated"
        env = invoke("result", "show", *req({"submission_id": submission_id}))
        assert env["data"]["submission"]["status"] == "terminated"
        assert env["data"]["latest_processing_record"][
            "reason_code"] == "TERMINATED", "必须保留终止结果记录"

    def test_conservative_uncertainty_continues_without_review(self, cli_env):
        """场景 9，SC-013：轻微不确定记录原因后继续，不生成审核项。"""
        env = ingest_text("保守处理验收：轻微不确定不改变核心结果。")
        task_ref = env["data"]["task_ref"]
        result = understanding("保守处理验收：轻微不确定不改变核心结果。")
        result["conservative_notes"] = ["排版细节不确定，采用保守默认值"]
        env = continue_task(task_ref, "compare_knowledge", result)
        assert env["status"] == "success"
        env = continue_task(task_ref, "organize_pages",
                            plan_create("保守处理验收：轻微不确定不改变核心结果。"))
        env = commit_task(task_ref)
        assert env["status"] == "success", "保守结果不应进入审核队列"

    def test_conflict_decisions_execute(self, cli_env):
        """场景 7：知识冲突四类决定按用户选择更新状态。"""
        first = complete_text_flow(US1_TEXT)
        page_id = first["data"]["affected_pages"][0]
        env = invoke("result", "show", *req(
            {"submission_id": first["data"]["submission_id"]}))
        page = env["data"]["affected_pages"][0]

        conflicting = US1_TEXT + "冲突：保留双方并限定条件。"
        anchor = anchor_of(conflicting)
        env = ingest_text(conflicting)
        task_ref = env["data"]["task_ref"]
        continue_task(task_ref, "compare_knowledge", understanding(
            conflicting, existing_page_id=page_id,
            conflicts=[{"page_id": page_id, "existing_claim": "既有判断",
                        "new_claim": "新判断", "evidence_anchor": anchor,
                        "resolution": "needs_user"}]))
        continue_task(task_ref, "organize_pages", {
            "creates": [], "updates": [],
            "pending_updates": [{
                "page_id": page_id, "revision_id": page["revision_id"],
                "content": "# 冲突更新\n\n新判断内容。\n\n## 定义\n\n定义。\n\n"
                           "## 边界\n\n边界。\n\n## 相近概念\n\n相近。\n\n"
                           "## 典型用法\n\n用法。",
                "claim_anchors": [{"claim": "冲突判断",
                                   "evidence_anchor": anchor}]}],
            "not_adopted_reason": None})
        env = commit_task(task_ref)
        review_item_id = env["result_refs"][0]

        # keep_both_with_condition：双方保留 + 限定条件
        env = invoke("review", "decide", *req(
            {"review_item_id": review_item_id,
             "decision_code": "keep_both_with_condition",
             "user_note": "仅适用于个人知识库"}))
        assert env["status"] == "success"
        assert env["data"]["review_item_status"] == "resolved"

        # 决定记录可回看（场景 US4-5 一并验证）
        env = invoke("review", "show", *req({"review_item_id": review_item_id}))
        decisions = env["data"]["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["decision_code"] == "keep_both_with_condition"
        assert decisions[0]["user_note"] == "仅适用于个人知识库"


# ---------------------------------------------------------------------------
# 用户故事 4（P2）：检查资料的处理结果
# ---------------------------------------------------------------------------

class TestUserStory4ResultsVisibility:
    def test_full_result_chain_without_original_dialog(self, cli_env):
        """场景 1/2/3/5，SC-001/004/010：不依赖原对话即可核对全部结果。"""
        completed = complete_text_flow(US1_TEXT)
        submission_id = completed["data"]["submission_id"]

        # SC-010：当前状态、证据、关联页面、审核决定均可读取
        env = invoke("result", "show", *req({"submission_id": submission_id}))
        assert env["status"] == "success"
        assert env["data"]["submission"]["status"] == "completed"
        assert env["data"]["evidence"]
        assert env["data"]["affected_pages"]
        assert env["data"]["latest_processing_record"]["outcome"] == "completed"
        assert env["data"]["review"] == []

        # SC-004：证据可阅读、页面贡献可追溯
        evidence_id = env["data"]["evidence"][0]["evidence_id"]
        env = invoke("evidence", "show", *req({"evidence_id": evidence_id}))
        assert US1_TEXT in env["data"]["content"]
        assert env["data"]["contributions"]

    def test_reviewed_submission_shows_decision_history(self, cli_env, tmp_path):
        """场景 4/5：中断后的结果与审核决定历史可回看。"""
        path = make_scanned_pdf(tmp_path / "scan.pdf")
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "pdf", "temporary_path": str(path)}}))
        review_item_id = env["result_refs"][0]
        submission_id = env["data"]["submission_id"]

        invoke("review", "decide", *req(
            {"review_item_id": review_item_id, "decision_code": "terminate"}))

        env = invoke("result", "show", *req({"submission_id": submission_id}))
        assert env["data"]["submission"]["status"] == "terminated"
        env = invoke("review", "show", *req({"review_item_id": review_item_id}))
        assert env["data"]["decisions"][0]["decision_code"] == "terminate"


# ---------------------------------------------------------------------------
# 用户故事 5（P2）：从人工审核中发现规则缺口
# ---------------------------------------------------------------------------

class TestUserStory5RuleGaps:
    _scan_seq = 0

    def make_interruption(self, tmp_path):
        """生成唯一变体的扫描 PDF 中断项。"""
        self._scan_seq += 1
        path = make_scanned_pdf(tmp_path / f"gap-scan-{self._scan_seq}.pdf",
                                variant=self._scan_seq + 100)
        env = invoke("ingest", "prepare", *req(
            {"input": {"kind": "pdf", "temporary_path": str(path)}}))
        assert env["status"] == "review_required", env
        return env["result_refs"][0]

    def test_rule_candidate_groups_recurring_cases(self, cli_env, tmp_path):
        """场景 1/2/3，SC-014/015：同类案例归类并形成关联候选。"""
        # 三条同一规则缺口（扫描 PDF 无文字层）
        same_trigger = [self.make_interruption(tmp_path) for _ in range(3)]
        for item_id in same_trigger:
            invoke("review", "decide", *req(
                {"review_item_id": item_id, "decision_code": "terminate",
                 "user_note": "先终止，等待规则化处理"}))

        # 按触发原因筛选（SC-014：能按原因和步骤重新找到）
        env = invoke("review", "list", *req(
            {"status": "resolved", "trigger_code": "EXTRACTION_INCOMPLETE"}))
        assert env["data"]["count"] == 3
        assert {i["review_item_id"] for i in env["data"]["items"]} == set(
            same_trigger)

        # 形成关联全部来源案例的规则改进候选
        env = invoke("rule-candidate", "create", *req({
            "title": "扫描版 PDF 的处理规则缺口",
            "gap_summary": "多份扫描 PDF 触发同类中断，缺少规则化的预处理约定",
            "trigger_pattern": {"trigger_code": "EXTRACTION_INCOMPLETE"},
            "affected_steps": ["evidence_fixing"],
            "review_item_ids": same_trigger}))
        assert env["status"] == "success"
        candidate_id = env["data"]["candidate_id"]
        assert sorted(env["data"]["linked_review_items"]) == sorted(same_trigger)

        env = invoke("rule-candidate", "show", *req({"candidate_id": candidate_id}))
        assert len(env["data"]["cases"]) == 3
        for case in env["data"]["cases"]:
            assert case["decisions"], "案例必须展示已有决定"
            assert case["decisions"][0]["decision_code"] == "terminate"

        # 关闭候选不修改已完成的个案决定
        env = invoke("rule-candidate", "close", *req({
            "candidate_id": candidate_id, "reason": "本轮不处理"}))
        assert env["status"] == "success"
        assert env["data"]["status"] == "closed"
        for item_id in same_trigger:
            env = invoke("review", "show", *req({"review_item_id": item_id}))
            assert env["data"]["review_item"]["status"] == "resolved"

    def test_single_decision_not_becomes_global_rule(self, cli_env, tmp_path):
        """场景 4，SC-015：单次决定不自动改变全局规则。"""
        item_id = self.make_interruption(tmp_path)
        env = invoke("review", "decide", *req(
            {"review_item_id": item_id, "decision_code": "terminate",
             "user_note": "这份特殊，仅本次终止"}))
        assert env["status"] == "success"

        # 没有自动生成规则改进候选
        env = invoke("rule-candidate", "list", *req({}))
        assert env["data"]["count"] == 0

        # 新的同类输入仍然按原规则进入审核，而不是沿用上次决定
        next_item_id = self.make_interruption(tmp_path)
        env = invoke("review", "show", *req({"review_item_id": next_item_id}))
        item = env["data"]["review_item"]
        assert item["status"] == "open"
        assert item["allowed_decisions"], "仍需用户逐项决定"
