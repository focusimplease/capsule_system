"""飞书适配器契约测试：注入 fake runner，验证 lark-cli 1.0.94 命令构造与错误映射。

不访问真实飞书；真实环境验收见 tests/acceptance 的 live_feishu 标记。
FakeRunner 返回与真实 lark-cli 一致的信封：{"ok":true,"data":{...}}。
"""

from __future__ import annotations

import json

import pytest

from capsule_system.contracts.envelope import CapsuleError
from capsule_system.domain import fingerprint as fp
from capsule_system.domain.entities import (
    DerivedEvidence,
    KnowledgePageRecord,
    OriginalEvidence,
    ReviewDecisionRecord,
    ReviewItemRecord,
    Submission,
    UnderstandingResultRecord,
)
from capsule_system.storage.feishu.adapter import (
    TABLES,
    TABLE_SCHEMAS,
    WIKI_BRANCHES,
    FeishuBackend,
    LarkCliRunner,
    escape_markdown,
    unescape_markdown,
)


def flag_value(args: list[str], name: str) -> str | None:
    """解析 flag 值，兼容「--flag 值」与「--flag=值」（连写防止以 - 开头的值被误解析）。"""
    prefix = f"{name}="
    for arg in args:
        if isinstance(arg, str) and arg.startswith(prefix):
            return arg[len(prefix):]
    try:
        index = args.index(name)
        return args[index + 1]
    except (ValueError, IndexError):
        return None


def ok(data: dict | None = None) -> dict:
    return {"ok": True, "identity": "user", "data": data or {}}


def err(code: int) -> dict:
    return {"ok": False, "error": {"code": code, "msg": "fake error"}}


class FakeRunner:
    """按命令前缀路由的 lark-cli 假实现，记录全部调用与 stdin 数据。"""

    def __init__(self, routes: dict | None = None, default: dict | None = None):
        self.calls: list[list[str]] = []
        self.entries: list[tuple[list[str], str | None]] = []
        self.routes = routes or {}
        self.default = default if default is not None else ok()

    def __call__(self, args: list[str], timeout: int = 60,
                 stdin_data: str | None = None) -> dict:
        self.calls.append(list(args))
        self.entries.append((list(args), stdin_data))
        for depth in (3, 2, 1):
            key = tuple(args[:depth])
            if key in self.routes:
                response = self.routes[key]
                if callable(response):
                    return response(args)
                return response
        return self.default

    def calls_matching(self, prefix: list[str]) -> list[list[str]]:
        return [c for c in self.calls if c[:len(prefix)] == prefix]

    def entries_matching(self, prefix: list[str]) -> list[tuple[list[str], str | None]]:
        return [e for e in self.entries if e[0][:len(prefix)] == prefix]


def make_backend(runner: FakeRunner, **overrides) -> FeishuBackend:
    params = {"wiki_space_id": "wiki-1", "base_app_token": "base-1",
              "drive_root_folder_id": "folder-1", "user_id": "u1"}
    params.update(overrides)
    return FeishuBackend(runner, **params)


class TestMarkdownEscaping:
    def test_escape_unescape_round_trip(self):
        samples = [
            "普通文字。",
            "价格$100与_下划线_和[链接]和*星号*和`代码`",
            "# 行首标题\n- 列表项\n> 引用\n+ 加号列表",
            "反斜杠\\与\\#混合\\[转义\\]",
            "|表格|线|和---分隔线",
            "多行\n\n文本\n\n\n含空行",
        ]
        for text in samples:
            assert unescape_markdown(escape_markdown(text)) == text

    def test_unescape_identity_for_plain_text(self):
        # 旧数据（未转义写入）不含转义序列，反转义是恒等变换
        assert unescape_markdown("真实冒烟证据：lark-cli 1.0.94 验证。") == \
            "真实冒烟证据：lark-cli 1.0.94 验证。"


def record_list_response(records: list[dict], has_more: bool = False) -> dict:
    """把 [{字段:值, record_id}] 转成 +record-list 的矩阵输出信封。"""
    columns = list(TABLE_SCHEMAS["submissions"][0])  # 占位，实际按记录键生成
    if records:
        columns = [k for k in records[0] if k != "record_id"]
    rows = [[r.get(c) for c in columns] for r in records]
    return ok({"fields": columns, "data": rows,
               "record_id_list": [r.get("record_id", "") for r in records],
               "has_more": has_more})


NOW = "2026-01-01T00:00:00Z"


class TestCheckAndBootstrap:
    def test_check_falls_back_to_whoami_when_auth_unsupported(self):
        """凭证外部注入的环境不支持 auth 命令，回退 whoami 验证。"""
        def auth_unsupported(args):
            raise CapsuleError("DEPENDENCY_UNAVAILABLE", "auth not supported")

        runner = FakeRunner(routes={
            ("auth",): auth_unsupported,
            ("whoami",): {"tokenStatus": "ready", "identity": "user",
                          "onBehalfOf": {"userName": "测试用户"}},
        })
        ready = make_backend(runner).check()
        assert ready["backend"] == "feishu"
        assert ready["ok"] is True
        assert ready["auth"] == "ready"
        assert ready["wiki_space_id"] == "wiki-1"
        assert runner.calls_matching(["whoami"]), "必须回退调用 whoami"

    def test_check_without_resource_ids_blocked(self):
        runner = FakeRunner(routes={
            ("whoami",): {"tokenStatus": "ready", "identity": "user"}})
        backend = make_backend(runner, wiki_space_id="", base_app_token="")
        with pytest.raises(CapsuleError) as excinfo:
            backend.check()
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_check_fallback_requires_user_identity(self):
        def auth_unsupported(args):
            raise CapsuleError("DEPENDENCY_UNAVAILABLE", "auth not supported")

        runner = FakeRunner(routes={
            ("auth",): auth_unsupported,
            ("whoami",): {"tokenStatus": "ready", "identity": "bot"},
        })
        with pytest.raises(CapsuleError) as excinfo:
            make_backend(runner).check()
        assert excinfo.value.code == "DEPENDENCY_UNAVAILABLE"
        assert "用户身份" in excinfo.value.message

    def test_bootstrap_rejects_non_acceptance_environment(self):
        backend = make_backend(FakeRunner())
        with pytest.raises(CapsuleError) as excinfo:
            backend.bootstrap("production")
        assert excinfo.value.code == "POLICY_BLOCKED"

    def test_bootstrap_creates_branches_and_tables_without_attachments(self):
        runner = FakeRunner(routes={
            ("wiki", "+node-list"): ok({"nodes": []}),
            ("base", "+table-list"): ok({"tables": [
                {"id": "tbl1", "name": "submissions"}]}),
        })
        created = make_backend(runner).bootstrap("acceptance")
        assert created["branches"] == ["主题", "概念", "证据库"]
        assert created["tables"] == list(TABLES.values())
        assert len(created["tables"]) == 11
        assert created["attachment_fields"] == 0
        # 分支节点创建挂在正确的 space 与 obj-type
        branch_calls = runner.calls_matching(["wiki", "+node-create"])
        assert len(branch_calls) == 3
        for call in branch_calls:
            assert flag_value(call, "--space-id") == "wiki-1"
            assert flag_value(call, "--obj-type") == "docx"
        # 已存在的同名旧表：先建其他新表（Base 必须保留至少一张表），
        # 再删除旧表并按新 schema 重建
        delete_calls = runner.calls_matching(["base", "+table-delete"])
        assert [flag_value(c, "--table-id") for c in delete_calls] == ["submissions"]
        create_names = [flag_value(c, "--name")
                        for c in runner.calls_matching(["base", "+table-create"])]
        assert len(create_names) == 11
        # 删除 submissions 前必须已创建过其他表
        delete_index = runner.calls.index(delete_calls[0])
        created_before_delete = [c for c in runner.calls[:delete_index]
                                 if c[:2] == ["base", "+table-create"]]
        assert len(created_before_delete) >= 1, "删唯一旧表前必须先创建其他表"
        for call in runner.calls_matching(["base", "+table-create"]):
            fields = json.loads(flag_value(call, "--fields"))
            assert len(fields) >= 1
            assert all(f["type"] == "text" for f in fields)
        # 不创建任何附件字段
        assert not any("attachment" in json.dumps(c) for c in runner.calls)

    def test_bootstrap_reuses_existing_branch_nodes(self):
        runner = FakeRunner(routes={
            ("wiki", "+node-list"): ok({"nodes": [
                {"title": "主题", "node_token": "node-topic"},
                {"title": "概念", "node_token": "node-concept"}]}),
            ("base", "+table-list"): ok({"tables": []}),
        })
        created = make_backend(runner).bootstrap("acceptance")
        assert created["branches"][0] == "node-topic"
        assert created["branches"][1] == "node-concept"
        # 只创建缺失的证据库分支
        assert len(runner.calls_matching(["wiki", "+node-create"])) == 1
        assert flag_value(
            runner.calls_matching(["wiki", "+node-create"])[0], "--title") == "证据库"


class TestErrorMapping:
    def test_rate_limited_error_mapped(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): err(1254291)})
        backend = make_backend(runner)
        with pytest.raises(CapsuleError) as excinfo:
            backend.get_submission("s1")
        assert excinfo.value.code == "RATE_LIMITED"
        assert excinfo.value.retryable is True

    def test_http_429_also_maps_to_rate_limited(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): err(429)})
        backend = make_backend(runner)
        with pytest.raises(CapsuleError) as excinfo:
            backend.get_submission("s1")
        assert excinfo.value.code == "RATE_LIMITED"

    def test_not_found_record_returns_none(self):
        """记录查询无结果返回 None（+record-list 空矩阵，而非错误）。"""
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([])})
        assert make_backend(runner).get_submission("s1") is None

    def test_stale_version_mapped(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): err(1254290)})
        backend = make_backend(runner)
        with pytest.raises(CapsuleError) as excinfo:
            backend.get_submission("s1")
        assert excinfo.value.code == "STALE_VERSION"

    def test_unknown_error_maps_to_dependency_unavailable(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): err(9999)})
        backend = make_backend(runner)
        with pytest.raises(CapsuleError) as excinfo:
            backend.get_submission("s1")
        assert excinfo.value.code == "DEPENDENCY_UNAVAILABLE"


class TestLarkCliRunnerEnvelopes:
    """真实 runner 的信封解析（monkeypatch subprocess.run）。"""

    def _patch(self, monkeypatch, returncode, stdout, stderr=""):
        class Completed:
            pass

        completed = Completed()
        completed.returncode = returncode
        completed.stdout = stdout
        completed.stderr = stderr

        def fake_run(cmd, **kwargs):
            return completed

        monkeypatch.setattr("subprocess.run", fake_run)

    def test_success_envelope_returned(self, monkeypatch):
        self._patch(monkeypatch, 0, json.dumps(ok({"a": 1})))
        result = LarkCliRunner("lark-cli").run(["base", "+table-list"])
        assert result["ok"] is True
        assert result["data"] == {"a": 1}

    def test_error_envelope_raises_mapped_error(self, monkeypatch):
        self._patch(monkeypatch, 1, json.dumps(err(1254291)))
        with pytest.raises(CapsuleError) as excinfo:
            LarkCliRunner("lark-cli").run(["base", "+table-list"])
        assert excinfo.value.code == "RATE_LIMITED"

    def test_non_json_failure_raises_dependency_unavailable(self, monkeypatch):
        self._patch(monkeypatch, 2, "", "auth not supported")
        with pytest.raises(CapsuleError) as excinfo:
            LarkCliRunner("lark-cli").run(["auth", "status", "--verify"])
        assert excinfo.value.code == "DEPENDENCY_UNAVAILABLE"
        assert "not supported" in excinfo.value.message

    def test_raw_json_passthrough(self, monkeypatch):
        who = {"tokenStatus": "ready", "identity": "user"}
        self._patch(monkeypatch, 0, json.dumps(who))
        assert LarkCliRunner("lark-cli").run(["whoami"]) == who


class TestSubmissionsAndOriginalEvidence:
    def test_save_submission_creates_record_with_contract_fields(self):
        runner = FakeRunner()
        backend = make_backend(runner)
        submission = Submission(
            submission_id="s1", input_kind="text", status="completed",
            content_fingerprint="fp", created_at=NOW, updated_at=NOW)
        backend.save_submission(submission)
        calls = runner.calls_matching(["base", "+record-batch-create"])
        assert len(calls) == 1
        assert flag_value(calls[0], "--table-id") == "submissions"
        payload = json.loads(flag_value(calls[0], "--json"))
        fields = payload["create_records"][0]
        assert fields["submission_id"] == "s1"
        assert fields["input_kind"] == "text"
        assert fields["content_fingerprint"] == "fp"
        # 提交表不保存任何本地路径或附件
        assert not any("path" in k for k in fields)

    def test_find_submission_by_fingerprint_filters_by_entity_field(self):
        submission = Submission(
            submission_id="s1", input_kind="web_url", status="completed",
            content_fingerprint="fp", created_at=NOW, updated_at=NOW)
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **submission.model_dump()}])})
        found = make_backend(runner).find_submission_by_fingerprint("fp")
        assert found.submission_id == "s1"
        # 过滤条件是元组形式（lark-cli filter-json 契约）
        call = runner.calls_matching(["base", "+record-list"])[0]
        filter_json = json.loads(flag_value(call, "--filter-json"))
        assert filter_json == {"logic": "and",
                               "conditions": [["content_fingerprint", "==", "fp"]]}

    def test_get_submission_round_trips_through_matrix(self):
        submission = Submission(
            submission_id="s1", input_kind="text", status="completed",
            source_url=None, user_context="背景",
            content_fingerprint="fp", created_at=NOW, updated_at=NOW)
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **submission.model_dump()}])})
        got = make_backend(runner).get_submission("s1")
        assert got.submission_id == "s1"
        assert got.user_context == "背景"
        assert got.status == "completed"

    def test_save_original_evidence_only_stores_url(self):
        runner = FakeRunner()
        backend = make_backend(runner)
        backend.save_original_evidence(OriginalEvidence(
            original_evidence_id="oe1", submission_id="s1",
            url="https://example.com/a", created_at=NOW, updated_at=NOW))
        calls = runner.calls_matching(["base", "+record-batch-create"])
        assert flag_value(calls[0], "--table-id") == "original_evidence"
        payload = json.loads(flag_value(calls[0], "--json"))
        fields = payload["create_records"][0]
        assert fields["url"] == "https://example.com/a"
        assert set(fields) == {
            "original_evidence_id", "submission_id", "url", "availability",
            "last_checked_at", "created_at", "updated_at"}

    def test_delete_original_evidence_locates_then_deletes(self):
        original = OriginalEvidence(
            original_evidence_id="oe1", submission_id="s1",
            url="https://example.com/a", created_at=NOW, updated_at=NOW)
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec9", **original.model_dump()}])})
        make_backend(runner).delete_original_evidence("oe1")
        calls = runner.calls_matching(["base", "+record-delete"])
        assert len(calls) == 1
        assert flag_value(calls[0], "--table-id") == "original_evidence"
        # 删除的是飞书生成的 record_id，不是实体 ID
        assert flag_value(calls[0], "--record-id") == "rec9"
        assert "--yes" in calls[0]

    def test_delete_missing_original_evidence_not_found(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([])})
        with pytest.raises(CapsuleError) as excinfo:
            make_backend(runner).delete_original_evidence("ghost")
        assert excinfo.value.code == "NOT_FOUND"

    def test_query_paginates_until_has_more_false(self):
        page = {"count": 0}

        def two_pages(args):
            if "--offset" in args and flag_value(args, "--offset") == "0":
                page["count"] += 1
                return record_list_response(
                    [{"record_id": "rec1", "submission_id": "s1"},
                     {"record_id": "rec2", "submission_id": "s2"}],
                    has_more=True)
            return record_list_response([{"record_id": "rec3", "submission_id": "s3"}])

        runner = FakeRunner(routes={("base", "+record-list"): two_pages})
        records = make_backend(runner)._query("submissions", {}, limit=200)
        assert [r["submission_id"] for r in records] == ["s1", "s2", "s3"]


class TestDerivedEvidence:
    def test_save_creates_docx_under_evidence_branch_and_index(self):
        def node_list(args):
            if "--parent-node-token" in args:
                # 查找新文档对应的知识库节点
                return ok({"nodes": [
                    {"obj_token": "doc-9", "node_token": "node-9"}]})
            return ok({"nodes": [
                {"title": "证据库", "node_token": "node-ev"},
                {"title": "主题", "node_token": "node-topic"}]})

        runner = FakeRunner(routes={
            ("wiki", "+node-list"): node_list,
            ("docs", "+create"): ok({"document": {
                "document_id": "doc-9", "revision_id": 1}}),
        })
        backend = make_backend(runner)
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="证据标题",
            content_hash="h", preview="预览", created_at=NOW)
        backend.save_derived_evidence(evidence)
        # 云文档直接创建在证据库分支节点下
        docx_calls = runner.calls_matching(["docs", "+create"])
        assert flag_value(docx_calls[0], "--parent-token") == "node-ev"
        assert flag_value(docx_calls[0], "--title") == "证据标题"
        assert flag_value(docx_calls[0], "--doc-format") == "markdown"
        # 索引记录保存 token，不保存证据正文
        create_calls = runner.calls_matching(["base", "+record-batch-create"])
        payload = json.loads(flag_value(create_calls[0], "--json"))
        fields = payload["create_records"][0]
        assert fields["docx_token"] == "doc-9"
        assert fields["wiki_node_token"] == "node-9"
        assert "content" not in fields and "text" not in fields

    def test_missing_branch_raises_not_found(self):
        runner = FakeRunner(routes={
            ("wiki", "+node-list"): ok({"nodes": []})})
        with pytest.raises(CapsuleError) as excinfo:
            make_backend(runner).save_derived_evidence(DerivedEvidence(
                evidence_id="e1", submission_id="s1", title="t",
                content_hash="h", created_at=NOW))
        assert excinfo.value.code == "NOT_FOUND"
        assert "bootstrap" in excinfo.value.message

    def test_save_evidence_content_overwrites_with_fetched_revision(self):
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t", content_hash="h",
            created_at=NOW, docx_token="doc-9")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": "旧文", "revision_id": 3}}),
        })
        make_backend(runner).save_evidence_content("e1", "权威证据文字")
        update_entries = runner.entries_matching(["docs", "+update"])
        assert flag_value(update_entries[0][0], "--doc") == "doc-9"
        assert flag_value(update_entries[0][0], "--command") == "overwrite"
        # 正文经 stdin 传递（--content -），转义后按字面存储
        assert flag_value(update_entries[0][0], "--content") == "-"
        assert update_entries[0][1] == "权威证据文字"
        # 用读取时取得的版本提交（乐观并发）
        assert flag_value(update_entries[0][0], "--revision-id") == "3"

    def test_save_evidence_content_escapes_markdown_literals(self):
        # 字面特殊字符必须转义写入，否则被 markdown 语法吞掉
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t", content_hash="h",
            created_at=NOW, docx_token="doc-9")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": "旧文", "revision_id": 3}}),
        })
        text = "价格$100与_下划线_和[链接]\n# 行首标题\n- 列表项\n|表格|线|"
        make_backend(runner).save_evidence_content("e1", text)
        update_entries = runner.entries_matching(["docs", "+update"])
        assert update_entries[0][1] == (
            "价格\\$100与\\_下划线\\_和\\[链接\\]\n"
            "\\# 行首标题\n\\- 列表项\n\\|表格\\|线\\|")

    def test_save_evidence_content_dash_leading_survives_cli_flags(self):
        # 正文以 - 开头（如列表）时经 stdin 传递（--content -），
        # 不会被 lark-cli 误认为 flag（bad flag syntax）
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t", content_hash="h",
            created_at=NOW, docx_token="doc-9")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": "旧文", "revision_id": 3}}),
        })
        make_backend(runner).save_evidence_content("e1", "- 列表项开头")
        update_entries = runner.entries_matching(["docs", "+update"])
        assert flag_value(update_entries[0][0], "--content") == "-"
        assert update_entries[0][1] == "\\- 列表项开头"

    def test_read_evidence_content_round_trips_escaped_literals(self):
        # 证据字面特殊字符：写入转义、导出保持转义、读回反转义还原
        text = "价格$100与_下划线_和[文字]\n# 行首标题\n- 列表项"
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t",
            content_hash=fp.content_hash(text),
            created_at=NOW, docx_token="doc-9")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": "# t\n\n" + escape_markdown(text),
                "revision_id": 2}})})
        assert make_backend(runner).read_evidence_content("e1") == text

    def test_read_evidence_content_normalizes_hard_breaks(self):
        # 飞书把段内换行导出为「  \n」硬换行标记，读回归一化还原
        text = "第一行\n第二行\n\n新段落"
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t",
            content_hash=fp.content_hash(text),
            created_at=NOW, docx_token="doc-9")
        exported = "# t\n\n" + escape_markdown(text).replace("\n", "  \n")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": exported, "revision_id": 2}})})
        assert make_backend(runner).read_evidence_content("e1") == text

    def test_read_evidence_content_reads_docx(self):
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t",
            content_hash=fp.content_hash("权威文字"),
            created_at=NOW, docx_token="doc-9")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            # 飞书导出把文档标题作为正文前的一级标题块
            ("docs", "+fetch"): ok({"document": {
                "content": "# t\n\n权威文字", "revision_id": 2}})})
        assert make_backend(runner).read_evidence_content("e1") == "权威文字"

    def test_read_evidence_content_strips_title_tag_form(self):
        # 正文含一级标题时，飞书改用 <title> 标签形式导出标题块
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t",
            content_hash=fp.content_hash("# 章节标题\n\n权威文字"),
            created_at=NOW, docx_token="doc-9")
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **evidence.model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": "<title>t</title>\n\n# 章节标题\n\n权威文字",
                "revision_id": 2}})})
        assert make_backend(runner).read_evidence_content(
            "e1") == "# 章节标题\n\n权威文字"

    def test_mark_deleted_removes_doc_and_updates_status(self):
        evidence = DerivedEvidence(
            evidence_id="e1", submission_id="s1", title="t", content_hash="h",
            created_at=NOW, docx_token="doc-9")

        def record_list(args):
            state = {"status": "active"}

            def inner(args_inner):
                data = dict(evidence.model_dump())
                data["status"] = state["status"]
                return record_list_response([
                    {"record_id": "rec1", **data}])

            state["list"] = inner
            return inner(args)

        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list})
        make_backend(runner).mark_evidence_deleted("e1")
        delete_calls = runner.calls_matching(["drive", "+delete"])
        assert flag_value(delete_calls[0], "--file-token") == "doc-9"
        assert flag_value(delete_calls[0], "--type") == "docx"
        assert "--yes" in delete_calls[0]
        update_calls = runner.calls_matching(["base", "+record-batch-update"])
        payload = json.loads(flag_value(update_calls[0], "--json"))
        assert payload["update_records"] == {"rec1": {"status": "deleted"}}


class TestKnowledgePages:
    def make_page(self, **overrides) -> KnowledgePageRecord:
        data = {"page_id": "p1", "page_type": "topic", "title": "页面",
                "revision_id": "rev-1", "content_hash": "h0",
                "created_at": NOW, "updated_at": NOW}
        data.update(overrides)
        return KnowledgePageRecord(**data)

    def test_save_page_registers_correct_branch(self):
        def node_list(args):
            if "--parent-node-token" in args:
                return ok({"nodes": [
                    {"obj_token": "doc-p", "node_token": "node-p"},
                    {"obj_token": "doc-c", "node_token": "node-c"}]})
            return ok({"nodes": [
                {"title": "主题", "node_token": "node-topic"},
                {"title": "概念", "node_token": "node-concept"},
                {"title": "证据库", "node_token": "node-ev"}]})

        def docs_create(args):
            return ok({"document": {
                "document_id": "doc-p" if flag_value(
                    args, "--title") == "页面" else "doc-c",
                "revision_id": 9}})

        runner = FakeRunner(routes={
            ("wiki", "+node-list"): node_list,
            ("docs", "+create"): docs_create})
        backend = make_backend(runner)
        backend.save_page(self.make_page())
        backend.save_page(self.make_page(
            page_id="p2", page_type="concept", title="概念页"))
        docx_calls = runner.calls_matching(["docs", "+create"])
        assert flag_value(docx_calls[0], "--parent-token") == "node-topic"
        assert flag_value(docx_calls[1], "--parent-token") == "node-concept"
        # 页记录保存引擎传入的页面版本号（rev-N 体系）；
        # 云文档创建返回的飞书 revision 是另一套体系，不写入记录
        create_calls = runner.calls_matching(["base", "+record-batch-create"])
        payloads = [json.loads(flag_value(c, "--json"))["create_records"][0]
                    for c in create_calls]
        assert payloads[0]["revision_id"] == "rev-1"

    def test_update_page_content_happy_path(self):
        state = {"fields": self.make_page(docx_token="doc-p").model_dump()}

        def record_list(args):
            return record_list_response(
                [{"record_id": "rec1", **dict(state["fields"])}])

        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list,
            ("docs", "+fetch"): ok({"document": {
                "content": "旧文", "revision_id": 5}}),
            ("docs", "+update"): ok({"document": {"revision_id": 6}})})

        def batch_update(args):
            payload = json.loads(flag_value(args, "--json"))
            for fields in payload["update_records"].values():
                state["fields"].update(fields)
            return ok({})

        runner.routes[("base", "+record-batch-update")] = batch_update
        updated = make_backend(runner).update_page_content(
            "p1", "rev-1", "# 新内容", "h-new")
        assert updated.revision_id == "rev-2"
        assert updated.content_hash == "h-new"
        update_entries = runner.entries_matching(["docs", "+update"])
        # 云文档写入使用读取时取得的飞书 revision（乐观并发）
        assert flag_value(update_entries[0][0], "--revision-id") == "5"
        # 页面正文是 markdown（不转义），经 stdin 传递
        assert update_entries[0][1] == "# 新内容"

    def test_update_page_content_stale_revision_rejected(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1",
                 **self.make_page(revision_id="rev-9").model_dump()}])})
        with pytest.raises(CapsuleError) as excinfo:
            make_backend(runner).update_page_content("p1", "rev-1", "# x", "h")
        assert excinfo.value.code == "STALE_VERSION"

    def test_read_page_content_strips_doc_title(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1",
                 **self.make_page(
                     docx_token="doc-p",
                     content_hash=fp.content_hash("# 页面\n\n## 定义")).model_dump()}]),
            # 飞书导出把文档标题作为正文前的一级标题块；
            # 正文首行与文档同名时只剥离第一个标题块
            ("docs", "+fetch"): ok({"document": {
                "content": "# 页面\n\n# 页面\n\n## 定义", "revision_id": 2}})})
        assert make_backend(runner).read_page_content("p1") == "# 页面\n\n## 定义"

    def test_read_page_content_keeps_body_h1_without_title_block(self):
        # 正文以一级标题开头时，飞书导出不附加标题块，原样即正文
        body = "# 页面\n\n## 定义"
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1",
                 **self.make_page(
                     docx_token="doc-p",
                     content_hash=fp.content_hash(body)).model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": body, "revision_id": 2}})})
        assert make_backend(runner).read_page_content("p1") == body

    def test_read_page_content_reconstructs_promoted_title_h1(self):
        # 正文首个一级标题被提升为文档标题后，导出改用 <title> 形式
        # 且正文缺少该标题行；读回时把标题行拼回正文开头还原写入内容
        body = "# 页面\n\n## A 节\n\n内容。\n\n# B 节\n\n内容二。"
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1",
                 **self.make_page(
                     docx_token="doc-p",
                     content_hash=fp.content_hash(body)).model_dump()}]),
            ("docs", "+fetch"): ok({"document": {
                "content": "<title>页面</title>\n\n## A 节\n\n内容。\n\n# B 节\n\n内容二。",
                "revision_id": 2}})})
        assert make_backend(runner).read_page_content("p1") == body

    def test_update_page_content_hash_mismatch_detected(self):
        state = {"fields": self.make_page(docx_token="doc-p").model_dump()}

        def record_list(args):
            return record_list_response(
                [{"record_id": "rec1", **dict(state["fields"])}])

        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list,
            ("docs", "+fetch"): ok({"document": {
                "content": "旧文", "revision_id": 5}}),
            ("docs", "+update"): ok({"document": {"revision_id": 6}})})

        def batch_update(args):  # 模拟写后哈希不一致（手工修改）
            state["fields"].update({"revision_id": "rev-2",
                                    "content_hash": "other"})
            return ok({})

        runner.routes[("base", "+record-batch-update")] = batch_update
        with pytest.raises(CapsuleError) as excinfo:
            make_backend(runner).update_page_content(
                "p1", "rev-1", "# x", "h-new")
        assert excinfo.value.code == "PARTIAL_WRITE"
        assert "写后核对" in excinfo.value.message


class TestSerializationRoundTrip:
    def test_json_bool_int_fields_round_trip(self):
        item = ReviewItemRecord(
            review_item_id="ri1", review_type="knowledge_conflict",
            status="open", trigger_code="CONFLICT_DETECTED",
            detected_stage="merge", reason="冲突",
            evidence_refs=["e1", "e2"], attempted_actions=["a1"],
            allowed_decisions=[{"code": "apply"}],
            recommendation={"severity": "high"},
            version=3, rule_gap_flag=True, created_at=NOW)
        backend = make_backend(FakeRunner())
        fields = backend._to_base_fields("review_items", item)
        assert fields["evidence_refs_json"] == json.dumps(["e1", "e2"])
        assert fields["version"] == "3"
        assert fields["rule_gap_flag"] == "true"
        assert fields["recommendation_json"] == json.dumps({"severity": "high"})
        restored = backend._from_base_fields("review_items", fields)
        assert restored.evidence_refs == ["e1", "e2"]
        assert restored.version == 3
        assert restored.rule_gap_flag is True
        assert restored.recommendation == {"severity": "high"}
        assert restored.allowed_decisions == [{"code": "apply"}]

    def test_empty_values_fall_back_to_model_defaults(self):
        backend = make_backend(FakeRunner())
        restored = backend._from_base_fields(
            "review_items", {"review_item_id": "ri1",
                             "review_type": "processing_interruption",
                             "trigger_code": "T", "detected_stage": "s",
                             "reason": "r", "created_at": NOW})
        assert restored.version == 1
        assert restored.rule_gap_flag is False
        assert restored.evidence_refs == []
        assert restored.recommendation == {}

    def test_rule_gap_flag_filter_serialized_as_text(self):
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([])})
        make_backend(runner).list_review_items(rule_gap_flag=True)
        call = runner.calls_matching(["base", "+record-list"])[0]
        filter_json = json.loads(flag_value(call, "--filter-json"))
        assert filter_json["conditions"] == [["rule_gap_flag", "==", "true"]]


class TestAppendOnlyTables:
    def test_review_decision_is_created_not_updated(self):
        runner = FakeRunner()
        backend = make_backend(runner)
        backend.append_review_decision(ReviewDecisionRecord(
            review_decision_id="d1", review_item_id="ri1",
            decision_code="terminate", review_item_version=1,
            execution_status="succeeded", created_at=NOW, executed_at=NOW))
        assert len(runner.calls_matching(["base", "+record-batch-create"])) == 1
        assert not runner.calls_matching(["base", "+record-batch-update"])

    def test_review_item_update_only_for_status_projection(self):
        item = ReviewItemRecord(
            review_item_id="ri1", review_type="processing_interruption",
            trigger_code="EXTRACTION_INCOMPLETE", detected_stage="evidence_fixing",
            reason="r", created_at=NOW)
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "rec1", **item.model_dump()}])})
        backend = make_backend(runner)
        backend.save_review_item(item)
        assert len(runner.calls_matching(["base", "+record-batch-create"])) == 1
        item.status = "resolved"
        backend.update_review_item(item)
        update_calls = runner.calls_matching(["base", "+record-batch-update"])
        assert len(update_calls) == 1
        assert flag_value(update_calls[0], "--table-id") == "review_items"
        payload = json.loads(flag_value(update_calls[0], "--json"))
        assert "rec1" in payload["update_records"]

    def test_understanding_result_saved_with_json_fields(self):
        runner = FakeRunner()
        backend = make_backend(runner)
        backend.save_understanding(UnderstandingResultRecord(
            understanding_id="u1", submission_id="s1", evidence_id="e1",
            schema_version="1.0", validation_status="valid",
            topics=[{"name": "主题A"}], created_at=NOW))
        calls = runner.calls_matching(["base", "+record-batch-create"])
        payload = json.loads(flag_value(calls[0], "--json"))
        fields = payload["create_records"][0]
        assert flag_value(calls[0], "--table-id") == "understanding_results"
        assert fields["validation_status"] == "valid"
        assert json.loads(fields["topics_json"]) == [{"name": "主题A"}]


class TestReadAfterWriteCache:
    """飞书 Base 过滤查询对刚写入记录有秒级索引延迟（最终一致）。

    适配器登记创建响应返回的 record_id_list 进程内写入缓存，
    同一进程写后立即回读不依赖过滤索引。
    """

    @staticmethod
    def _submission() -> Submission:
        return Submission(
            submission_id="s1", input_kind="text", status="completed",
            content_fingerprint="fp", created_at=NOW, updated_at=NOW)

    def test_create_then_read_when_filter_index_delayed(self):
        """写后立即回读：过滤查询返回空（索引延迟）也能读到刚写入的实体。"""
        runner = FakeRunner(routes={
            # 模拟索引延迟：刚写入的记录过滤查询不可见
            ("base", "+record-list"): record_list_response([])})
        runner.default = ok({"record_id_list": ["recNew1"]})
        backend = make_backend(runner)
        backend.save_submission(self._submission())
        got = backend.get_submission("s1")
        assert got is not None and got.submission_id == "s1"
        assert got.status == "completed"

    def test_update_uses_cached_record_id_without_filter_query(self):
        """更新刚写入的记录：直接用缓存 record_id，不再发过滤查询。"""
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([])})
        runner.default = ok({"record_id_list": ["recNew2"]})
        backend = make_backend(runner)
        item = ReviewItemRecord(
            review_item_id="ri1", review_type="processing_interruption",
            trigger_code="EXTRACTION_INCOMPLETE", detected_stage="evidence_fixing",
            reason="r", created_at=NOW)
        backend.save_review_item(item)
        item.status = "resolved"
        backend.update_review_item(item)
        update_calls = runner.calls_matching(["base", "+record-batch-update"])
        assert len(update_calls) == 1
        assert flag_value(update_calls[0], "--table-id") == "review_items"
        payload = json.loads(flag_value(update_calls[0], "--json"))
        assert "recNew2" in payload["update_records"]
        # 更新走缓存定位，全程无过滤查询
        assert not runner.calls_matching(["base", "+record-list"])

    def test_query_merges_cached_records_and_dedups(self):
        """按二级字段（submission_id）查询刚写入的记录也走缓存并入结果。"""
        original = OriginalEvidence(
            original_evidence_id="oe1", submission_id="s1",
            url="https://example.com/a", created_at=NOW, updated_at=NOW)
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([])})
        runner.default = ok({"record_id_list": ["recNew3"]})
        backend = make_backend(runner)
        backend.save_original_evidence(original)
        got = backend.get_original_evidence_by_submission("s1")
        assert got is not None and got.original_evidence_id == "oe1"
        assert got.url == "https://example.com/a"

    def test_delete_removes_cached_record(self):
        """删除刚写入的记录后，缓存不再返回该记录。"""
        original = OriginalEvidence(
            original_evidence_id="oe1", submission_id="s1",
            url="https://example.com/a", created_at=NOW, updated_at=NOW)
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([])})
        runner.default = ok({"record_id_list": ["recNew4"]})
        backend = make_backend(runner)
        backend.save_original_evidence(original)
        delete_calls = runner.calls_matching(["base", "+record-delete"])
        backend.delete_original_evidence("oe1")
        assert len(delete_calls) + 1 == len(
            runner.calls_matching(["base", "+record-delete"]))
        assert backend.get_original_evidence("oe1") is None

    def test_create_without_record_id_list_falls_back_to_filter(self):
        """创建响应无 record_id_list（旧版 CLI）时不缓存，回退过滤查询。"""
        runner = FakeRunner(routes={
            ("base", "+record-list"): record_list_response([
                {"record_id": "recOld",
                 **{"submission_id": "s1", "input_kind": "text",
                    "status": "completed", "content_fingerprint": "fp",
                    "created_at": NOW, "updated_at": NOW}}])})
        backend = make_backend(runner)
        backend.save_submission(self._submission())
        got = backend.get_submission("s1")
        assert got is not None and got.status == "completed"
        assert runner.calls_matching(["base", "+record-list"])
