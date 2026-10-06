"""资料处理任务引擎：prepare → continue（next_action 循环）→ commit。

- 中间状态只存在于受控工作区，不进入飞书；
- prepare 遇到输入即满足人工审核条件的情况时，直接写入终态并返回
  review_required（contracts/agent-cli.md「ingest prepare」）；
- continue 只校验不写入；其余终态写入集中在 commit（FinalWriteSet）；
- 入口去重拦截确定性重复，不代替内容价值判断；
- 包含已采纳新增内容的资料必须完成页面演化才能形成 completed 终态
  （FR-023、US-1.5）。
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..contracts import actions as act
from ..contracts.envelope import (
    NOT_FOUND,
    POLICY_BLOCKED,
    VALIDATION_ERROR,
    CapsuleError,
    Envelope,
    NextAction,
    failed as failed_env,
    review_required as review_env,
    success as success_env,
)
from ..domain import fingerprint as fp
from ..domain import ids, policies
from ..domain.entities import (
    DerivedEvidence,
    EvidenceContributionRecord,
    KnowledgePageRecord,
    OriginalEvidence,
    ProcessingRecord,
    ReviewItemRecord,
    Submission,
    UnderstandingResultRecord,
)
from ..extractors.image_extractor import validate_image
from ..extractors.media_extractor import extract_media
from ..extractors.pdf_extractor import extract_pdf
from ..extractors.text_extractor import extract_text
from ..extractors.web_extractor import extract_web
from ..temporary.workspace import TaskWorkspace

MEDIA_KINDS = {"pdf", "image", "audio", "video"}
MAX_CANDIDATE_PAGES = 10


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def ingest_prepare(request, backend, workspace: TaskWorkspace,
                   config: Config) -> Envelope:
    spec = request.input
    try:
        _validate_input_spec(spec)
    except CapsuleError as exc:
        return failed_env(request.request_id, exc.code, exc.message,
                          retryable=exc.retryable, details=exc.details,
                          suggested_action=exc.suggested_action)
    workspace.sweep_stale(config.task_ttl_hours)

    # 入口去重：确定性重复直接拦截，不创建任务，也不写任何记录
    dedup = _entry_dedup(spec, backend)
    if dedup is not None:
        return success_env(request.request_id, data={"entry_dedup": dedup},
                           result_refs=[dedup["duplicate_of"]])

    task_id = ids.new_id()
    task_ref = workspace.create(task_id)
    state = {
        "task_id": task_id,
        "kind": "ingest",
        "created_at": ids.now_utc(),
        "input": {"kind": spec.kind, "text": spec.text,
                  "path": spec.temporary_path, "url": spec.url,
                  "user_context": request.user_context},
        "fingerprint": None,
        "extraction": None,
        "web_image_keys": [],
        "understanding": None,
        "page_plan": None,
        "planned_outcome": None,
        "review_payload": None,
        "expected_action": None,
    }
    try:
        extraction, fingerprint = _extract(spec, config)
        state["extraction"] = extraction
        state["fingerprint"] = fingerprint
        workspace.save_state(task_ref, state)
    except CapsuleError as exc:
        workspace.cleanup(task_ref)
        return failed_env(request.request_id, exc.code, exc.message,
                          retryable=exc.retryable, details=exc.details,
                          suggested_action=exc.suggested_action)

    # 扫描版 PDF（无文字层）：不渲染、不 OCR，直接进入人工审核（FR-008、US-2 场景）
    if spec.kind == "pdf" and not extraction["text"].strip():
        workspace.cleanup(task_ref)
        return _commit_immediate_review(
            request, backend, state,
            trigger="EXTRACTION_INCOMPLETE",
            reason="PDF 没有可用文字层（可能为扫描图片），系统不渲染页面、"
                   "不执行 OCR、不补写内容；核心内容无法取得，等待用户处理",
            attempted=["pdf_text_layer_extraction"],
            rule_gap_flag=True)

    # 网页正文为空：核心内容无法识别，直接进入人工审核（FR-016、US-2 场景 10）
    if spec.kind == "web_url" and not extraction["text"].strip():
        workspace.cleanup(task_ref)
        return _commit_immediate_review(
            request, backend, state,
            trigger="EMPTY_WEB_EXTRACTION",
            reason="网页正文抽取结果为空（可能仅 JavaScript 渲染或需要登录），"
                   "未补写内容，不临时增加浏览器兜底，等待用户处理",
            attempted=["web_body_extraction"],
            rule_gap_flag=True)

    # 音视频无法可靠转写（无语音内容或视频无音频轨道）：进入人工审核（FR-011）
    if spec.kind in ("audio", "video") and not extraction["text"].strip():
        workspace.cleanup(task_ref)
        return _commit_immediate_review(
            request, backend, state,
            trigger="MEDIA_TRANSCRIPTION_EMPTY",
            reason="音频/视频未能可靠转写出文字内容（无语音或无音频轨道），"
                   "系统不根据局部信息补写内容，等待用户处理",
            attempted=["media_transcription"],
            rule_gap_flag=True)

    # 图片的语义理解由 Agent 视觉任务完成（FR-009）
    if spec.kind == "image":
        state["expected_action"] = act.ACTION_UNDERSTAND_IMAGE
        workspace.save_state(task_ref, state)
        next_action = NextAction(
            action_code=act.ACTION_UNDERSTAND_IMAGE,
            context_refs=[_write_input_context(workspace, task_ref, spec,
                                               request.user_context)],
            allowed_next_commands=["capsule ingest continue"])
        return success_env(request.request_id,
                           data={"task_ref": task_ref,
                                 "quality": extraction["quality"]},
                           next_action=next_action)

    # 网页包含可能携带独立信息的图片：交由 Agent 受控视觉路径处理（FR-014/016）
    if spec.kind == "web_url" and extraction.get("image_candidates"):
        state["web_image_keys"] = [c["key"] for c in extraction["image_candidates"]]
        state["expected_action"] = act.ACTION_UNDERSTAND_WEB_IMAGES
        workspace.save_state(task_ref, state)
        context_refs = [workspace.write_context(
            task_ref, "evidence.txt", extraction["text"])]
        context_refs.append(workspace.write_context(
            task_ref, "web-images.txt",
            "需要判断的网页图片候选：\n" + "\n".join(
                f"- {c['key']}：{c['description']}" for c in extraction["image_candidates"])))
        return success_env(
            request.request_id,
            data={"task_ref": task_ref,
                  "image_candidates": extraction["image_candidates"],
                  "extraction_summary": extraction["text"][:500]},
            next_action=NextAction(
                action_code=act.ACTION_UNDERSTAND_WEB_IMAGES,
                context_refs=context_refs,
                allowed_next_commands=["capsule ingest continue"]))

    # text / pdf（有文字层）/ audio / video：证据文字已就绪，进入知识比较
    return _enter_compare_knowledge(request, backend, workspace, state,
                                    extraction["text"])


def _validate_input_spec(spec) -> None:
    if spec.kind == "text" and not (spec.text or "").strip():
        raise CapsuleError(VALIDATION_ERROR, "纯文本输入必须包含 text 字段")
    if spec.kind in MEDIA_KINDS and not spec.temporary_path:
        raise CapsuleError(VALIDATION_ERROR, f"{spec.kind} 输入必须提供 temporary_path")
    if spec.kind == "web_url" and not (spec.url or "").strip():
        raise CapsuleError(VALIDATION_ERROR, "web_url 输入必须提供 url 字段")
    if spec.kind == "web_url" and spec.temporary_path:
        raise CapsuleError(
            VALIDATION_ERROR, "web_url 输入不接受本地文件路径，只接受 URL")
    if spec.kind == "text" and spec.temporary_path:
        raise CapsuleError(
            VALIDATION_ERROR, "纯文本输入不接受本地文件路径，请使用 text 字段")


def _entry_dedup(spec, backend) -> dict | None:
    if spec.kind == "text":
        digest = fp.fingerprint_text("text", spec.text or "")
    elif spec.kind == "web_url":
        digest = fp.fingerprint_url(spec.url or "")
    else:
        data = Path(spec.temporary_path).read_bytes()
        digest = fp.fingerprint_bytes(spec.kind, data)
    existing = backend.find_submission_by_fingerprint(digest)
    if existing is not None:
        records = backend.list_processing_records(existing.submission_id)
        return {
            "duplicate": True,
            "duplicate_of": existing.submission_id,
            "status": existing.status,
            "submitted_at": existing.created_at,
            "reason_code": records[0].reason_code if records else None,
            "note": "入口去重只拦截确定性重复（相同 URL、相同文件、相同消息），"
                    "不代替内容价值判断",
        }
    return None


def _extract(spec, config: Config) -> tuple[dict, str]:
    if spec.kind == "text":
        result = extract_text(spec.text or "", config.evidence_max_chars)
        digest = fp.fingerprint_text("text", spec.text or "")
    elif spec.kind == "web_url":
        result = extract_web(spec.url or "", config.web_max_bytes,
                             config.web_timeout_seconds, config.evidence_max_chars)
        digest = fp.fingerprint_url(spec.url or "")
    elif spec.kind == "pdf":
        result = extract_pdf(spec.temporary_path, config.pdf_max_pages,
                             config.pdf_max_bytes, config.evidence_max_chars)
        digest = fp.fingerprint_bytes("pdf", Path(spec.temporary_path).read_bytes())
    elif spec.kind == "image":
        result = validate_image(spec.temporary_path, config.image_max_pixels,
                                config.pdf_max_bytes)
        digest = fp.fingerprint_bytes("image", Path(spec.temporary_path).read_bytes())
    else:
        result = extract_media(spec.temporary_path, config.media_max_bytes,
                               config.evidence_max_chars, spec.kind)
        digest = fp.fingerprint_bytes(spec.kind, Path(spec.temporary_path).read_bytes())
    return result.model_dump(), digest


def _write_input_context(workspace: TaskWorkspace, task_ref: str, spec,
                         user_context: str | None) -> str:
    if spec.temporary_path:
        return workspace.write_context(
            task_ref, "input-ref.txt",
            f"输入文件（用户原始文件，只读，任务结束后系统不保留）：{spec.temporary_path}\n"
            f"输入类型：{spec.kind}\n用户说明：{user_context or '无'}\n")
    return workspace.write_context(
        task_ref, "input-ref.txt",
        f"输入类型：{spec.kind}\nURL：{spec.url or '无'}\n"
        f"用户说明：{user_context or '无'}\n")


def _enter_compare_knowledge(request, backend, workspace: TaskWorkspace,
                             state: dict, evidence_text: str) -> Envelope:
    task_ref = _task_ref_of(workspace, state)
    state["expected_action"] = act.ACTION_COMPARE_KNOWLEDGE
    workspace.save_state(task_ref, state)
    next_action = _compare_knowledge_action(backend, workspace, task_ref,
                                            state, evidence_text)
    return success_env(
        request.request_id,
        data={"task_ref": task_ref,
              "extraction_summary": evidence_text[:500],
              "omissions": state["extraction"].get("omissions", [])},
        next_action=next_action)


def _task_ref_of(workspace: TaskWorkspace, state: dict) -> str:
    return str(workspace.tasks_dir / state["task_id"])


def _compare_knowledge_action(backend, workspace: TaskWorkspace, task_ref: str,
                              state: dict, evidence_text: str) -> NextAction:
    existing_pages = backend.list_pages(status="active", limit=200)
    state["candidate_page_ids"] = [p.page_id for p in existing_pages[:MAX_CANDIDATE_PAGES]]
    workspace.save_state(task_ref, state)

    context = state["input"].get("user_context") or ""
    context_refs = [workspace.write_context(
        task_ref, "evidence.txt",
        (f"（用户说明：{context}）\n\n" if context else "") + evidence_text)]
    catalog_lines = []
    for page in existing_pages[:MAX_CANDIDATE_PAGES]:
        content = backend.read_page_content(page.page_id)
        context_refs.append(workspace.write_context(
            task_ref, f"page-{page.page_id}.md",
            f"# {page.title}（{page.page_type}，revision {page.revision_id}）\n\n"
            f"{content}"))
        catalog_lines.append(
            f"- {page.page_id}: {page.title}（{page.page_type}，revision {page.revision_id}）")
    context_refs.append(workspace.write_context(
        task_ref, "candidates.txt",
        "候选页面目录：\n" + "\n".join(catalog_lines)))
    return NextAction(
        action_code=act.ACTION_COMPARE_KNOWLEDGE,
        context_refs=context_refs,
        allowed_next_commands=["capsule ingest continue"])


# ---------------------------------------------------------------------------
# continue
# ---------------------------------------------------------------------------

def ingest_continue(request, backend, workspace: TaskWorkspace,
                    config: Config) -> Envelope:
    try:
        state = workspace.load_state(request.task_ref)
    except CapsuleError as exc:
        return failed_env(request.request_id, exc.code, exc.message,
                          suggested_action=exc.suggested_action)
    if state.get("kind") != "ingest":
        return failed_env(request.request_id, VALIDATION_ERROR,
                          "任务引用不是资料处理任务")
    expected = state.get("expected_action")
    if expected != request.action_code:
        return failed_env(
            request.request_id, VALIDATION_ERROR,
            f"动作代码不匹配：期望 {expected}，收到 {request.action_code}",
            suggested_action="按 next_action 的 action_code 提交结果")
    if state.get("planned_outcome"):
        return failed_env(request.request_id, VALIDATION_ERROR,
                          "任务已经准备提交，请调用 capsule ingest commit")

    handler = {
        act.ACTION_UNDERSTAND_IMAGE: _continue_understand_image,
        act.ACTION_UNDERSTAND_WEB_IMAGES: _continue_understand_web_images,
        act.ACTION_COMPARE_KNOWLEDGE: _continue_compare_knowledge,
        act.ACTION_ORGANIZE_PAGES: _continue_organize_pages,
    }.get(request.action_code)
    if handler is None:
        return failed_env(request.request_id, VALIDATION_ERROR,
                          f"未知动作代码：{request.action_code}")
    try:
        return handler(request, state, backend, workspace, config)
    except CapsuleError as exc:
        return failed_env(request.request_id, exc.code, exc.message,
                          retryable=exc.retryable, details=exc.details,
                          suggested_action=exc.suggested_action)


def _continue_understand_image(request, state, backend, workspace,
                               config) -> Envelope:
    result = act.UnderstandImageResult(**request.result)
    if result.outcome == "no_evidence":
        state["planned_outcome"] = "no_evidence"
        state["expected_action"] = None
        state["extraction"]["omissions"] = result.omissions
        workspace.save_state(request.task_ref, state)
        return success_env(
            request.request_id,
            data={"task_ref": request.task_ref, "ready_to_commit": True,
                  "planned_outcome": "no_evidence", "reason": result.reason},
            result_refs=[request.task_ref])
    if result.outcome == "review":
        state["planned_outcome"] = "needs_review"
        state["review_payload"] = _interruption_review_payload(
            trigger="IMAGE_UNRELIABLE",
            reason="图片内容只能部分理解，系统不补写无法确认的内容："
                   f"{'; '.join(result.uncertainties) or result.reason}",
            attempted=["image_understanding"],
            requires_new_input=not state["input"].get("url"))
        state["expected_action"] = None
        workspace.save_state(request.task_ref, state)
        return success_env(
            request.request_id,
            data={"task_ref": request.task_ref, "ready_to_commit": True,
                  "planned_outcome": "needs_review"},
            result_refs=[request.task_ref])
    # usable：图片文字成为证据草稿（FR-009）
    if not result.evidence_text.strip():
        raise CapsuleError(
            VALIDATION_ERROR, "outcome=usable 时必须提供 evidence_text")
    state["extraction"]["text"] = result.evidence_text
    state["extraction"]["omissions"] = result.omissions
    return _enter_compare_knowledge(request, backend, workspace, state,
                                    result.evidence_text)


def _continue_understand_web_images(request, state, backend, workspace,
                                    config) -> Envelope:
    result = act.UnderstandWebImagesResult(**request.result)
    policies.validate_web_images_result(result, set(state["web_image_keys"]))
    if result.core_incomplete:
        state["planned_outcome"] = "needs_review"
        state["review_payload"] = _interruption_review_payload(
            trigger="WEB_IMAGE_CORE_INCOMPLETE",
            reason="网页核心内容依赖无法可靠转换的图片，证据不完整："
                   f"{result.reason or '图片信息无法可靠识别'}",
            attempted=["web_body_extraction", "web_image_understanding"],
            requires_new_input=True)
        state["expected_action"] = None
        workspace.save_state(request.task_ref, state)
        return success_env(
            request.request_id,
            data={"task_ref": request.task_ref, "ready_to_commit": True,
                  "planned_outcome": "needs_review"},
            result_refs=[request.task_ref])

    # 忽略的图片记录省略结果（FR-013/046）；转换后的文字并入证据（FR-014）
    omissions = list(state["extraction"].get("omissions", []))
    converted: list[str] = []
    for handling in result.images:
        if handling.ignored:
            omissions.append(f"忽略网页图片 {handling.image_key}：{handling.reason}")
        else:
            converted.append(handling.text)
    state["extraction"]["omissions"] = omissions
    if converted:
        evidence_text = (state["extraction"]["text"] +
                         "\n\n（网页图片转换内容）\n" + "\n\n".join(converted))
        state["extraction"]["text"] = evidence_text
    else:
        evidence_text = state["extraction"]["text"]
    return _enter_compare_knowledge(request, backend, workspace, state,
                                    evidence_text)


def _continue_compare_knowledge(request, state, backend, workspace,
                                config) -> Envelope:
    result = act.UnderstandEvidenceResult(**request.result)
    evidence_text = state["extraction"]["text"]
    existing_pages = backend.list_pages(status="active", limit=200)
    policies.validate_understanding(
        result, evidence_text, {p.page_id for p in existing_pages})
    state["understanding"] = result.model_dump()

    # 为页面组织准备候选页面上下文：理解结果引用的页面 + 标题相关的页面
    referenced = {t.existing_page_id for t in result.topics if t.existing_page_id}
    referenced |= {c.page_id for c in result.conflicts}
    candidates = [p for p in existing_pages if p.page_id in referenced]
    if not candidates:
        candidates = existing_pages[:MAX_CANDIDATE_PAGES]
    state["candidate_page_ids"] = [p.page_id for p in candidates]
    state["expected_action"] = act.ACTION_ORGANIZE_PAGES
    workspace.save_state(request.task_ref, state)

    context_refs = [workspace.write_context(
        request.task_ref, "evidence.txt", evidence_text)]
    catalog_lines = []
    for page in candidates:
        content = backend.read_page_content(page.page_id)
        context_refs.append(workspace.write_context(
            request.task_ref, f"page-{page.page_id}.md",
            f"# {page.title}（{page.page_type}，revision {page.revision_id}）\n\n"
            f"{content}"))
        catalog_lines.append(
            f"- {page.page_id}: {page.title}（{page.page_type}，revision {page.revision_id}）")
    context_refs.append(workspace.write_context(
        request.task_ref, "candidates.txt",
        "候选页面目录：\n" + "\n".join(catalog_lines)))
    return success_env(
        request.request_id,
        data={"task_ref": request.task_ref,
              "candidate_pages": [{"page_id": p.page_id, "title": p.title,
                                   "page_type": p.page_type,
                                   "revision_id": p.revision_id}
                                  for p in candidates]},
        next_action=NextAction(
            action_code=act.ACTION_ORGANIZE_PAGES,
            context_refs=context_refs,
            allowed_next_commands=["capsule ingest continue"]))


def _continue_organize_pages(request, state, backend, workspace,
                             config) -> Envelope:
    result = act.OrganizePagesResult(**request.result)
    evidence_text = state["extraction"]["text"]
    pages_by_id = {p.page_id: p for p in backend.list_pages(status="active", limit=200)}
    # 冲突挂起更新引用的页面也纳入校验
    for update in result.pending_updates:
        page = backend.get_page(update.page_id)
        if page is not None:
            pages_by_id[page.page_id] = page
    active_titles = {(p.page_type, p.title) for p in pages_by_id.values()
                     if p.status == "active"}
    policies.validate_page_plan(result, pages_by_id, evidence_text,
                                config.page_max_chars, active_titles)

    understanding = act.UnderstandEvidenceResult(**state["understanding"])
    unresolved = policies.unresolved_conflicts(understanding)
    has_pending = bool(result.pending_updates)
    if unresolved and not has_pending:
        raise CapsuleError(
            VALIDATION_ERROR,
            "理解结果包含需要用户决定的冲突，页面组织必须把冲突页面的更新"
            "放入 pending_updates，不能静默覆盖或强行合并")

    if unresolved and has_pending:
        state["planned_outcome"] = "needs_review"
        state["review_payload"] = _conflict_review_payload(result, unresolved)
        state["page_plan"] = result.model_dump()
        state["expected_action"] = None
        workspace.save_state(request.task_ref, state)
        return success_env(
            request.request_id,
            data={"task_ref": request.task_ref, "ready_to_commit": True,
                  "planned_outcome": "needs_review",
                  "note": "非冲突部分将正常采纳，冲突部分等待用户决定"},
            result_refs=[request.task_ref])

    state["page_plan"] = result.model_dump()
    state["planned_outcome"] = "completed"  # 不被采纳也是完成的处理结果
    state["expected_action"] = None
    workspace.save_state(request.task_ref, state)
    return success_env(
        request.request_id,
        data={"task_ref": request.task_ref, "ready_to_commit": True,
              "planned_outcome": "completed",
              "not_adopted_reason": result.not_adopted_reason},
        result_refs=[request.task_ref])


def _interruption_review_payload(trigger: str, reason: str,
                                 attempted: list[str],
                                 requires_new_input: bool) -> dict:
    suggested_next_task = {
        "note": "决定后由 Agent 启动新的处理任务，不恢复原任务执行现场",
    }
    if requires_new_input:
        suggested_next_task["requires_new_input"] = True
        suggested_next_task["input_needed"] = (
            "资料没有来源 URL，系统未保存原始附件；继续处理需要重新提交资料")
    return {
        "review_type": "processing_interruption",
        "trigger_code": trigger,
        "detected_stage": "evidence_fixing",
        "reason": reason,
        "attempted_actions": attempted,
        "allowed_decisions": [
            {"code": "supplement_context", "display": "补充上下文",
             "consequence": "以补充说明启动新的处理任务"},
            {"code": "retry", "display": "重试",
             "consequence": "在当前输入上重新执行一次处理任务"},
            {"code": "specify_topic", "display": "指定主题",
             "consequence": "按用户指定的主题归属重新组织"},
            {"code": "resubmit", "display": "重新提交资料",
             "consequence": "提供可读取的新版本输入（如带文字层的文件）"},
            {"code": "terminate", "display": "终止处理",
             "consequence": "保留记录并结束，不再处理"},
        ],
        "suggested_next_task": suggested_next_task,
        "rule_gap_flag": True,
    }


def _conflict_review_payload(plan: act.OrganizePagesResult,
                             unresolved: list[act.ConflictItem]) -> dict:
    return {
        "review_type": "knowledge_conflict",
        "trigger_code": "KNOWLEDGE_CONFLICT",
        "detected_stage": "page_organization",
        "reason": "新资料挑战了知识页中的既有判断，系统不静默覆盖、不强行合并，"
                  "等待用户决定",
        "conflicts": [c.model_dump() for c in unresolved],
        "pending_updates": [u.model_dump() for u in plan.pending_updates],
        "allowed_decisions": [
            {"code": "keep_both_with_condition", "display": "保留双方并限定条件",
             "consequence": "把新观点作为补充判断写入页面，"
                            "并把用户说明记录为限定条件"},
            {"code": "adopt_existing", "display": "采纳既有判断",
             "consequence": "不修改页面，冲突部分记为不被采纳"},
            {"code": "adopt_new", "display": "采纳新观点",
             "consequence": "用挂起的更新替换对应页面内容，旧判断不再保留在正文"},
            {"code": "defer", "display": "暂缓处理",
             "consequence": "本次不修改页面，审核项关闭，可另行发起处理"},
        ],
        "suggested_next_task": {
            "note": "决定后由 Agent 启动新的处理任务（如需要），不恢复原任务执行现场"},
        "rule_gap_flag": False,
    }


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------

def ingest_commit(request, backend, workspace: TaskWorkspace,
                  config: Config) -> Envelope:
    try:
        state = workspace.load_state(request.task_ref)
    except CapsuleError as exc:
        return failed_env(request.request_id, exc.code, exc.message,
                          suggested_action=exc.suggested_action)
    if state.get("kind") != "ingest":
        return failed_env(request.request_id, VALIDATION_ERROR,
                          "任务引用不是资料处理任务")
    outcome = state.get("planned_outcome")
    if not outcome:
        return failed_env(
            request.request_id, VALIDATION_ERROR,
            "任务尚未完成全部语义任务，不能提交",
            suggested_action="继续处理 next_action 或重新发起任务")
    try:
        result_refs = _execute_commit(state, backend, config)
    except CapsuleError as exc:
        return failed_env(request.request_id, exc.code, exc.message,
                          retryable=exc.retryable, details=exc.details,
                          suggested_action=exc.suggested_action)
    workspace.cleanup(request.task_ref)
    if outcome == "needs_review":
        return review_env(request.request_id,
                          data={"outcome": outcome,
                                "submission_id": result_refs["submission_id"]},
                          result_refs=[result_refs["review_item_id"]])
    return success_env(
        request.request_id,
        data={"outcome": outcome, "submission_id": result_refs["submission_id"],
              "evidence_id": result_refs.get("evidence_id"),
              "affected_pages": result_refs.get("page_ids", [])},
        result_refs=[result_refs["submission_id"]] +
        ([result_refs["evidence_id"]] if result_refs.get("evidence_id") else []) +
        result_refs.get("page_ids", []))


def _commit_immediate_review(request, backend, state: dict, trigger: str,
                             reason: str, attempted: list[str],
                             rule_gap_flag: bool) -> Envelope:
    """prepare 阶段输入即满足人工审核条件：直接写入终态（agent-cli.md）。"""
    payload = _interruption_review_payload(
        trigger, reason, attempted, requires_new_input=not state["input"].get("url"))
    payload["rule_gap_flag"] = rule_gap_flag
    state["planned_outcome"] = "needs_review"
    state["review_payload"] = payload
    result_refs = _execute_commit(state, backend, None)
    return review_env(
        request.request_id,
        data={"outcome": "needs_review",
              "submission_id": result_refs["submission_id"],
              "reason": reason},
        result_refs=[result_refs["review_item_id"]])


def _execute_commit(state: dict, backend, config) -> dict:
    now = ids.now_utc()
    spec = state["input"]
    outcome = state["planned_outcome"]
    submission_id = ids.new_id()
    result_refs: dict = {"submission_id": submission_id, "page_ids": []}

    submission = Submission(
        submission_id=submission_id, input_kind=spec["kind"],
        source_url=spec.get("url"), user_context=spec.get("user_context"),
        content_fingerprint=state.get("fingerprint"), status=outcome,
        created_at=now, updated_at=now)
    backend.save_submission(submission)

    # 原始证据：仅来源 URL（FR-004/005）
    original_id = None
    if spec.get("url"):
        original_id = ids.new_id()
        backend.save_original_evidence(OriginalEvidence(
            original_evidence_id=original_id, submission_id=submission_id,
            url=spec["url"], created_at=now, updated_at=now))

    # 衍生证据
    evidence_id = None
    evidence_text = state["extraction"]["text"]
    if evidence_text.strip():
        evidence_id = ids.new_id()
        evidence = DerivedEvidence(
            evidence_id=evidence_id, submission_id=submission_id,
            original_evidence_id=original_id,
            title=f"证据-{spec['kind']}-{submission_id[:8]}",
            content_hash=fp.content_hash(evidence_text),
            preview=evidence_text[:120], status="active", created_at=now)
        backend.save_derived_evidence(evidence)
        backend.save_evidence_content(evidence_id, evidence_text)
        result_refs["evidence_id"] = evidence_id

    understanding_id = None
    if state.get("understanding") and evidence_id:
        understanding_id = ids.new_id()
        record = UnderstandingResultRecord(
            understanding_id=understanding_id, submission_id=submission_id,
            evidence_id=evidence_id, schema_version="1.0",
            **{k: state["understanding"][k] for k in
               ("topics", "key_claims", "duplicate_parts", "novel_parts",
                "conflicts", "target_pages", "conservative_notes")},
            validation_status="valid", created_at=now)
        backend.save_understanding(record)

    # 页面创建 / 更新与贡献记录
    page_plan = state.get("page_plan")
    if page_plan and evidence_id:
        plan = act.OrganizePagesResult(**page_plan)
        understanding = act.UnderstandEvidenceResult(**state["understanding"])
        # FR-023/US-1.5：有新增价值却没有任何页面写入时，不能形成完成终态
        if outcome == "completed" and understanding.novel_parts and not (
                plan.creates or plan.updates):
            raise CapsuleError(
                POLICY_BLOCKED,
                "资料包含已采纳的新增内容，必须至少完成一次知识页的新建或更新"
                "才能结束处理，不能停在新增证据或索引记录")
        for create in plan.creates:
            page_id = ids.new_id()
            page = KnowledgePageRecord(
                page_id=page_id, page_type=create.page_type, title=create.title,
                revision_id="rev-1", content_hash=fp.content_hash(create.content),
                created_at=now, updated_at=now)
            backend.save_page(page)
            backend.update_page_content(page_id, "rev-1", create.content,
                                        page.content_hash)
            contribution_kind = ("definition" if create.page_type == "concept"
                                 else "created")
            backend.save_contribution(EvidenceContributionRecord(
                contribution_id=ids.new_id(), evidence_id=evidence_id,
                page_id=page_id, understanding_id=understanding_id or "",
                contribution_kind=contribution_kind,
                claim_anchors=[a.model_dump() for a in create.claim_anchors],
                page_revision_before=None, page_revision_after=page.revision_id,
                summary=f"创建页面「{create.title}」", created_at=now))
            result_refs["page_ids"].append(page_id)
        for update in plan.updates:
            page = backend.get_page(update.page_id)
            if page is None:
                raise CapsuleError(NOT_FOUND, f"页面不存在：{update.page_id}")
            updated = backend.update_page_content(
                update.page_id, update.revision_id, update.content,
                fp.content_hash(update.content))
            backend.save_contribution(EvidenceContributionRecord(
                contribution_id=ids.new_id(), evidence_id=evidence_id,
                page_id=update.page_id, understanding_id=understanding_id or "",
                contribution_kind="added",
                claim_anchors=[a.model_dump() for a in update.claim_anchors],
                page_revision_before=update.revision_id,
                page_revision_after=updated.revision_id,
                summary=update.change_summary or f"更新页面「{page.title}」",
                created_at=now))
            result_refs["page_ids"].append(update.page_id)

    # 审核项（needs_review）
    review_item_id = None
    if outcome == "needs_review":
        payload = state.get("review_payload") or {}
        # FR-034：冲突挂起更新指向的页面同样是受影响页面，必须在审核项中展示
        affected_pages = list(result_refs["page_ids"])
        for update in payload.get("pending_updates", []):
            if update.get("page_id") not in affected_pages:
                affected_pages.append(update["page_id"])
        review_item_id = ids.new_id()
        item = ReviewItemRecord(
            review_item_id=review_item_id, submission_id=submission_id,
            review_type=payload.get("review_type", "processing_interruption"),
            status="open", trigger_code=payload.get("trigger_code", "UNKNOWN"),
            detected_stage=payload.get("detected_stage", "unknown"),
            reason=payload.get("reason", ""),
            evidence_refs=[evidence_id] if evidence_id else [],
            affected_page_refs=affected_pages,
            target_refs=payload.get("target_refs", []),
            attempted_actions=payload.get("attempted_actions", []),
            allowed_decisions=payload.get("allowed_decisions", []),
            recommendation={
                "conflicts": payload.get("conflicts", []),
                "pending_updates": payload.get("pending_updates", []),
            },
            suggested_next_task=payload.get("suggested_next_task", {}),
            rule_gap_flag=payload.get("rule_gap_flag", False),
            created_at=now)
        backend.save_review_item(item)
        result_refs["review_item_id"] = review_item_id

    # 最终处理记录
    reason_code = {
        "completed": (None if (page_plan and result_refs["page_ids"])
                      else "NOT_ADOPTED"),
        "no_evidence": "NO_KNOWLEDGE_EVIDENCE",
        "needs_review": (state.get("review_payload") or {}).get("trigger_code"),
        "terminated": "TERMINATED",
    }.get(outcome)
    plan_not_adopted = (page_plan and
                        act.OrganizePagesResult(**page_plan).not_adopted_reason)
    record = ProcessingRecord(
        processing_record_id=ids.new_id(), submission_id=submission_id,
        outcome=outcome, reason_code=reason_code,
        reason_detail=(plan_not_adopted if reason_code == "NOT_ADOPTED" else None),
        omissions=state["extraction"].get("omissions", []),
        evidence_refs=[evidence_id] if evidence_id else [],
        affected_page_refs=result_refs["page_ids"],
        created_at=now)
    backend.save_processing_record(record)

    # 写后核对：重新读取关键资源，验证结果一致
    _verify_after_write(backend, submission_id, evidence_id,
                        result_refs["page_ids"], state, original_id)
    return result_refs


def _verify_after_write(backend, submission_id, evidence_id, page_ids,
                        state, original_id) -> None:
    submission = backend.get_submission(submission_id)
    if submission is None or submission.status != state["planned_outcome"]:
        raise CapsuleError(
            "PARTIAL_WRITE",
            f"写后核对失败：提交记录 {submission_id} 状态不一致")
    if evidence_id:
        evidence = backend.get_derived_evidence(evidence_id)
        if evidence is None or evidence.status != "active":
            raise CapsuleError("PARTIAL_WRITE",
                               f"写后核对失败：证据 {evidence_id} 状态不一致")
        content = backend.read_evidence_content(evidence_id)
        if fp.content_hash(content) != evidence.content_hash:
            raise CapsuleError("PARTIAL_WRITE",
                               f"写后核对失败：证据 {evidence_id} 内容哈希不一致")
    if original_id:
        original = backend.get_original_evidence_by_submission(submission_id)
        if original is None or original.url != state["input"].get("url"):
            raise CapsuleError("PARTIAL_WRITE",
                               f"写后核对失败：原始证据 URL 记录不一致")
    for page_id in page_ids:
        page = backend.get_page(page_id)
        if page is None:
            raise CapsuleError("PARTIAL_WRITE",
                               f"写后核对失败：页面 {page_id} 缺失")
